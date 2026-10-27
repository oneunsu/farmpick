import json
import logging
from datetime import date, timedelta
from django.utils import timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from accounts.models import Farm
from weather.models import WeatherForecast
from forecaster.harvest_outlook import farm_outlook
from forecaster.selected_forecast import MODEL_ID
from .models import CropSchedule, get_schedule_template, TASK_COLORS
from .harvest_status import harvest_status

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────

def _get_farm(request, source='GET'):
    data = request.GET if source == 'GET' else request.POST
    farm_id = data.get('farm')
    if farm_id:
        return get_object_or_404(Farm, pk=farm_id, user=request.user)
    return Farm.objects.filter(user=request.user).first()


def _calendar_redirect_for(farm):
    if not farm:
        return redirect('calendar_app:index')
    return redirect(f'{reverse("calendar_app:index")}?farm={farm.pk}')


# ─────────────────────────────────────────
# 1. 메인 캘린더 페이지
# ─────────────────────────────────────────

def _page_context(request):
    farms = Farm.objects.filter(user=request.user).order_by('-created_at')
    farm = _get_farm(request, source='GET')

    today = timezone.localdate()

    dashboard = {}
    if farm:
        # 2. 이번 주 날씨 (7일치)
        from weather.models import WeatherForecast
        week_forecasts = WeatherForecast.objects.filter(
            farm=farm,
            forecast_date__gte=today,
            forecast_date__lte=today + timedelta(days=6),
        ).order_by('forecast_date')

        # 날씨 아이콘 매핑 (sky_condition: 1=맑음, 2=구름조금, 3=구름많음, 4=흐림)
        SKY_ICON = {1: '☀️', 2: '🌤', 3: '⛅', 4: '☁️', None: '❓'}
        RAIN_ICON = '🌧'

        # 단기·중기 범위가 겹쳐도 날짜당 한 카드만 표시한다. 단기예보를 우선한다.
        forecasts_by_day = {}
        for wf in week_forecasts:
            if wf.forecast_date not in forecasts_by_day or wf.source == 'short':
                forecasts_by_day[wf.forecast_date] = wf
        week_weather = []
        for wf in forecasts_by_day.values():
            icon = RAIN_ICON if (wf.rain_prob or 0) >= 60 else SKY_ICON.get(wf.sky_condition, '❓')
            week_weather.append({
                'date': wf.forecast_date,
                'icon': icon,
                'rain_prob': wf.rain_prob or 0,
                'temp_max': wf.temp_max,
                'temp_min': wf.temp_min,
            })

        # 3. 이번 주 할 일 (7일 이내 미완료 일정)
        week_schedules = CropSchedule.objects.filter(
            farm=farm,
            scheduled_at__gte=today,
            scheduled_at__lte=today + timedelta(days=6),
            is_done=False,
        ).order_by('scheduled_at')

        # 현재 진행 중인 기간을 다음 수확 시작일보다 우선한다.
        status = harvest_status(list(CropSchedule.objects.filter(farm=farm, task_type='harvest')), today)
        next_harvest = status['start']
        harvest_dday = status['dday']
        harvest_price = None
        outlook = {'available': False, 'reason': status['price_reason']}
        if status['forecast_date']:
            outlook = farm_outlook(farm, status['forecast_date'], as_of=today)
            harvest_price = outlook.get('base') if outlook.get('available') else None

        dashboard = {
            'week_weather': week_weather,
            'week_schedules': week_schedules,
            'harvest_dday': harvest_dday,
            'harvest_price': harvest_price,
            'next_harvest': next_harvest,
            'outlook': outlook,
            'harvest_status': status,
        }

    return {
        'farm': farm,
        'farms': farms,
        'today': today,
        'task_colors': TASK_COLORS,
        'dashboard': dashboard,
    }


@login_required
def index(request):
    return render(request, 'calendar_app/index.html', _page_context(request))


@login_required
def summary(request):
    from forecaster.models import MarketSnapshot, RevenuePlan
    context = _page_context(request)
    farm = context['farm']
    if farm:
        context['recent_plans'] = RevenuePlan.objects.filter(farm=farm).order_by('-updated_at')[:3]
        context['market_snapshot'] = MarketSnapshot.objects.filter(
            crop_type=farm.crop_type, collected_at__lte=timezone.now(),
        ).order_by('-collected_at').first()
    response = render(request, 'calendar_app/summary.html', context)
    response['Cache-Control'] = 'private, no-store'
    return response


# ─────────────────────────────────────────
# 2. FullCalendar 이벤트 API
# ─────────────────────────────────────────

@login_required
def schedule_api(request):
    """
    GET /calendar/api/schedule/?farm=1&start=2026-06-01&end=2026-07-01
    → FullCalendar Events JSON 반환
    """
    farm = _get_farm(request, source='GET')
    if not farm:
        return JsonResponse([], safe=False)

    start_str = request.GET.get('start', '')
    end_str   = request.GET.get('end',   '')

    try:
        start_date = date.fromisoformat(start_str[:10])
        end_date   = date.fromisoformat(end_str[:10])
    except (ValueError, TypeError):
        today      = timezone.localdate()
        start_date = today.replace(day=1)
        end_date   = (start_date + timedelta(days=40)).replace(day=1)

    events = []

    schedules = list(CropSchedule.objects.filter(farm=farm, scheduled_at__gte=start_date, scheduled_at__lt=end_date))
    price_map: dict[date, float] = {}
    today = timezone.localdate()
    for target in sorted({s.scheduled_at for s in schedules if s.task_type == 'harvest' and today < s.scheduled_at <= today+timedelta(days=365)}):
        outlook = farm_outlook(farm, target, as_of=today)
        if outlook.get('available'):
            price_map[target] = outlook['base']
    # 기존 TimesFM 저장값은 보존하지만 사용자 달력에는 선정 모델만 표시한다.
    for target, price in price_map.items():
        events.append({
            'id':        f'price_{target.isoformat()}',
            'title':     f'예상 참고가 {price:,.0f}원/kg',
            'start':     target.isoformat(),
            'allDay':    True,
            'type':      'price',
            'price':     price,
            'method':    'tree',
            'model_id':  MODEL_ID,
            'color':     '#27AE60',
            'textColor': '#fff',
            'editable':  False,   # 드래그 비활성화
            'startEditable': False,
            'durationEditable': False,
        })

    # ── 영농 일정 이벤트 ──────────────────────
    for s in schedules:
        sched_date = s.scheduled_at.date() if hasattr(s.scheduled_at, 'date') else s.scheduled_at

        # 수확 일정이면 예상 가격 타이틀에 추가
        price_label   = ''
        harvest_price = None
        if s.task_type == 'harvest' and sched_date in price_map:
            harvest_price = price_map[sched_date]
            price_label   = f' | 예상 참고가 {harvest_price:,.0f}원/kg'

        events.append({
            'id':            f'schedule_{s.pk}',
            'title':         ('✅ ' if s.is_done else '') + s.task_name + price_label,
            'start':         s.scheduled_at.isoformat(),
            'allDay':        True,
            'type':          'schedule',
            'task_type':     s.task_type,
            'is_done':       s.is_done,
            'note':          s.note,
            'harvest_price': harvest_price,   # 모달용
            'color':         TASK_COLORS.get(s.task_type, '#95A5A6'),
            'textColor':     '#fff',
            'opacity':       '0.5' if s.is_done else '1.0',
        })

    response = JsonResponse(events, safe=False)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response['Pragma'] = 'no-cache'
    return response


# ─────────────────────────────────────────
# 3. 일정 자동 생성
# ─────────────────────────────────────────

@login_required
@require_POST
def generate_schedule(request):
    farm = _get_farm(request, source='POST')
    if not farm:
        messages.error(request, '농장이 없습니다.')
        return redirect('calendar_app:index')

    if not farm.planting_date:
        messages.warning(request, '정식일이 설정되지 않았습니다. 농장 정보를 수정해 주세요.')
        return _calendar_redirect_for(farm)

    # 재배방식 + 작물에 맞는 템플릿 선택
    template = get_schedule_template(farm.crop_type)
    if not template:
        messages.warning(
            request,
            f'{farm.get_crop_type_display()} 작물의 일정 템플릿이 없습니다.'
        )
        return _calendar_redirect_for(farm)

    created = 0
    # 기존·수동 일정은 보존하며 동일한 자동 일정은 다시 추가하지 않는다.
    with transaction.atomic():
        for t in template:
            _, was_created = CropSchedule.objects.get_or_create(
                farm=farm,
                task_type=t['task_type'],
                task_name=t['task_name'],
                scheduled_at=farm.planting_date + timedelta(days=t['offset_days']),
            )
            created += int(was_created)

    messages.success(
        request,
        f'[{farm.name}] 정식·수확 일정 {created}개를 추가했습니다. 기존 일정은 유지됩니다.'
    )
    return _calendar_redirect_for(farm)


# ─────────────────────────────────────────
# 4. 일정 완료 토글 (Ajax)
# ─────────────────────────────────────────

@login_required
@require_POST
def toggle_done(request):
    schedule_id = request.POST.get('schedule_id')
    schedule    = get_object_or_404(CropSchedule, pk=schedule_id, farm__user=request.user)
    schedule.is_done = not schedule.is_done
    schedule.save(update_fields=['is_done'])
    return JsonResponse({'is_done': schedule.is_done, 'id': schedule.pk})


# ─────────────────────────────────────────
# 5. 일정 추가
# ─────────────────────────────────────────

@login_required
@require_POST
def add_schedule(request):
    farm = _get_farm(request, source='POST')
    if not farm:
        return JsonResponse({'error': '농장이 없습니다.'}, status=400)

    task_type = request.POST.get('task_type', 'inspection')
    task_name = request.POST.get('task_name', '').strip()
    scheduled_at = request.POST.get('scheduled_at', '')
    note = request.POST.get('note', '').strip()

    if not task_name or not scheduled_at:
        return JsonResponse({'error': '일정 이름과 날짜를 입력해 주세요.'}, status=400)

    try:
        sched_date = date.fromisoformat(scheduled_at)
    except ValueError:
        return JsonResponse({'error': '날짜 형식이 올바르지 않습니다.'}, status=400)

    schedule = CropSchedule.objects.create(
        farm=farm,
        task_type=task_type,
        task_name=task_name,
        scheduled_at=sched_date,
        note=note,
    )
    return JsonResponse({
        'ok': True,
        'id': schedule.pk,
        'task_name': schedule.task_name,
        'task_type': schedule.task_type,
        'scheduled_at': schedule.scheduled_at.isoformat(),
    })


# ─────────────────────────────────────────
# 6. 일정 삭제
# ─────────────────────────────────────────

@login_required
@require_POST
def delete_schedule(request):
    schedule_id = request.POST.get('schedule_id')
    schedule    = get_object_or_404(CropSchedule, pk=schedule_id, farm__user=request.user)
    name        = schedule.task_name
    schedule.delete()
    return JsonResponse({'ok': True, 'task_name': name})

# ─────────────────────────────────────────
# 6. 일정 수정
# ─────────────────────────────────────────

@login_required
@require_POST
def update_schedule(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = request.POST

    schedule_id = data.get('schedule_id')
    scheduled_at = data.get('scheduled_at')
    task_name = data.get('task_name')
    task_type = data.get('task_type')
    note = data.get('note')

    schedule = get_object_or_404(CropSchedule, pk=schedule_id, farm__user=request.user)

    if scheduled_at:
        try:
            schedule.scheduled_at = date.fromisoformat(scheduled_at)
        except ValueError:
            return JsonResponse({'error': '날짜 형식이 올바르지 않습니다.'}, status=400)

    if task_name is not None:
        schedule.task_name = task_name.strip()
    if task_type is not None:
        schedule.task_type = task_type
    if note is not None:
        schedule.note = note.strip()

    schedule.save()
    return JsonResponse({
        'ok': True,
        'id': schedule.pk,
        'task_name': schedule.task_name,
        'task_type': schedule.task_type,
        'scheduled_at': schedule.scheduled_at.isoformat(),
    })

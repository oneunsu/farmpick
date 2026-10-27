from datetime import date
from uuid import UUID
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from accounts.models import Farm
from calendar_app.models import CropSchedule
from .models import RevenuePlan, MarketSettlement, ForecastRun
from .revenue import calculate_receipts
from .revenue_forms import RevenuePlanForm, ChannelFormSet
from .harvest_outlook import farm_outlook
from .forecast_records import make_payload, save_payload, stored_outlook
from .channel_comparison import ComparisonForm, compare_channels


def reference_evidence(row):
    return {
        'id': row.pk, 'date': row.settlement_date.isoformat(), 'market': row.market_name,
        'variety': row.variety_name, 'dimensions': row.dimensions,
        'original_average': str(row.average_price), 'unit_weight': str(row.unit_weight),
        'snapshot_checksum': row.snapshot.checksum, 'source': row.snapshot.source,
    }


def editor_context(farm, form, formset, plan=None):
    return {
        'farm': farm, 'form': form, 'formset': formset, 'plan': plan,
        'harvest_dates': {str(s.pk): s.scheduled_at.isoformat() for s in CropSchedule.objects.filter(farm=farm, task_type='harvest')},
        'version': plan.updated_at.isoformat() if plan and plan.pk else '',
        'linked_forecast': plan.forecast_run if plan and plan.forecast_run_id else None,
    }


def render_editor(request, farm, form, formset, plan, status=200):
    response = render(request, 'forecaster/revenue_form.html', editor_context(farm, form, formset, plan), status=status)
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def plan_list(request):
    farms = Farm.objects.filter(user=request.user).order_by('-created_at')
    farm = get_object_or_404(farms, pk=request.GET['farm']) if request.GET.get('farm') else farms.first()
    plans = RevenuePlan.objects.filter(farm__user=request.user).select_related('farm').prefetch_related('channels').order_by('-updated_at')
    if farm:
        plans = plans.filter(farm=farm)
    response = render(request, 'forecaster/revenue_list.html', {'farms': farms, 'farm': farm, 'plans': plans})
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def plan_editor(request, pk=None):
    plan = get_object_or_404(RevenuePlan.objects.select_related('farm'), pk=pk, farm__user=request.user) if pk else None
    farm_id = request.POST.get('farm') if request.method == 'POST' else request.GET.get('farm')
    farm = plan.farm if plan else (get_object_or_404(Farm, pk=farm_id, user=request.user) if farm_id
                                  else Farm.objects.filter(user=request.user).first())
    if farm is None:
        messages.warning(request, '농장을 등록한 뒤 수취액 계산을 시작해 주세요.')
        return redirect('farm_list')
    if not plan:
        plan = RevenuePlan(farm=farm)
    supplied_run = request.POST.get('forecast_run') if request.method == 'POST' else request.GET.get('forecast_run')
    selected_run = None
    if supplied_run:
        try:
            run_id = UUID(supplied_run)
        except (ValueError, TypeError):
            raise Http404
        selected_run = get_object_or_404(ForecastRun, pk=run_id, farm=farm, farm__user=request.user)
        if request.method == 'GET':
            plan.forecast_run = selected_run
    initial = {}
    channel_initial = None
    if request.method == 'GET' and not plan.pk:
        schedules = CropSchedule.objects.filter(farm=farm, task_type='harvest').order_by('scheduled_at')
        schedule = (get_object_or_404(schedules, pk=request.GET['harvest']) if request.GET.get('harvest') else
                    schedules.filter(scheduled_at__gte=timezone.localdate()).first())
        initial = {'title': f'{farm.get_crop_type_display()} 출하 비교', 'harvest_date': schedule.scheduled_at if schedule else timezone.localdate(),
                   'harvest_schedule': schedule, 'saleable_percent': 100}
        if request.GET.get('target'):
            try:
                initial['harvest_date'] = date.fromisoformat(request.GET['target'])
                initial['harvest_schedule'] = None
            except ValueError:
                form_error = '수확일 형식이 올바르지 않습니다.'
                messages.error(request, form_error)
        if selected_run:
            initial['harvest_date'] = selected_run.target_date
            initial['harvest_schedule'] = None
        if request.GET.get('reference'):
            row = get_object_or_404(MarketSettlement.objects.select_related('snapshot'), pk=request.GET['reference'], snapshot__crop_type=farm.crop_type)
            channel_initial = [{'name': row.market_name, 'channel_type': 'wholesale', 'price_source': 'market_reference',
                                'reference_settlement': row.pk, 'price_note': f'{row.settlement_date} {row.market_name} {row.variety_name} 정산자료 참고 · kg 단가는 별도 입력'}]
    form = RevenuePlanForm(request.POST if request.method == 'POST' else None, instance=plan, farm=farm, initial=initial)
    formset = ChannelFormSet(request.POST if request.method == 'POST' else None, instance=plan, prefix='channels',
                            form_kwargs={'farm': farm}, initial=channel_initial)
    if request.method == 'POST':
        form_ok, channels_ok = form.is_valid(), formset.is_valid()
        if form_ok and channels_ok:
            with transaction.atomic():
                current = None
                # 다른 탭에서 수정한 입력을 조용히 덮어쓰지 않는다.
                if plan.pk:
                    current = RevenuePlan.objects.select_for_update().get(pk=plan.pk, farm__user=request.user)
                    if request.POST.get('version') != current.updated_at.isoformat():
                        form.add_error(None, '다른 화면에서 수정된 계산입니다. 새로고침한 뒤 다시 입력해 주세요.')
                        return render_editor(request, farm, form, formset, plan, status=409)
                saved = form.save(commit=False)
                saved.farm = farm
                linked = selected_run
                if linked and linked.target_date != saved.harvest_date:
                    if current and linked.pk == current.forecast_run_id:
                        linked = None
                    else:
                        form.add_error('harvest_date', '연결한 전망의 대상일과 다릅니다. 같은 수확일을 선택해주세요.')
                        return render_editor(request, farm, form, formset, plan, status=400)
                if linked is None and current and current.harvest_date == saved.harvest_date:
                    linked = current.forecast_run
                if linked and linked.crop_type != farm.crop_type and not (current and linked.pk == current.forecast_run_id):
                    form.add_error(None, '연결한 전망의 작물이 현재 농장과 다릅니다.')
                    return render_editor(request, farm, form, formset, plan, status=400)
                if linked is None:
                    point = farm_outlook(farm, saved.harvest_date)
                    try:
                        linked = save_payload(farm, make_payload(farm, saved.harvest_date, point))
                    except (ValueError, OSError):
                        form.add_error(None, '전망 입력이 변경됐습니다. 새로고침한 뒤 다시 저장해주세요.')
                        return render_editor(request, farm, form, formset, plan, status=409)
                saved.forecast_run = linked
                saved.save()
                formset.instance = saved
                for channel in formset.save(commit=False):
                    row = channel.reference_settlement
                    if row:
                        if channel.reference_snapshot.get('id') != row.pk:
                            channel.reference_snapshot = reference_evidence(row)
                    else:
                        channel.reference_snapshot = {}
                    channel.plan = saved
                    channel.save()
                for channel in formset.deleted_objects:
                    channel.delete()
                # 검증·저장이 하나의 트랜잭션 안에서 끝나야 이전 성공 입력이 보존된다.
                calculate_receipts(saved, saved.channels.all())
            messages.success(request, '입력한 가정으로 수취액을 계산하고 저장했습니다.')
            return redirect('forecaster:revenue_detail', pk=saved.pk)
        return render_editor(request, farm, form, formset, plan, status=400)
    return render_editor(request, farm, form, formset, plan)


@login_required
@require_GET
def plan_detail(request, pk):
    plan = get_object_or_404(RevenuePlan.objects.select_related('farm', 'harvest_schedule', 'forecast_run'), pk=pk, farm__user=request.user)
    channels = list(plan.channels.all())
    comparison = calculate_receipts(plan, channels)
    defaults = {'baseline': str(channels[0].pk) if channels else '', 'price_shift': '10', 'transport_shift': '20'}
    form = ComparisonForm({key: request.GET.get(key, value) for key, value in defaults.items()}, channels=channels)
    valid = form.is_valid()
    if valid:
        comparison = compare_channels(plan, channels, **form.cleaned_data)
    response = render(request, 'forecaster/revenue_detail.html', {'plan': plan, 'farm': plan.farm, 'comparison': comparison,
                      'comparison_form': form, 'outlook': stored_outlook(plan)}, status=200 if valid or not channels else 400)
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def plan_api(request, pk):
    plan = get_object_or_404(RevenuePlan.objects.select_related('forecast_run'), pk=pk, farm__user=request.user)
    channels = list(plan.channels.all())
    defaults = {'baseline': str(channels[0].pk) if channels else '', 'price_shift': '10', 'transport_shift': '20'}
    form = ComparisonForm({key: request.GET.get(key, value) for key, value in defaults.items()}, channels=channels)
    if channels and not form.is_valid():
        return JsonResponse({'error': '비교 조건을 확인해주세요.', 'errors': form.errors.get_json_data()}, status=400)
    result = compare_channels(plan, channels, **form.cleaned_data) if channels else calculate_receipts(plan, channels)
    rows = []
    for row in result['rows']:
        channel = row['channel']
        rows.append({**{k: v for k, v in row.items() if k != 'channel'}, 'id': channel.pk, 'name': channel.name,
                     'price_per_kg': channel.price_per_kg, 'price_source': channel.price_source, 'price_note': channel.price_note})
    response = JsonResponse({'plan': plan.pk, 'farm': plan.farm_id, 'harvest_date': plan.harvest_date.isoformat(),
                             'calculation_version': result['version'], 'saleable_kg': result['saleable_kg'],
                             'production_cost': result['production_cost'], 'rows': rows,
                             'outlook': stored_outlook(plan),
                             'forecast_run': str(plan.forecast_run_id) if plan.forecast_run_id else None})
    response['Cache-Control'] = 'private, no-store'
    return response

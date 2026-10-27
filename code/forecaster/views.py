"""
forecaster/views.py
- 가격 데이터 수집 / 예측 실행 뷰
"""
import logging
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.utils import timezone

from accounts.models import Farm
from datetime import timedelta

from .kamis_api import fetch_and_save_price
from .models import PriceData
from .selected_forecast import selected_curve, MODEL_ID
from calendar_app.models import CropSchedule

logger = logging.getLogger(__name__)


@login_required
@require_POST
def update_price_data(request, farm_id: int):
    """
    KAMIS에서 과거 가격 수집 후 DB 저장.
    POST /forecaster/update/<farm_id>/
    """
    farm = get_object_or_404(Farm, id=farm_id, user=request.user)

    try:
        result = fetch_and_save_price(farm)
        if result['status'] == 'no_data':
            messages.warning(request, '조회 기간에 KAMIS 공시 가격이 없습니다. 기존 가격은 유지됩니다.')
        else:
            messages.success(
                request,
                f"가격 데이터 업데이트 완료 "
                f"(신규 {result['created']}개, 갱신 {result['updated']}개)"
            )
    except Exception as e:
        logger.error(f"가격 수집 오류 farm_id={farm_id}: {e}")
        messages.error(request, f"가격 데이터 수집 중 오류가 발생했습니다: {e}")

    return redirect('forecaster:harvest_outlook', farm_id=farm.pk)


@login_required
@require_POST
def run_price_forecast(request, farm_id: int):
    """
    선정 모델로 수확 기간 가격 전망. 과거 실험 저장값은 덮어쓰지 않는다.
    POST /forecaster/forecast/<farm_id>/
    Body: harvest_start=YYYY-MM-DD, harvest_end=YYYY-MM-DD
    """
    farm = get_object_or_404(Farm, id=farm_id, user=request.user)

    start_str = request.POST.get('harvest_start') or request.GET.get('harvest_start')
    end_str   = request.POST.get('harvest_end')   or request.GET.get('harvest_end')
    if not start_str or not end_str:
        return JsonResponse({'error': 'harvest_start, harvest_end 파라미터가 필요합니다.'}, status=400)

    try:
        harvest_start = date.fromisoformat(start_str)
        harvest_end   = date.fromisoformat(end_str)
    except ValueError:
        return JsonResponse({'error': '날짜 형식이 올바르지 않습니다 (YYYY-MM-DD).'}, status=400)

    try:
        result = selected_curve(farm, harvest_start, harvest_end)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"가격 예측 오류 farm_id={farm_id}: {e}")
        return JsonResponse({'error': f'예측 중 오류가 발생했습니다: {e}'}, status=500)

    forecasts = result['forecasts']

    return JsonResponse({
        'ok':            True,
        'harvest_start': start_str,
        'harvest_end':   end_str,
        'forecast_days': len(forecasts),
        'forecasts':     forecasts,
        'model_id':     MODEL_ID,
    })


@login_required
def price_forecast_api(request, farm_id: int):
    """
    캘린더 Ajax용: 저장된 PriceForecast 반환.
    GET /forecaster/api/forecast/<farm_id>/
    """
    farm = get_object_or_404(Farm, id=farm_id, user=request.user)

    today = timezone.localdate()
    dates = list(CropSchedule.objects.filter(farm=farm, task_type='harvest', scheduled_at__gt=today,
                 scheduled_at__lte=today+timedelta(days=365)).order_by('scheduled_at').values_list('scheduled_at', flat=True))
    data, reason, metadata = [], '', {}
    if dates:
        try:
            result = selected_curve(farm, dates[0], dates[-1], as_of=today)
            data = result.pop('forecasts')
            metadata = result
        except (ValueError, OSError) as exc:
            reason = str(exc)
    history_qs = PriceData.objects.filter(
        farm=farm,
        date__gte=today - timedelta(days=90),
        date__lte=today,
    ).order_by('date')

    history = [
        {
            'date':  h.date.isoformat(),
            'price': h.price,
        }
        for h in history_qs
    ]

    # 호환 필드는 유지하지만 관측과 수확기 예측 사이에 가격을 만들어 넣지 않는다.
    response = JsonResponse({'forecasts': data, 'history': history, 'bridge': [], 'model_id': MODEL_ID,
                             'reason': reason, 'metadata': metadata})
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response['Pragma'] = 'no-cache'
    return response

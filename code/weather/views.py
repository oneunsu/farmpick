import logging

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.urls import reverse

from accounts.models import Farm
from weather import kma_api

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# 헬퍼: farm 가져오기
# ─────────────────────────────────────────

def _get_farm(request, source='POST'):
    """요청에서 farm_id 추출 → Farm 객체 반환 (없으면 첫 번째 농장)"""
    data = request.POST if source == 'POST' else request.GET
    farm_id = data.get('farm')
    if farm_id:
        return get_object_or_404(Farm, pk=farm_id, user=request.user)
    farm = Farm.objects.filter(user=request.user).first()
    if not farm:
        return None
    return farm


# ─────────────────────────────────────────
# 1. 예보 수집 뷰
# ─────────────────────────────────────────

@login_required
@require_POST
def fetch_forecast_view(request):
    """단기 + 중기 예보를 수집해서 DB에 저장"""
    farm = _get_farm(request)
    if not farm:
        messages.error(request, '등록된 농장이 없습니다. 먼저 농장을 등록해 주세요.')
        return redirect('farm_list')

    try:
        result = kma_api.fetch_and_save_forecast(farm)
        messages.success(
            request,
            f'[{farm.name}] 예보 수집 완료 — '
            f'단기 {result["short"]}건 · 중기 {result["mid"]}건'
        )
    except Exception as e:
        logger.error(f'fetch_forecast 오류: {e}', exc_info=True)
        messages.error(request, f'예보 수집 중 오류가 발생했습니다: {e}')

    next_url = request.POST.get('next', 'farm_list')
    if next_url in {'calendar_app:summary', 'calendar_app:index'}:
        return redirect(f'{reverse(next_url)}?farm={farm.pk}')
    return redirect(next_url)



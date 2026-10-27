from datetime import date
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Farm
from calendar_app.models import CropSchedule
from .forecast_records import make_payload, read_token, save_payload
from .harvest_outlook import farm_outlook
from .harvest_chart import build_harvest_chart
from .models import ForecastRun


@login_required
@require_POST
def issue(request, farm_id):
    farm = get_object_or_404(Farm, pk=farm_id, user=request.user)
    try:
        target = date.fromisoformat(request.POST.get('target', ''))
        token = request.POST.get('forecast_token')
        payload = read_token(token, farm, request.user) if token else None
        if payload is None or payload['outlook']['target'] != target.isoformat():
            today = timezone.localdate()
            if not 1 <= (target-today).days <= 365:
                raise ValueError('수확일은 내일부터 365일 이내여야 합니다.')
            result = farm_outlook(farm, target, as_of=today)
            chart = build_harvest_chart(farm, target, list(CropSchedule.objects.filter(farm=farm, task_type='harvest')), as_of=today) if result.get('available') else None
            payload = make_payload(farm, target, result, chart=chart)
        run = save_payload(farm, payload)
    except (ValueError, signing.BadSignature):
        return HttpResponseBadRequest('전망을 저장하지 못했습니다. 화면이 오래되었거나 자료가 변경됐을 수 있습니다. 새로고침해 다시 확인해주세요.')
    if request.POST.get('action') == 'revenue':
        return redirect(reverse('forecaster:revenue_create')+'?'+urlencode(
            {'farm': farm.pk, 'target': run.target_date.isoformat(), 'forecast_run': run.pk}))
    return redirect('forecaster:forecast_run', pk=run.pk)


@login_required
@require_GET
def run_detail(request, pk):
    run = get_object_or_404(ForecastRun.objects.select_related('farm'), pk=pk, farm__user=request.user)
    response = render(request, 'forecaster/forecast_run.html', {'run': run, 'farm': run.farm,
        'outlook': run.payload['outlook'], 'chart': run.payload.get('chart', {'available': False})})
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def run_list(request):
    farms = Farm.objects.filter(user=request.user).order_by('-created_at')
    farm = get_object_or_404(farms, pk=request.GET['farm']) if request.GET.get('farm') else farms.first()
    from django.core.paginator import Paginator
    runs = ForecastRun.objects.filter(farm=farm) if farm else ForecastRun.objects.none()
    response = render(request, 'forecaster/forecast_runs.html', {'farm': farm, 'runs': Paginator(runs, 20).get_page(request.GET.get('page'))})
    response['Cache-Control'] = 'private, no-store'
    return response

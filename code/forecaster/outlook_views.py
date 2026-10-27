from datetime import date
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.models import Farm
from calendar_app.models import CropSchedule
from calendar_app.harvest_status import harvest_status
from .harvest_outlook import farm_outlook
from .harvest_chart import build_harvest_chart
from .forecast_records import make_payload, make_token


@login_required
@require_GET
def outlook(request, farm_id, api=False):
    farm = get_object_or_404(Farm, pk=farm_id, user=request.user)
    today = timezone.localdate()
    all_harvests = list(CropSchedule.objects.filter(farm=farm, task_type='harvest').order_by('scheduled_at'))
    schedules = [item for item in all_harvests if item.scheduled_at >= today]
    harvest = harvest_status(all_harvests, today)
    target = harvest['forecast_date']
    status = 200
    if request.GET.get('target'):
        try:
            target = date.fromisoformat(request.GET['target'])
        except ValueError:
            target, status = None, 400
    result = farm_outlook(farm, target, as_of=today) if target else {
        'available': False, 'reason': '올바른 수확 예정일을 선택해 주세요.' if status == 400 else harvest['price_reason'], 'as_of': today.isoformat()}
    if api:
        response = JsonResponse(result, status=status)
    else:
        chart = build_harvest_chart(farm, target, all_harvests, as_of=today) if result.get('available') else {
            'available': False, 'reason': result.get('reason', '가격 자료를 확인해주세요.')}
        token = ''
        capture_error = ''
        if target and status == 200:
            try:
                token = make_token(make_payload(farm, target, result, chart=chart))
            except (ValueError, OSError):
                capture_error = '전망과 입력 자료를 함께 확인하지 못했습니다. 새로고침한 뒤 발행해주세요.'
        response = render(request, 'forecaster/outlook.html', dict(
            farm=farm, outlook=result, target=target, schedules=schedules, chart=chart,
            forecast_token=token, capture_error=capture_error), status=status)
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def validation(request):
    if not request.user.is_staff:
        return redirect('forecaster:model_result')
    crop = request.GET.get('crop', 'paprika')
    choices = {'paprika': ('파프리카', 'harvest_20261023_exploratory'),
               'tomato': ('토마토', 'tomato_harvest_20261023_exploratory')}
    if crop not in choices:
        raise Http404
    crop_name, folder = choices[crop]
    path = Path(settings.BASE_DIR) / 'reports' / folder / 'summary.json'
    summary = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    scores = [r for r in summary['scores'] if r['split'] == 'test'] if summary else []
    ranges = [r for r in summary['scenario_ranges'] if r['split'] == 'test'] if summary else []
    response = render(request, 'forecaster/outlook_validation.html', dict(summary=summary, scores=scores, ranges=ranges,
                      crop=crop, crop_name=crop_name))
    response['Cache-Control'] = 'private, no-store'
    return response


def load_model_comparison(history='initial'):
    folders = {'initial': 'model_comparison_20261023', 'extended': 'model_comparison_extended_20261023'}
    path = Path(settings.BASE_DIR) / 'reports' / folders[history] / 'summary.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


@login_required
@require_GET
def model_result(request):
    from .selected_forecast import load_release, MODEL_LABEL
    crop = request.GET.get('crop', 'tomato')
    names = {'tomato': '토마토', 'paprika': '파프리카'}
    if crop not in names:
        raise Http404
    release, rows, reason = None, [], ''
    try:
        release = load_release()
        for lead in release['evaluated_horizons']:
            scores = {r['split']: r for r in release['evidence_scores'][crop] if r['lead'] == lead}
            rows.append(dict(lead=lead, validation=scores['validation'], test=scores['test']))
    except (ValueError, OSError) as exc:
        reason = str(exc)
    response = render(request, 'forecaster/model_result.html', dict(release=release, rows=rows, crop=crop,
                      crop_name=names[crop], model_name=MODEL_LABEL, reason=reason))
    response['Cache-Control'] = 'private, no-store'
    return response


def load_historical_stability():
    path = Path(settings.BASE_DIR) / 'reports/historical_stability_20261023/summary.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


@login_required
@require_GET
def model_comparison(request):
    if not request.user.is_staff:
        return redirect('forecaster:model_result')
    crop = request.GET.get('crop', 'paprika')
    names = {'paprika': '파프리카', 'tomato': '토마토'}
    if crop not in names:
        raise Http404
    history = request.GET.get('history', 'initial')
    if history not in ('initial', 'extended'):
        return JsonResponse({'error': '허용된 자료 이력을 선택해 주세요.'}, status=400)
    try:
        width = int(request.GET.get('window', '1'))
        if width not in (1, 7, 14):
            raise ValueError
    except ValueError:
        return JsonResponse({'error': '평가창은 1·7·14일 중 선택해 주세요.'}, status=400)
    artifact = load_model_comparison(history)
    result = artifact['crops'].get(crop) if artifact else None
    rows, selected = [], []
    if result:
        for lead in (7, 14, 30, 60, 90, 180):
            scores = {r['method']: r for r in result['scores'] if r['split'] == 'test' and r['width'] == width and r['lead'] == lead}
            if scores:
                rows.append(dict(lead=lead, n=scores['last']['n'], scores=scores))
        for choice in result['selected_on_validation']:
            test = next((r for r in result['scores'] if r['split'] == 'test' and r['lead'] == choice['lead']
                         and r['width'] == 1 and r['method'] == choice['selected']), None)
            selected.append({**choice, 'test': test})
    historical = load_historical_stability() if history == 'extended' else None
    historical_rows = []
    if historical:
        for year in historical['years']:
            for lead in (7, 14, 30, 60, 90, 180):
                scores = {r['method']: r for r in historical['crops'][crop]['scores_by_year']
                          if r['year'] == year and r['width'] == width and r['lead'] == lead}
                if scores:
                    historical_rows.append(dict(year=year, lead=lead, n=scores['last']['n'], scores=scores))
    response = render(request, 'forecaster/model_comparison.html', dict(crop=crop, crop_name=names[crop], width=width,
                      artifact=artifact, result=result, rows=rows, selected=selected, history=history,
                      historical=historical, historical_rows=historical_rows))
    response['Cache-Control'] = 'private, no-store'
    return response

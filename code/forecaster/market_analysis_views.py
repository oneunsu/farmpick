from datetime import date
from decimal import Decimal
from django import forms
from django.contrib.auth.decorators import login_required
from django.forms import formset_factory
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import Farm
from .models import RevenuePlan
from .market_pricing import cached_representative_series as representative_series
from .market_projection import project_prices, receipt_scenarios, VERSION
from .harvest_outlook import farm_outlook
from .forecast_records import make_payload, save_payload


class CostForm(forms.Form):
    include = forms.BooleanField(label='비교', required=False)
    market_code = forms.CharField(widget=forms.HiddenInput)
    freight = forms.DecimalField(label='위탁 운송비 총액', min_value=0, max_value=Decimal('1e12'), decimal_places=0, required=False)
    commission = forms.DecimalField(label='수수료 %', min_value=0, max_value=100, decimal_places=2, required=False)
    packing = forms.DecimalField(label='포장비 원/kg', min_value=0, max_value=Decimal('1e9'), decimal_places=2, required=False)
    other = forms.DecimalField(label='기타 비용 총액', min_value=0, max_value=Decimal('1e12'), decimal_places=0, required=False)

    def clean(self):
        data = super().clean()
        if data.get('include'):
            for key in ('freight', 'commission', 'packing', 'other'):
                if data.get(key) is None:
                    self.add_error(key, '비용이 없으면 0을 입력해주세요.')
        return data


def chart_data(records, start, end):
    from datetime import timedelta
    days = [start+timedelta(days=i) for i in range((end-start).days+1)]
    markets = sorted({(r['market_code'], r['market']) for r in records})
    lookup = {(r['date'], r['market_code']): float(r['price']) for r in records}
    return {'labels': [d.isoformat() for d in days], 'datasets': [
        {'label': name, 'data': [lookup.get((d.isoformat(), code)) for d in days],
         'spanGaps': False} for code, name in markets]}


@login_required
@require_http_methods(['GET', 'POST'])
def analysis(request, farm_id, api=False):
    if api and request.method != 'GET':
        return JsonResponse({'error': '조회 API는 GET만 허용합니다.'}, status=405)
    farm = get_object_or_404(Farm, pk=farm_id, user=request.user)
    query = request.POST if request.method == 'POST' else request.GET
    data = representative_series(farm.crop_type)
    selected = query.get('condition') or (data['choices'][0]['key'] if data['choices'] else '')
    if selected and selected not in {c['key'] for c in data['choices']}:
        return JsonResponse({'error': '비교 조건이 없거나 자료가 변경됐습니다.'}, status=400)
    records = [r for r in data['records'] if r['cohort'] == selected]
    latest = {}
    for row in records: latest[row['market_code']] = row
    plans = RevenuePlan.objects.filter(farm=farm).order_by('-updated_at')
    if query.get('plan') and (not query['plan'].isdecimal() or len(query['plan']) > 18):
        return JsonResponse({'error': '출하 계획 선택값이 올바르지 않습니다.'}, status=400)
    plan = get_object_or_404(plans, pk=query['plan']) if query.get('plan') else plans.first()
    # 조건 선택 GET은 KAMIS 모델을 실행하지 않는다. 계산할 때만 전망을 생성한다.
    CostSet = formset_factory(CostForm, extra=0, max_num=100, validate_max=True, absolute_max=100)
    initials = [{'market_code': code} for code in sorted(latest)]
    costs = CostSet(request.POST if request.method == 'POST' else None, initial=initials, prefix='costs')
    for form, code in zip(costs, sorted(latest)):
        form.market_label = latest[code]['market']
        for field in form.visible_fields():
            field.field.widget.attrs['aria-label'] = form.market_label + ' ' + field.label
            if field.name != 'include': field.field.widget.attrs['class'] = 'form-control form-control-sm'
    error, projection_excluded = '', []
    if request.method == 'POST':
        if plan is None:
            error = '수취액 계산에서 수확일·수확량·판매가능 비율을 저장한 뒤 선택해주세요.'
        elif not costs.is_valid():
            error = '선택한 시장의 비용을 확인해주세요.'
        elif (len(costs.forms) != len(initials) or
              [f.cleaned_data.get('market_code') for f in costs] != sorted(latest)):
            error = '시장 목록이 변경됐습니다. 다시 조회해주세요.'
        else:
            try:
                outlook = farm_outlook(farm, plan.harvest_date)
                payload = make_payload(farm, plan.harvest_date, outlook)
                projections, projection_excluded = project_prices(records, outlook, payload['input_records'])
                if not outlook.get('available'):
                    raise ValueError(outlook['reason'])
                outputs = receipt_scenarios(plan, projections, costs.cleaned_data)
                payload['market_projection'] = {'version': VERSION, 'condition': next(c['label'] for c in data['choices'] if c['key'] == selected),
                    'condition_key': selected, 'plan_id': plan.pk, 'harvest_kg': str(plan.harvest_kg),
                    'saleable_percent': str(plan.saleable_percent),
                    'production_cost': None if plan.production_cost is None else str(plan.production_cost),
                    'rows': outputs, 'excluded': projection_excluded,
                    'warning': '과거 시장/KAMIS 가격비가 유지된다는 가정입니다. 시장별 예측 정확도·농가 실제 수취액 정확도는 미검증입니다.'}
                run = save_payload(farm, payload)
                return redirect('forecaster:forecast_run', pk=run.pk)
            except (ValueError, OSError) as exc:
                error = str(exc)
    chart_start = date.fromisoformat(min(r['date'] for r in records)) if records else timezone.localdate()
    chart = chart_data(records, chart_start, timezone.localdate())
    response = (JsonResponse({'version': data['version'], 'condition': selected,
        'records': records, 'excluded': data['excluded'], 'chart': chart}) if api else
        render(request, 'forecaster/market_analysis.html', {'farm': farm, 'data': data, 'selected': selected,
            'records': records, 'latest': list(latest.values()), 'chart': chart, 'plans': plans, 'plan': plan,
            'costs': costs, 'error': error, 'projection_excluded': projection_excluded}))
    if error: response.status_code = 400
    response['Cache-Control'] = 'private, no-store'
    return response

"""KAMIS 변화율을 정산가격에 적용하는 미검증 시나리오. 시장 전용 예측 모델이 아니다."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from .revenue import calculate_receipts
from .models import SalesChannelEstimate

VERSION = 'market-kamis-relative-scenario-v1'


def project_prices(records, outlook, input_records):
    if not outlook.get('available'):
        return [], [{'market': 'KAMIS', 'reason': outlook.get('reason', '전망 없음')}]
    as_of = date.fromisoformat(outlook['as_of'])
    if len({r['cohort'] for r in records}) > 1:
        raise ValueError('서로 다른 비교 조건의 시장가격은 섞을 수 없습니다.')
    observations = {r['date']: Decimal(str(r['price'])) for r in input_records}
    markets = defaultdict(list)
    for row in records:
        day = date.fromisoformat(row['date'])
        if as_of-timedelta(days=27) <= day < as_of and row['date'] in observations:
            markets[row['market_code']].append(row)
    result, excluded = [], []
    for code in sorted({r['market_code'] for r in records}):
        history = sorted(markets[code], key=lambda r: r['date'])
        name = next(r['market'] for r in records if r['market_code'] == code)
        if len({r['date'] for r in history}) < 2:
            excluded.append({'market': name, 'reason': '최근 28일에 KAMIS와 같은 날의 유효 정산가격이 2일 이상 필요합니다.'})
            continue
        if (as_of-date.fromisoformat(history[-1]['date'])).days > 7:
            excluded.append({'market': name, 'reason': '같은 날 비교 가능한 최근 정산자료가 7일 넘게 오래됐습니다.'})
            continue
        ratios = [Decimal(r['price']) / observations[r['date']] for r in history]
        ratio = median(ratios)
        if not Decimal('.1') <= ratio <= 10:
            excluded.append({'market': name, 'reason': 'KAMIS 대비 가격 차이가 커 기준 확인이 필요합니다.'})
            continue
        price = (Decimal(str(outlook['base'])) * ratio).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        result.append({'market_code': code, 'market': name, 'price': str(price),
            'ratio': str(ratio), 'matched_days': len(history), 'last': history[-1]['date'],
            'basis': history, 'version': VERSION})
    return result, excluded


def receipt_scenarios(plan, projections, costs):
    results = []
    mapping = {r['market_code']: r for r in projections}
    for inputs in costs:
        if not inputs.get('include'):
            continue
        code = inputs['market_code']
        if code not in mapping:
            raise ValueError('선택한 시장은 현재 자료로 계산할 수 없습니다. 아래 시장별 제외 사유를 확인해주세요.')
        projection = mapping[code]
        channel = SalesChannelEstimate(name=projection['market'], price_per_kg=Decimal(projection['price']),
            transport_mode='hired', freight_cost=inputs['freight'], commission_percent=inputs['commission'],
            packing_per_kg=inputs['packing'], other_cost=inputs['other'])
        row = calculate_receipts(plan, [channel])['rows'][0]
        results.append({**projection, 'costs': {k: str(inputs[k]) for k in ('freight', 'commission', 'packing', 'other')},
            **{k: None if row[k] is None else str(row[k]) for k in (
                'gross', 'transport', 'packing', 'commission', 'other', 'expenses', 'receipts', 'profit')}})
    if not results:
        raise ValueError('계산할 시장을 하나 이상 선택해주세요.')
    return results

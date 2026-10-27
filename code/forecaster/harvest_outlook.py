"""수확일 참고 전망. 웹과 평가에서 공유하며 미래 관측을 사용하지 않는다."""
import hashlib
import json
import math
from datetime import date, timedelta

import numpy as np

from .date_utils import as_local_date
from .price_predictor import prepare_forecast_input

VERSION = 'harvest-last30-season14-q20-50-80-v1'
SHORT_DAYS = 30
SEASON_WINDOW = 14
MIN_YEAR_OBSERVATIONS = 10
MIN_YEARS = 2
MAX_YEARS = 5


def harvest_outlook(records, *, as_of, target):
    as_of, target = as_local_date(as_of), as_local_date(target)
    lead = (target - as_of).days
    if not 1 <= lead <= 365:
        raise ValueError('수확일은 기준일 다음 날부터 365일 이내로 선택해 주세요.')
    past = sorted(({'date': as_local_date(r['date']), 'price': float(r['price'])}
                   for r in records if as_local_date(r['date']) <= as_of), key=lambda r: r['date'])
    if not past:
        raise ValueError('기준일까지 확인된 KAMIS 관측자료가 없습니다.')
    if len({r['date'] for r in past}) != len(past) or any(not math.isfinite(r['price']) or r['price'] <= 0 for r in past):
        raise ValueError('관측 날짜 중복 또는 유효하지 않은 가격이 있습니다.')
    serialized = [[r['date'].isoformat(), r['price']] for r in past]
    result = dict(version=VERSION, as_of=as_of.isoformat(), target=target.isoformat(), lead_days=lead,
                  last_observed=past[-1]['date'].isoformat(), last_price=past[-1]['price'],
                  input_start=past[0]['date'].isoformat(), observations=len(past),
                  input_sha256=hashlib.sha256(json.dumps(serialized, separators=(',', ':')).encode()).hexdigest(),
                  unit='원/kg', source='KAMIS 중도매인 판매 참고가격', low=None, high=None, years=[])
    if lead <= SHORT_DAYS:
        context, _, _ = prepare_forecast_input(past, as_of=as_of, end_date=target)
        result.update(method='last', label='단기 기준 전망 · 최근 관측가 유지', base=float(context[-1]))
        return result

    samples = []
    # 대상일의 월·일을 과거 연도에 대응한다. 윤일만 2월 28일로 대응한다.
    for year in range(as_of.year, as_of.year - MAX_YEARS - 1, -1):
        if year >= target.year:
            continue
        try:
            center = date(year, target.month, target.day)
        except ValueError:
            center = date(year, 2, 28)
        start, end = center - timedelta(days=SEASON_WINDOW), center + timedelta(days=SEASON_WINDOW)
        # 아직 끝나지 않은 과거 계절창이나 부분 수집창은 쓰지 않는다.
        if end > as_of or start < past[0]['date']:
            continue
        values = [r['price'] for r in past if start <= r['date'] <= end]
        if len(values) < MIN_YEAR_OBSERVATIONS:
            continue
        samples.extend(values)
        result['years'].append(dict(year=year, start=start.isoformat(), end=end.isoformat(), observations=len(values)))
        if len(result['years']) == MAX_YEARS:
            break
    if len(result['years']) < MIN_YEARS:
        raise ValueError('같은 계절의 완결된 관측창이 2개 연도 이상 필요합니다(연도별 최소 10개 관측). 가격 이력을 추가 수집해 주세요.')
    low, base, high = np.quantile(samples, [.2, .5, .8], method='linear').tolist()
    result.update(method='seasonal_window', label='과거 같은 계절 시나리오', low=low, base=base, high=high,
                  scenario_observations=len(samples))
    return result


def farm_outlook(farm, target, *, as_of=None):
    # 과거 평가의 harvest_outlook()은 그대로 보존하고 웹만 선정 모델로 연결한다.
    from .selected_forecast import selected_outlook
    return selected_outlook(farm, target, as_of=as_of)

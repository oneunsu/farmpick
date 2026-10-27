"""정산 원본을 보존하는 보수적 중량 환산·동일 조건 대표가격 조회."""
from collections import defaultdict, Counter
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from django.utils import timezone
from django.core.cache import cache
from django.db.models import Count, Max
from .models import MarketSnapshot, MarketSettlement

VERSION = 'settlement-weight-cohort-v1'
COHORT_KEYS = ('grade', 'size', 'package', 'origin', 'trade')
D = Decimal


def cached_representative_series(crop, *, as_of=None, start=None):
    """웹의 반복 집계만 캐시한다. 새 스냅샷/삭제 시 키를 바꾸고 60초 후 만료한다."""
    as_of = as_of or timezone.localdate()
    start = start or as_of - timedelta(days=89)
    stamp = MarketSnapshot.objects.filter(crop_type=crop, collected_at__lte=timezone.now()).aggregate(
        count=Count('pk'), last=Max('pk'), imported=Max('imported_at'))
    key = 'market-series:' + hashlib.sha256(json.dumps(
        [VERSION, crop, str(as_of), str(start), stamp], default=str, sort_keys=True).encode()).hexdigest()
    result = cache.get(key)
    if result is None:
        result = representative_series(crop, as_of=as_of, start=start)
        cache.set(key, result, timeout=60)
    return result


def convert_row(row):
    """총물량을 단위 중량으로 해석하되 금액·가격 범위로 확인, 다른 해석 가능하면 거부."""
    unit = row.dimensions.get('unit', {})
    factor = {'kg': D(1), '㎏': D(1), 'g': D('.001')}.get(unit.get('name', '').lower())
    if not unit.get('verified') or factor is None:
        return {'available': False, 'reason': '중량 단위 미확인'}
    values = [row.unit_weight, row.total_quantity, row.total_amount, row.average_price,
              row.minimum_price, row.maximum_price]
    if any(v is None or not v.is_finite() or v <= 0 for v in values):
        return {'available': False, 'reason': '가격·물량 누락 또는 유효하지 않은 값'}
    weight, qty, amount, avg, low, high = values
    if not low <= avg <= high:
        return {'available': False, 'reason': '평균가·최저가·최고가 불일치'}
    kg_per_pack = weight * factor
    kg = qty * factor
    price = amount / kg
    lower, upper = low / kg_per_pack, high / kg_per_pack
    # API의 원 단위 가격 반올림만 허용한다. 큰 오차를 비율 허용치로 숨기지 않는다.
    tolerance = D(1) / kg_per_pack
    if not lower - tolerance <= price <= upper + tolerance:
        return {'available': False, 'reason': '총금액·총중량·가격 범위 불일치'}
    count_price = amount / (qty * kg_per_pack)
    if weight != 1 and lower - tolerance <= count_price <= upper + tolerance:
        return {'available': False, 'reason': '총물량의 중량/포장수 해석이 모호함'}
    return {'available': True, 'kg': kg, 'amount': amount, 'krw_per_kg': price,
            'package_average_per_kg': avg / kg_per_pack, 'version': VERSION}


def cohort(row):
    required = ('variety', 'grade', 'size', 'package', 'origin')
    if any(not row.dimensions.get(k, {}).get('verified') for k in required):
        return None
    if row.dimensions.get('trade', {}).get('code') in {None, '', '.', '-', 'None', 'null'}:
        return None
    unit = row.dimensions.get('unit', {})
    factor = {'kg': D(1), '㎏': D(1), 'g': D('.001')}.get(unit.get('name', '').lower())
    if (not unit.get('verified') or factor is None or row.unit_weight is None or
            not row.unit_weight.is_finite() or row.unit_weight <= 0):
        return None
    pack_kg = format((row.unit_weight * factor).normalize(), 'f')
    codes = [row.product_key] + [row.dimensions[k]['code'] for k in COHORT_KEYS]
    key = hashlib.sha256(json.dumps(codes + [pack_kg], ensure_ascii=False).encode()).hexdigest()[:24]
    label = ' / '.join([row.variety_name] + [row.dimensions[k]['name'] for k in COHORT_KEYS[:-1]]
                       + [pack_kg + 'kg/포장', '매매구분 ' + codes[-1]])
    return key, label


def representative_series(crop, *, as_of=None, start=None):
    as_of = as_of or timezone.localdate()
    start = start or as_of - timedelta(days=89)
    # 조회일·수집시각 양쪽에서 미래를 차단한다. 당시 공시 판본의 재현 백테스트는 별도다.
    snapshots = list(MarketSnapshot.objects.filter(crop_type=crop,
        collected_at__lte=timezone.now()).order_by('-collected_at', '-pk'))
    latest = {}
    for snapshot in snapshots:
        for coverage in snapshot.coverage:
            if start.isoformat() <= coverage['date'] <= as_of.isoformat():
                scope = (coverage['date'], coverage['market'], coverage['category'], coverage['item'])
                latest.setdefault(scope, (snapshot.pk, coverage))
    groups, labels, excluded = defaultdict(list), {}, Counter()
    rows = MarketSettlement.objects.filter(snapshot_id__in={v[0] for v in latest.values()},
        settlement_date__range=(start, as_of)).select_related('snapshot').defer('raw')
    for row in rows:
        category, item, _ = row.product_key.split('/')
        entry = latest.get((row.settlement_date.isoformat(), row.market_code, category, item))
        if entry is None or entry[0] != row.snapshot_id:
            continue
        condition = cohort(row)
        if condition is None:
            excluded['품종·등급·크기·포장·산지·중량·매매구분 미확인'] += 1
            continue
        key, label = condition
        labels[key] = label
        groups[(key, row.settlement_date, row.market_code)].append((row, entry[1], convert_row(row)))
    result = []
    for (key, day, market), entries in groups.items():
        reasons = {e[2]['reason'] for e in entries if not e[2]['available']}
        if any(e[1]['partial'] for e in entries):
            reasons.add('전체 페이지 수집 필요')
        if reasons:
            for reason in reasons: excluded[reason] += len(entries)
            continue  # 환산 가능한 일부만 골라 전체 조건의 대표가인 것처럼 표시하지 않는다.
        kg = sum((e[2]['kg'] for e in entries), D(0))
        amount = sum((e[2]['amount'] for e in entries), D(0))
        price = (amount / kg).quantize(D('.01'), rounding=ROUND_HALF_UP)
        result.append({'cohort': key, 'condition': labels[key], 'date': day.isoformat(),
            'market_code': market, 'market': entries[0][0].market_name, 'price': str(price),
            'kg': str(kg), 'amount': str(amount), 'rows': len(entries),
            'source_rows': [e[0].pk for e in entries],
            'snapshot_checksums': sorted({e[0].snapshot.checksum for e in entries}),
            'version': VERSION})
    result.sort(key=lambda r: (r['date'], r['market_code'], r['cohort']))
    stats = defaultdict(lambda: defaultdict(list))
    for r in result: stats[r['cohort']][r['market_code']].append(r['date'])
    choices = []
    for key, markets in stats.items():
        # 이 숫자는 정산자료의 필요조건만 점검한다. KAMIS 같은 날 관측 여부는 계산 시 확인한다.
        recent = sum(len([d for d in days if d >= (as_of-timedelta(days=27)).isoformat()]) >= 2
                     and max(days) >= (as_of-timedelta(days=7)).isoformat() for days in markets.values())
        choices.append({'key': key, 'label': labels[key], 'markets': len(markets), 'recent_markets': recent,
                        'last': max(max(days) for days in markets.values())})
    choices.sort(key=lambda c: (c['recent_markets'], c['last'], c['markets'], c['label']), reverse=True)
    return {'records': result, 'choices': choices, 'excluded': dict(excluded), 'version': VERSION,
            'start': start.isoformat(), 'end': as_of.isoformat()}

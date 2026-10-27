"""인증정보 없는 KAMIS 스냅샷과 품질 점검."""
import hashlib
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n'
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quality_report(records, start, end):
    if not records:
        raise ValueError('스냅샷에 관측 가격이 없습니다.')
    dates = [date.fromisoformat(r['date']) for r in records]
    if dates != sorted(set(dates)):
        raise ValueError('스냅샷 날짜가 중복되거나 정렬되지 않았습니다.')
    if dates[0] < start or dates[-1] > end:
        raise ValueError('스냅샷 날짜가 수집 범위를 벗어납니다.')
    if any(not math.isfinite(r['price']) or r['price'] <= 0 for r in records):
        raise ValueError('유효하지 않은 가격이 있습니다.')
    basis = records[0].get('provenance')
    if not basis or any(r.get('provenance') != basis for r in records):
        raise ValueError('가격 기준이 없거나 서로 다른 기준이 섞여 있습니다.')
    available = set(dates)
    missing = [start + timedelta(days=i) for i in range((end-start).days+1)
               if start + timedelta(days=i) not in available]
    gaps = [
        {'from': (a+timedelta(days=1)).isoformat(), 'to': (b-timedelta(days=1)).isoformat(),
         'days': (b-a).days-1}
        for a, b in zip(dates, dates[1:]) if (b-a).days > 1
    ]
    return {
        'observations': len(records), 'first_observation': dates[0].isoformat(),
        'last_observation': dates[-1].isoformat(), 'calendar_days': (end-start).days+1,
        'missing_calendar_days': len(missing),
        'missing_weekdays': sum(d.weekday() < 5 for d in missing),
        'missing_note': '미공시 사유는 응답만으로 확정할 수 없음; 휴일·누락을 단정하지 않음',
        'longest_internal_gap_days': max((g['days'] for g in gaps), default=0),
        'gaps_over_7_days': [g for g in gaps if g['days'] > 7],
        'observations_by_year': dict(sorted(Counter(str(d.year) for d in dates).items())),
        'min_price': min(r['price'] for r in records), 'max_price': max(r['price'] for r in records),
        'latest_lag_days_from_requested_end': (end-dates[-1]).days,
        'price_basis': basis,
    }

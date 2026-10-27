"""선정 모델과 같은 입력·추론을 사용하는 수확일 전망 그래프 자료."""
from datetime import timedelta
import math

from .selected_forecast import prepare_live_input, predict_dates, MODEL_ID


def harvest_period(schedules, target, as_of):
    starts = sorted(s.scheduled_at for s in schedules if '시작' in s.task_name)
    ends = sorted(s.scheduled_at for s in schedules if '종료' in s.task_name)
    before = [day for day in starts if day <= target]
    start = before[-1] if before else next((day for day in starts if day > as_of), None)
    if start is None:
        return None, None
    next_start = next((day for day in starts if day > start), None)
    end = next((day for day in ends if day >= start and (next_start is None or day < next_start)), None)
    if end is not None and end < target:
        return None, None
    return start, end


def build_harvest_chart(farm, target, schedules, *, as_of):
    if not target or not 1 <= (target-as_of).days <= 365:
        return {'available': False, 'reason': '수확 예정일을 기준일 다음 날부터 365일 이내로 선택해주세요.'}
    start, end = harvest_period(schedules, target, as_of)
    requested_end = max(day for day in (target, start, end) if day is not None)
    graph_end = min(requested_end, as_of+timedelta(days=365))
    try:
        history, model, release, evidence = prepare_live_input(farm, as_of=as_of)
        future = [as_of+timedelta(days=i) for i in range(1, (graph_end-as_of).days+1)]
        prices = predict_dates(history, model, as_of=as_of, dates=future)
    except (ValueError, OSError) as exc:
        return {'available': False, 'reason': str(exc)}
    forecast = dict(zip(future, prices))
    first = as_of-timedelta(days=89)
    rows = []
    for offset in range((graph_end-first).days+1):
        day = first+timedelta(days=offset)
        index = history.day_index(day)
        observed = history.observed[index] if day <= as_of and 0 <= index < history.days else float('nan')
        rows.append({'date': day.isoformat(),
                     'observed': float(observed) if math.isfinite(observed) else None,
                     'forecast': round(forecast[day], 1) if day in forecast else None})
    return {'available': True, 'model_id': MODEL_ID, 'as_of': as_of.isoformat(),
            'target': target.isoformat(), 'harvest_start': start.isoformat() if start else None,
            'harvest_end': end.isoformat() if end else None, 'truncated': requested_end > graph_end,
            'last_observed': evidence['last_observed'], 'input_sha256': evidence['input_sha256'], 'rows': rows}

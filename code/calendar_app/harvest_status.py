"""수확 일정의 현재 상태. 종료일이나 기간을 추측해 만들지 않는다."""
from datetime import timedelta


def harvest_status(schedules, today):
    schedules = sorted(schedules, key=lambda item: (item.scheduled_at, item.pk or 0))
    starts = {item.scheduled_at: item for item in schedules if '시작' in item.task_name}
    ends = [item for item in schedules if '종료' in item.task_name]
    days = sorted(starts)
    periods = []
    for index, day in enumerate(days):
        next_day = days[index+1] if index+1 < len(days) else None
        end = next((item for item in ends if item.scheduled_at >= day and
                    (next_day is None or item.scheduled_at < next_day)), None)
        periods.append((starts[day], end))
    # 완료한 시작 작업도 수확 기간 전체의 완료를 뜻하지 않는다.
    active = [(start, end) for start, end in periods if end and not end.is_done and
              start.scheduled_at <= today <= end.scheduled_at]
    upcoming = [(start, end) for start, end in periods if start.scheduled_at > today and not (end and end.is_done)]
    # 사용자가 직접 등록한 단일 수확 일정도 미래 일정으로 선택한다.
    single = [item for item in schedules if '시작' not in item.task_name and '종료' not in item.task_name
              and item.scheduled_at >= today and not item.is_done]
    result = {'state': 'none', 'start': None, 'end': None, 'forecast_date': None,
              'remaining_days': None, 'dday': None, 'price_reason': '수확 일정 등록 후 전망을 확인할 수 있습니다.'}
    if active:
        start, end = active[-1]
        result.update(state='active', start=start, end=end, remaining_days=(end.scheduled_at-today).days, dday=0)
        if today < end.scheduled_at:
            result['forecast_date'] = today+timedelta(days=1)
        else:
            result['price_reason'] = '오늘은 등록한 수확 종료일입니다. 오늘 가격을 미래 예측으로 표시하지 않습니다.'
    elif upcoming or single:
        choices = upcoming + [(item, None) for item in single]
        start, end = min(choices, key=lambda pair: pair[0].scheduled_at)
        if start.scheduled_at == today:
            result.update(state='today', start=start, dday=0,
                          price_reason='오늘의 수확 일정입니다. 오늘 가격을 미래 예측으로 표시하지 않습니다.')
        else:
            result.update(state='upcoming', start=start, end=end,
                          forecast_date=start.scheduled_at, dday=(start.scheduled_at-today).days)
    elif periods:
        start, end = periods[-1]
        result.update(state='ended' if end else 'missing_end', start=start, end=end)
        result['price_reason'] = ('수확 종료 일정이 없습니다. 종료일을 확인하거나 전망 화면에서 날짜를 직접 선택해주세요.'
                                  if not end else '등록한 수확 기간이 종료됐습니다. 다음 수확일을 등록하거나 전망 화면에서 날짜를 선택해주세요.')
    elif any(item.scheduled_at >= today and not item.is_done for item in ends):
        end = next(item for item in ends if item.scheduled_at >= today and not item.is_done)
        result.update(state='missing_start', end=end,
                      price_reason='수확 시작 일정이 없습니다. 시작일을 확인하거나 전망 화면에서 날짜를 직접 선택해주세요.')
    elif schedules:
        result.update(state='past', start=schedules[-1], price_reason='지난 수확 일정만 있습니다. 다음 수확일을 선택해주세요.')
    return result

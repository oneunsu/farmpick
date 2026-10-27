"""가격 데이터에 사용하는 현지 달력 날짜 변환."""
from datetime import date, datetime

from django.utils import timezone


def as_local_date(value: date) -> date:
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()
    if not isinstance(value, date):
        raise ValueError('날짜는 date 또는 datetime이어야 합니다.')
    return value

"""사용자 입력 가정의 차액·가상 변동 비교. 시장 가격 추천·예측구간이 아니다."""
from copy import copy
from decimal import Decimal

from django import forms

from .revenue import calculate_receipts, won


class ComparisonForm(forms.Form):
    baseline = forms.ChoiceField(label='비교 기준 판로')
    price_shift = forms.DecimalField(label='판매단가 변동폭 (%)', min_value=0, max_value=50, decimal_places=1)
    transport_shift = forms.DecimalField(label='운송비 증가폭 (%)', min_value=0, max_value=100, decimal_places=1)

    def __init__(self, data, *, channels):
        super().__init__(data)
        self.fields['baseline'].choices = [(str(channel.pk), channel.name) for channel in channels]
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-select' if isinstance(field, forms.ChoiceField) else 'form-control'


def compare_channels(plan, channels, *, baseline, price_shift=Decimal(10), transport_shift=Decimal(20)):
    channels = list(channels)
    result = calculate_receipts(plan, channels)
    rows = result['rows']
    reference = next((row for row in rows if str(row['channel'].pk) == str(baseline)), None)
    if reference is None:
        raise ValueError('이 계산에 속한 기준 판로를 선택해주세요.')
    best = max(row['receipts'] for row in rows)
    for row in rows:
        row['delta'] = row['receipts'] - reference['receipts']
        row['is_best'] = row['receipts'] == best
    variants = {}
    for name, factor in [('down', 1-price_shift/100), ('up', 1+price_shift/100)]:
        changed = []
        for channel in channels:
            value = copy(channel)
            value.price_per_kg = channel.price_per_kg*factor
            changed.append(value)
        variants[name] = calculate_receipts(plan, changed)['rows']
    for index, row in enumerate(rows):
        row['price_down'] = variants['down'][index]['receipts']
        row['price_up'] = variants['up'][index]['receipts']
        row['transport_up'] = row['receipts'] - won(row['transport']*transport_shift/100)
    result.update(baseline=reference['channel'], leaders=[row['channel'].name for row in rows if row['is_best']],
                  best_receipts=best, gain_over_baseline=best-reference['receipts'],
                  price_shift=price_shift, transport_shift=transport_shift)
    return result

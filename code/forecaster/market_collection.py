"""웹 수집 입력 검증. 실제 API 접근은 기존 검증·가져오기 명령을 재사용한다."""
import json
from pathlib import Path

from django import forms
from django.conf import settings
from django.utils import timezone

from accounts.models import Farm


def market_choices():
    # 읽기 전용 공식 코드 사본. 화면을 열 때 API를 호출하지 않는다.
    path = Path(settings.BASE_DIR) / 'data/markets/paprika_settlement_verified_20261023/codes.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return sorted({(str(row['whsl_mrkt_cd']), row['whsl_mrkt_nm'])
                       for row in data['wholesaleMarkets']}, key=lambda item: item[1])
    except (OSError, KeyError, TypeError, ValueError):
        return []


class MarketCollectionForm(forms.Form):
    farm = forms.ModelChoiceField(queryset=Farm.objects.none(), widget=forms.HiddenInput)
    crop = forms.ChoiceField(label='수집 작물', choices=Farm.CROP_CHOICES)
    date = forms.DateField(label='시작일', widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(label='종료일', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    market = forms.MultipleChoiceField(label='수집 시장', choices=(), widget=forms.CheckboxSelectMultiple,
                                       help_text='일부 시장을 고르거나 전체 선택할 수 있습니다.')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['farm'].queryset = Farm.objects.filter(user=user)
        self.fields['market'].choices = market_choices()
        for name in ('date', 'end_date'):
            self.fields[name].widget.attrs.update({'max': timezone.localdate().isoformat(), 'class': 'form-control'})
        self.fields['crop'].widget.attrs['class'] = 'form-select'
        self.fields['market'].widget.attrs['class'] = 'market-choice-list'

    def clean_date(self):
        day = self.cleaned_data['date']
        if day > timezone.localdate():
            raise forms.ValidationError('미래 정산일은 수집할 수 없습니다.')
        return day

    def clean(self):
        data = super().clean()
        start = data.get('date')
        end = data.get('end_date') or start
        if start and end:
            data['end_date'] = end
            if end > timezone.localdate():
                self.add_error('end_date', '미래 정산일은 수집할 수 없습니다.')
            elif end < start:
                self.add_error('end_date', '종료일은 시작일보다 빠를 수 없습니다.')
            elif (end-start).days >= 7:
                self.add_error('end_date', '시작일과 종료일을 포함해 최대 7일까지 수집할 수 있습니다.')
        return data

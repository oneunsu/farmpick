from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from calendar_app.models import CropSchedule
from .models import RevenuePlan, SalesChannelEstimate, MarketSettlement


def style_fields(form):
    for field in form.fields.values():
        if not field.widget.is_hidden:
            field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                                           else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')


class RevenuePlanForm(forms.ModelForm):
    class Meta:
        model = RevenuePlan
        fields = ['title', 'harvest_schedule', 'harvest_date', 'harvest_kg', 'saleable_percent', 'production_cost']
        widgets = {'harvest_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})}

    def __init__(self, *args, farm, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['harvest_schedule'].queryset = CropSchedule.objects.filter(farm=farm, task_type='harvest')
        self.fields['harvest_schedule'].label_from_instance = lambda s: f'{s.scheduled_at} · {s.task_name}'
        style_fields(self)

    def clean(self):
        data = super().clean()
        schedule = data.get('harvest_schedule')
        if schedule and data.get('harvest_date') != schedule.scheduled_at:
            self.add_error('harvest_date', '선택한 수확 일정의 날짜와 맞춰주세요. 다른 날짜를 계산하려면 일정 연결을 해제하세요.')
        return data


class ChannelForm(forms.ModelForm):
    class Meta:
        model = SalesChannelEstimate
        exclude = ['plan', 'reference_snapshot']
        widgets = {'reference_settlement': forms.HiddenInput()}

    def __init__(self, *args, farm, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['reference_settlement'].queryset = MarketSettlement.objects.filter(snapshot__crop_type=farm.crop_type)
        # 비용의 빈 입력은 0원으로 통일한다. 판매단가는 명시적으로 입력해야 한다.
        self.cost_fields = ['freight_cost', 'round_trip_km', 'fuel_efficiency', 'fuel_price', 'toll_cost',
                            'packing_per_kg', 'commission_percent', 'other_cost']
        for key in self.cost_fields:
            self.fields[key].required = False
        if self.is_bound:
            mode = self.data.get(self.add_prefix('transport_mode'))
            inactive = (['freight_cost'] if mode == 'self' else
                        ['round_trip_km', 'fuel_efficiency', 'fuel_price', 'toll_cost'] if mode in {'hired', 'pickup'} else [])
            if mode == 'pickup':
                inactive.append('freight_cost')
            self.data = self.data.copy()
            for key in inactive:
                self.data[self.add_prefix(key)] = '0'
        style_fields(self)

    def clean(self):
        data = super().clean()
        for key in self.cost_fields:
            if data.get(key) is None:
                data[key] = Decimal(0)
        mode = data.get('transport_mode')
        if mode == 'self':
            for field in ('fuel_efficiency', 'fuel_price'):
                if data.get(field, 0) <= 0:
                    self.add_error(field, '자가 운송에는 0보다 큰 값을 입력하세요.')
            data['freight_cost'] = Decimal(0)
        else:
            for field in ('round_trip_km', 'fuel_efficiency', 'fuel_price', 'toll_cost'):
                data[field] = Decimal(0)
            if mode == 'pickup':
                data['freight_cost'] = Decimal(0)
        if data.get('price_source') == 'market_reference' and not data.get('reference_settlement'):
            self.add_error('price_source', '시장 정산자료 화면에서 참고 내역을 선택하거나 다른 단가 근거를 선택하세요.')
        return data


class BaseChannelFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        for form in self.forms:
            existing = form.cleaned_data.get('id')
            if existing and existing.plan_id != self.instance.pk:
                raise forms.ValidationError('이 계산에 속하지 않는 판로는 수정할 수 없습니다.')


ChannelFormSet = inlineformset_factory(
    RevenuePlan, SalesChannelEstimate, form=ChannelForm, formset=BaseChannelFormSet,
    extra=1, min_num=1, validate_min=True, max_num=100, validate_max=True, absolute_max=110, can_delete=True,
)

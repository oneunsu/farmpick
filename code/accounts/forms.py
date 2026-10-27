from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Farm
from weather.kma_api import NX_NY_TABLE, MID_LAND_CODE

REGION_CHOICES = [('', '--- 지역 선택 ---')] + [(r, r) for r in NX_NY_TABLE.keys()]


class SignUpForm(UserCreationForm):
    email = forms.EmailField(
        required=False,
        label='이메일',
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': '이메일 (선택)'}),
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = '아이디'
        self.fields['username'].widget.attrs.update({'class': 'form-control', 'placeholder': '아이디'})
        self.fields['password1'].label = '비밀번호'
        self.fields['password1'].widget.attrs.update({'class': 'form-control', 'placeholder': '비밀번호'})
        self.fields['password2'].label = '비밀번호 확인'
        self.fields['password2'].widget.attrs.update({'class': 'form-control', 'placeholder': '비밀번호 확인'})


class FarmForm(forms.ModelForm):
    region = forms.ChoiceField(
        choices=REGION_CHOICES,
        label='지역',
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_region'}),
    )

    class Meta:
        model = Farm
        fields = ('name', 'crop_type', 'region', 'planting_date')
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '예) 우리 농장',
            }),
            'crop_type': forms.Select(attrs={
                'class': 'form-select',
                'id': 'id_crop_type',
            }),
            'planting_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
        }
        labels = {
            'name':          '농장명',
            'crop_type':     '작물',
            'planting_date': '정식일 (선택)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['crop_type'].choices = [('', '--- 작물 선택 ---')] + list(Farm.CROP_CHOICES)

    def clean(self):
        cleaned   = super().clean()
        crop_type = cleaned.get('crop_type')
        allowed   = [c[0] for c in Farm.CROP_CHOICES]
        if crop_type and crop_type not in allowed:
            self.add_error('crop_type', '지원하지 않는 작물입니다.')
        return cleaned

    def save(self, commit=True):
        farm     = super().save(commit=False)
        region   = self.cleaned_data['region']
        nx, ny   = NX_NY_TABLE.get(region, (60, 127))
        mid_code = MID_LAND_CODE.get(region, '11B00000')
        farm.region_code = region
        farm.nx          = nx
        farm.ny          = ny
        farm.mid_code    = mid_code
        if commit:
            farm.save()
        return farm

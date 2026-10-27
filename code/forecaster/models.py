from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
import uuid
from django.core.exceptions import ValidationError
from accounts.models import Farm


class PriceData(models.Model):
    """KAMIS에서 수집한 과거 도매가격 데이터"""
    farm       = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='price_data')
    date       = models.DateField()
    price      = models.FloatField(help_text='원/kg')
    fetched_at = models.DateTimeField(auto_now=True)
    # 기존 자료에는 가격 기준을 추측해서 채우지 않는다.
    provenance = models.JSONField(default=dict, blank=True, help_text='KAMIS 요청 기준과 응답 품목 정보')

    class Meta:
        ordering = ['date']
        unique_together = ['farm', 'date']  # 같은 날짜 중복 저장 방지
        verbose_name = '가격 데이터'
        verbose_name_plural = '가격 데이터'

    def __str__(self):
        return f"{self.farm.name} | {self.date} | {self.price:.0f}원/kg"


class PriceForecast(models.Model):
    """TimesFM으로 예측한 미래 가격"""
    farm        = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='price_forecasts')
    forecast_at = models.DateField(help_text='예측 대상 날짜')
    price       = models.FloatField(help_text='예상 가격 원/kg')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['forecast_at']
        unique_together = ['farm', 'forecast_at']
        verbose_name = '가격 예측'
        verbose_name_plural = '가격 예측'

    def __str__(self):
        return f"{self.farm.name} | {self.forecast_at} | 예상 {self.price:.0f}원/kg"


class MarketSnapshot(models.Model):
    """인증정보 없는 aT 정산 스냅샷. KAMIS 농장 가격과 분리한 공통 자료."""
    checksum = models.CharField(max_length=64, unique=True)
    crop_type = models.CharField(max_length=20, db_index=True)
    crop_name = models.CharField(max_length=60)
    collected_at = models.DateTimeField()
    imported_at = models.DateTimeField(auto_now_add=True)
    source = models.URLField()
    source_name = models.CharField(max_length=200)
    coverage = models.JSONField(default=list)
    row_count = models.PositiveIntegerField(default=0)
    normalization_version = models.CharField(max_length=40)


class MarketSettlement(models.Model):
    snapshot = models.ForeignKey(MarketSnapshot, on_delete=models.CASCADE, related_name='settlements')
    fingerprint = models.CharField(max_length=64)
    settlement_date = models.DateField(db_index=True)
    market_code = models.CharField(max_length=30, db_index=True)
    market_name = models.CharField(max_length=100)
    product_key = models.CharField(max_length=80)
    variety_name = models.CharField(max_length=100)
    # 코드와 표시명·확인 상태를 함께 보존한다. 미확인 값을 다른 품질로 채우지 않는다.
    dimensions = models.JSONField(default=dict)
    unit_weight = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    average_price = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    total_amount = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    total_quantity = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    minimum_price = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    maximum_price = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    quality_flags = models.JSONField(default=list)
    raw = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['snapshot', 'fingerprint'], name='unique_market_snapshot_row')]
        ordering = ['market_name', 'product_key', 'fingerprint']


class ForecastRun(models.Model):
    """POST로 발행한 당시의 전망·입력·그래프. 이전 실행을 덮어쓰지 않는다."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='forecast_runs')
    crop_type = models.CharField(max_length=20)
    issued_at = models.DateTimeField()
    as_of = models.DateField()
    target_date = models.DateField()
    status = models.CharField(max_length=20, choices=[('success', '전망 있음'), ('unavailable', '전망 불가')])
    model_id = models.CharField(max_length=100, blank=True)
    input_sha256 = models.CharField(max_length=64, blank=True)
    checksum = models.CharField(max_length=64, unique=True)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ['-issued_at', '-pk']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('발행한 전망은 수정할 수 없습니다. 새 전망을 발행해주세요.')
        return super().save(*args, **kwargs)

    @property
    def crop_label(self):
        return dict(Farm.CROP_CHOICES).get(self.crop_type, self.crop_type)


class RevenuePlan(models.Model):
    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='revenue_plans')
    title = models.CharField('계산 이름', max_length=100)
    forecast_run = models.ForeignKey(ForecastRun, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='revenue_plans')
    harvest_date = models.DateField('수확 예정일')
    harvest_schedule = models.ForeignKey('calendar_app.CropSchedule', on_delete=models.SET_NULL,
                                         null=True, blank=True, verbose_name='연결할 수확 일정')
    harvest_kg = models.DecimalField('예상 수확량 (kg)', max_digits=12, decimal_places=3,
                                    validators=[MinValueValidator(Decimal('0.001'))])
    saleable_percent = models.DecimalField('판매가능 비율 (%)', max_digits=5, decimal_places=2,
                                          default=100, validators=[MinValueValidator(0), MaxValueValidator(100)])
    production_cost = models.DecimalField('이번 수확물의 전체 생산비 (원)', max_digits=14, decimal_places=0,
                                         null=True, blank=True, validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class SalesChannelEstimate(models.Model):
    CHANNEL_TYPES = [('wholesale', '도매시장'), ('direct', '직거래'), ('contract', '계약판매'), ('other', '기타 판로')]
    PRICE_SOURCES = [('manual', '직접 입력한 가정'), ('quote', '받은 견적'), ('contract', '계약단가'),
                     ('market_reference', '시장 정산자료를 참고한 가정')]
    TRANSPORT_MODES = [('hired', '위탁 운송'), ('self', '자가 운송'), ('pickup', '거래처 수거·운송비 부담 없음')]
    plan = models.ForeignKey(RevenuePlan, on_delete=models.CASCADE, related_name='channels')
    name = models.CharField('판로 이름', max_length=100)
    channel_type = models.CharField('판매 방식', choices=CHANNEL_TYPES, max_length=20, default='wholesale')
    price_per_kg = models.DecimalField('예상 판매단가 (원/kg)', max_digits=12, decimal_places=2,
                                      validators=[MinValueValidator(0)])
    price_source = models.CharField('단가 근거', choices=PRICE_SOURCES, max_length=30, default='manual')
    price_note = models.CharField('견적·계약·가격 가정 설명', max_length=250)
    reference_settlement = models.ForeignKey(MarketSettlement, on_delete=models.SET_NULL, null=True, blank=True)
    reference_snapshot = models.JSONField(default=dict, blank=True)
    transport_mode = models.CharField('운송 방식', choices=TRANSPORT_MODES, max_length=20, default='hired')
    freight_cost = models.DecimalField('위탁 운송비 총액 (원)', max_digits=14, decimal_places=0,
                                      default=0, validators=[MinValueValidator(0)])
    round_trip_km = models.DecimalField('왕복 총 이동거리 (km)', max_digits=10, decimal_places=2,
                                       default=0, validators=[MinValueValidator(0)])
    fuel_efficiency = models.DecimalField('차량 연비 (km/L)', max_digits=8, decimal_places=2,
                                         default=0, validators=[MinValueValidator(0)])
    fuel_price = models.DecimalField('연료 가격 (원/L)', max_digits=10, decimal_places=2,
                                    default=0, validators=[MinValueValidator(0)])
    toll_cost = models.DecimalField('자가 운송 통행료 총액 (원)', max_digits=14, decimal_places=0,
                                   default=0, validators=[MinValueValidator(0)])
    packing_per_kg = models.DecimalField('포장비 (원/판매가능 kg)', max_digits=12, decimal_places=2,
                                        default=0, validators=[MinValueValidator(0)])
    commission_percent = models.DecimalField('판매금액 기준 수수료 (%)', max_digits=5, decimal_places=2,
                                            default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    other_cost = models.DecimalField('기타 출하비용 총액 (원)', max_digits=14, decimal_places=0,
                                    default=0, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ['pk']

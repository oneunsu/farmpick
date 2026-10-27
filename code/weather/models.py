from django.db import models
from accounts.models import Farm


class WeatherObservation(models.Model):
    """기상청 과거 관측 데이터 (TimesFM 학습용)"""
    farm        = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='weather_obs')
    observed_at = models.DateField(verbose_name='관측일')
    temp_max    = models.FloatField(null=True, verbose_name='최고기온')
    temp_min    = models.FloatField(null=True, verbose_name='최저기온')
    rain_mm     = models.FloatField(null=True, verbose_name='강수량')
    humidity    = models.FloatField(null=True, verbose_name='습도')
    wind_speed  = models.FloatField(null=True, verbose_name='풍속')

    class Meta:
        unique_together = ['farm', 'observed_at']
        ordering = ['observed_at']


class WeatherForecast(models.Model):
    """기상청 예보 데이터 (단기 + 중기 통합)"""
    SOURCE_CHOICES = [('short', '단기예보'), ('mid', '중기예보')]

    farm           = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='forecasts')
    forecast_date  = models.DateField(verbose_name='예보일')
    source         = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    temp_max       = models.FloatField(null=True, verbose_name='최고기온')
    temp_min       = models.FloatField(null=True, verbose_name='최저기온')
    rain_prob      = models.FloatField(null=True, verbose_name='강수확률')
    rain_mm        = models.FloatField(null=True, verbose_name='강수량')
    humidity       = models.FloatField(null=True, verbose_name='습도')
    wind_speed     = models.FloatField(null=True, verbose_name='풍속')
    sky_condition  = models.IntegerField(null=True, verbose_name='하늘상태')
    fetched_at     = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['farm', 'forecast_date', 'source']
        ordering = ['forecast_date']


class RiskIndex(models.Model):
    """기존 위험지수 보존용 모델. 제출 기능에서는 계산·조회·노출하지 않는다."""
    LEVEL_CHOICES = [
        (1, '낮음'),
        (2, '보통'),
        (3, '높음'),
        (4, '매우높음'),
    ]
    SOURCE_CHOICES = [
        ('forecast', '예보 기반'),
    ]

    farm              = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='risk_indices')
    date              = models.DateField(verbose_name='날짜')
    source            = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='forecast')

    # 5가지 위험지수 (1~4 레벨)
    pesticide_risk    = models.IntegerField(choices=LEVEL_CHOICES, default=1, verbose_name='방제 가능 지수')
    heat_stress       = models.IntegerField(choices=LEVEL_CHOICES, default=1, verbose_name='고온 스트레스')
    cold_risk         = models.IntegerField(choices=LEVEL_CHOICES, default=1, verbose_name='저온 냉해 위험')
    disease_risk      = models.IntegerField(choices=LEVEL_CHOICES, default=1, verbose_name='병해 발생 위험')
    irrigation_need   = models.IntegerField(choices=LEVEL_CHOICES, default=1, verbose_name='관수 필요도')

    # 종합 위험도
    overall_risk      = models.IntegerField(choices=LEVEL_CHOICES, default=1, verbose_name='종합 위험도')
    best_work_day     = models.BooleanField(default=False, verbose_name='최적 작업일')

    calculated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['farm', 'date', 'source']
        ordering = ['date']

    def __str__(self):
        return f"{self.farm.name} - {self.date} ({self.get_source_display()})"

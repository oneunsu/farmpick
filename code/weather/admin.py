from django.contrib import admin
from .models import WeatherForecast, WeatherObservation


@admin.register(WeatherForecast)
class WeatherForecastAdmin(admin.ModelAdmin):
    list_display  = ('farm', 'forecast_date', 'source', 'temp_max', 'temp_min',
                     'rain_prob', 'rain_mm', 'humidity', 'wind_speed', 'fetched_at')
    list_filter   = ('source', 'farm')
    search_fields = ('farm__name',)
    ordering      = ('-forecast_date',)


@admin.register(WeatherObservation)
class WeatherObservationAdmin(admin.ModelAdmin):
    list_display  = ('farm', 'observed_at', 'temp_max', 'temp_min', 'rain_mm',
                     'humidity', 'wind_speed')
    list_filter   = ('farm',)
    ordering      = ('-observed_at',)

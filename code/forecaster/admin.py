from django.contrib import admin
from .models import PriceData, PriceForecast


@admin.register(PriceData)
class PriceDataAdmin(admin.ModelAdmin):
    list_display  = ('farm', 'date', 'price', 'fetched_at')
    list_filter   = ('farm__crop_type',)
    ordering      = ('-date',)
    search_fields = ('farm__name',)


@admin.register(PriceForecast)
class PriceForecastAdmin(admin.ModelAdmin):
    list_display  = ('farm', 'forecast_at', 'price', 'created_at')
    list_filter   = ('farm__crop_type',)
    ordering      = ('forecast_at',)
    search_fields = ('farm__name',)
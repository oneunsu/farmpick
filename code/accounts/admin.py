from django.contrib import admin
from .models import Farm


@admin.register(Farm)
class FarmAdmin(admin.ModelAdmin):
    list_display  = ('name', 'user', 'crop_type', 'region_code', 'nx', 'ny', 'planting_date', 'created_at')
    list_filter   = ('crop_type', 'region_code')
    search_fields = ('name', 'user__username')

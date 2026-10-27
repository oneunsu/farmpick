from django.contrib import admin
from .models import CropSchedule


@admin.register(CropSchedule)
class CropScheduleAdmin(admin.ModelAdmin):
    list_display  = ('farm', 'task_name', 'task_type', 'scheduled_at', 'is_done', 'created_at')
    list_filter   = ('task_type', 'is_done', 'farm')
    search_fields = ('task_name', 'farm__name')
    ordering      = ('scheduled_at',)
    list_editable = ('is_done',)

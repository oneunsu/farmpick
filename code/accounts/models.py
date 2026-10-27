from django.contrib.auth.models import User
from django.db import models


class Farm(models.Model):
    CROP_CHOICES = [
        ('tomato',     '토마토'),
        ('strawberry', '딸기'),
        ('paprika',    '파프리카'),
        ('cucumber',   '오이'),
    ]

    user          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='farms')
    name          = models.CharField(max_length=100, verbose_name='농장명')
    crop_type     = models.CharField(max_length=20, choices=CROP_CHOICES, verbose_name='작물')
    region_code   = models.CharField(max_length=50, verbose_name='지역명')
    nx            = models.IntegerField(verbose_name='기상청 격자 X')
    ny            = models.IntegerField(verbose_name='기상청 격자 Y')
    mid_code      = models.CharField(max_length=20, verbose_name='중기예보 코드')
    planting_date = models.DateField(null=True, blank=True, verbose_name='정식일')
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = '농장'
        verbose_name_plural = '농장'

    def __str__(self):
        return f"{self.name} ({self.get_crop_type_display()})"

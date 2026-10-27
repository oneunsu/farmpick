from django.db import models
from accounts.models import Farm


class CropSchedule(models.Model):
    TASK_CHOICES = [
        ('planting',   '정식'),
        ('pesticide',  '방제'),
        ('irrigation', '관수'),
        ('fertilizer', '시비'),
        ('pruning',    '적심/전정'),
        ('harvest',    '수확'),
        ('inspection', '생육 점검'),
        ('ventilation','환기'),       # 시설 전용
        ('heating',    '가온'),       # 시설 전용
    ]

    farm         = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name='schedules')
    task_type    = models.CharField(max_length=20, choices=TASK_CHOICES)
    task_name    = models.CharField(max_length=100)
    scheduled_at = models.DateField()
    is_done      = models.BooleanField(default=False)
    note         = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['scheduled_at']
        verbose_name        = '영농 일정'
        verbose_name_plural = '영농 일정'

    def __str__(self):
        return f"{self.farm.name} | {self.scheduled_at} | {self.task_name}"


TASK_COLORS = {
    'planting':   '#3498DB',
    'pesticide':  '#E74C3C',
    'irrigation': '#2ECC71',
    'fertilizer': '#F39C12',
    'pruning':    '#9B59B6',
    'harvest':    '#1ABC9C',
    'inspection': '#95A5A6',
    'ventilation':'#5DADE2',  # 시설 전용 - 하늘색
    'heating':    '#E59866',  # 시설 전용 - 주황갈색
}



# 정식 후 수확 시작·종료까지의 기존 작물별 참고 일수.
# 실제 수확일은 재배 조건에 따라 달라지므로 사용자가 일정에서 조정한다.
HARVEST_OFFSETS = {
    'paprika': (80, 300),
    'tomato': (75, 210),
    'strawberry': (75, 240),
    'cucumber': (40, 120),
}


def get_schedule_template(crop_type: str) -> list:
    """자동 생성은 정식·수확에 한정한다. 수동 작업 유형은 유지한다."""
    offsets = HARVEST_OFFSETS.get(crop_type)
    if offsets is None:
        return []
    return [
        {'task_type': 'planting', 'task_name': '정식', 'offset_days': 0},
        {'task_type': 'harvest', 'task_name': '수확 시작', 'offset_days': offsets[0]},
        {'task_type': 'harvest', 'task_name': '수확 종료', 'offset_days': offsets[1]},
    ]

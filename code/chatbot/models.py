from django.db import models
from django.contrib.auth.models import User
from accounts.models import Farm


class ChatMessage(models.Model):
    """제거된 챗봇의 기존 메시지 보존용 모델. 서비스에서는 사용하지 않는다."""
    ROLE_CHOICES = [
        ('user',      '사용자'),
        ('assistant', 'AI'),
    ]

    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_messages')
    farm       = models.ForeignKey(Farm, on_delete=models.CASCADE, null=True, blank=True)
    role       = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = '채팅 메시지'
        verbose_name_plural = '채팅 메시지'

    def __str__(self):
        return f"[{self.role}] {self.content[:30]}"

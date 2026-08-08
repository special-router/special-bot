from django.db import models


class MonitorState(models.Model):
    layer = models.CharField(max_length=8, unique=True)
    last_ok = models.BooleanField(default=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    alert = models.BooleanField(default=False)
    error_class = models.CharField(max_length=64, blank=True)
    details = models.JSONField(default=dict, blank=True)
    checked_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Monitoring state'
        verbose_name_plural = 'Monitoring states'

    def __str__(self):
        return f'{self.layer}: {"ok" if self.last_ok else "failed"}'


class MonitorTransition(models.Model):
    layer = models.CharField(max_length=8)
    event = models.CharField(max_length=16)
    error_class = models.CharField(max_length=64, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'Monitoring transition'
        verbose_name_plural = 'Monitoring transitions'

    def __str__(self):
        return f'{self.layer}: {self.event}'

from django.db import models


class ServerQuerySet(models.QuerySet):
    def with_related_tariffs(self):
        return self.select_related('tariff')

    def order_by_random(self):
        return self.order_by('?')

from typing import Self

from django.db.models import Prefetch, QuerySet


class UserVPNQuerySet(QuerySet):
    def with_related_user(self, queryset=None) -> Self:
        if queryset is None:
            return self.select_related('user')
        return self.prefetch_related(Prefetch('user', queryset=queryset))

    def with_related_server(self) -> Self:
        return self.select_related('server')

    def filter_by_user(self, user_id: int) -> Self:
        return self.filter(user_id=user_id)

    def filter_by_id(self, user_vpn_id: int) -> Self:
        return self.filter(id=user_vpn_id)

    def filter_by_server(self, server_id: int) -> Self:
        return self.filter(server_id=server_id)

    def filter_by_enabled(self, enabled: bool = True) -> Self:
        return self.filter(enabled=enabled)

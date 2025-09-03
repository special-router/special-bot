from typing import Self

from django.db.models import QuerySet


class UserVPNQuerySet(QuerySet):
    def with_related_user(self) -> Self:
        return self.select_related('user')

    def with_related_server(self) -> Self:
        return self.select_related('server')

    def filter_by_user(self, user_id: int) -> Self:
        return self.filter(user_id=user_id)

    def filter_by_server(self, server_id: int) -> Self:
        return self.filter(server_id=server_id)

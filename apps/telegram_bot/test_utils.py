from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.utils import get_user


class TelegramUserFallbackTests(IsolatedAsyncioTestCase):
    @patch('apps.telegram_bot.utils.TelegramUser.objects')
    async def test_get_user_uses_non_null_fallback_when_telegram_username_is_missing(self, objects):
        from_user = SimpleNamespace(id=12345, username=None)
        update = SimpleNamespace(callback_query=None, message=SimpleNamespace(from_user=from_user))
        created_user = SimpleNamespace(telegram_id=12345)
        hydrated_user = SimpleNamespace(telegram_id=12345)
        objects.aget_or_create = AsyncMock(return_value=(created_user, True))
        objects.annotate_balance.return_value.with_related_referral_user.return_value.aget = AsyncMock(
            return_value=hydrated_user
        )

        result = await get_user(update)

        self.assertIs(result, hydrated_user)
        objects.aget_or_create.assert_awaited_once_with(
            telegram_id=12345,
            defaults={'username': 'user_12345', 'referral_user': None},
        )

    @patch('apps.telegram_bot.utils.TelegramUser.objects')
    async def test_get_user_preserves_telegram_username(self, objects):
        from_user = SimpleNamespace(id=12346, username='real_name')
        update = SimpleNamespace(callback_query=None, message=SimpleNamespace(from_user=from_user))
        user = SimpleNamespace(telegram_id=12346)
        objects.aget_or_create = AsyncMock(return_value=(user, False))
        objects.annotate_balance.return_value.with_related_referral_user.return_value.aget = AsyncMock(
            return_value=user
        )

        await get_user(update)

        objects.aget_or_create.assert_awaited_once_with(
            telegram_id=12346,
            defaults={'username': 'real_name', 'referral_user': None},
        )

    @patch('apps.telegram_bot.utils.TelegramUser.objects')
    async def test_get_user_uses_callback_query_user(self, objects):
        from_user = SimpleNamespace(id=12347, username=None)
        update = SimpleNamespace(callback_query=SimpleNamespace(from_user=from_user), message=None)
        user = SimpleNamespace(telegram_id=12347)
        objects.aget_or_create = AsyncMock(return_value=(user, True))
        objects.annotate_balance.return_value.with_related_referral_user.return_value.aget = AsyncMock(
            return_value=user
        )

        await get_user(update)

        objects.aget_or_create.assert_awaited_once_with(
            telegram_id=12347,
            defaults={'username': 'user_12347', 'referral_user': None},
        )

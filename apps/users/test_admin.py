"""Карточка клиента в админке и то, что делает её пригодной для поддержки.

Оператор приходит сюда из темы обращения с одним вопросом: что происходит у
этого человека. Поэтому проверяется, что на одной странице видны баланс с
разбивкой, подписки с числом привязанных устройств и последние операции, что
предъявительских идентификаторов на ней нет, и что страница вообще рисуется со
стилями — при `DEBUG=False` статику отдаёт приложение, и без неё админка
открывается голым HTML.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.payments.choices import TransactionSourceChoices, TransactionStatusChoices
from apps.payments.models import Transaction
from apps.servers.models import Server, TariffServer
from apps.subscriptions.models import SubscriptionDevice
from apps.users.admin import TelegramUserAdmin
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN


def _server(name='Нидерланды', price='7.00'):
    tariff = TariffServer.objects.create(name='Базовый', price=Decimal(price))
    return Server.objects.create(
        name=name,
        ip_address='127.0.0.1',
        ssh_username='user',
        ssh_password='password',
        vpn_username='panel',
        vpn_password='panel',
        vpn_key='key',
        tariff=tariff,
    )


class TelegramUserAdminPageTests(TestCase):
    """Что оператор видит и что может поправить на карточке клиента."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser('operator', 'operator@example.test', 'password'))

        self.user = TelegramUser.objects.create(telegram_id=1001, username='client')
        self.subscription = UserVPN.objects.create(user=self.user, server=_server(), enabled=True)
        SubscriptionDevice.objects.create(subscription=self.subscription, hwid='hwid-1')
        SubscriptionDevice.objects.create(subscription=self.subscription, hwid='hwid-2')
        Transaction.objects.create(
            user=self.user,
            amount=Decimal('120.00'),
            status=TransactionStatusChoices.SUCCESS,
            source=TransactionSourceChoices.YOUMONEY,
        )

    def _page(self):
        response = self.client.get(reverse('admin:users_telegramuser_change', args=[self.user.id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_page_shows_the_balance_with_the_real_and_bonus_split(self):
        content = self._page()

        self.assertIn('Реальные деньги', content)
        self.assertIn('Бонусные', content)

    def test_the_page_shows_subscriptions_with_their_device_counts(self):
        content = self._page()

        self.assertIn('Нидерланды', content)
        self.assertIn('Базовый', content)
        self.assertIn('Активна', content)
        self.assertIn('устройств: 2 из 2', content)

    def test_the_page_shows_recent_transactions(self):
        content = self._page()

        self.assertIn('120.00', content)
        self.assertIn('Юмани', content)
        self.assertIn('Успешно', content)

    def test_the_page_never_shows_the_client_uuid(self):
        """UUID — предъявительские данные, а карточку открывают при клиенте."""
        content = self._page()

        self.assertNotIn(str(self.subscription.vpn_uuid), content)

    def test_the_promo_flag_stays_editable(self):
        content = self._page()

        self.assertIn('name="is_active_promo"', content)

    def test_the_telegram_id_is_editable_only_while_the_customer_is_new(self):
        """Правка у существующего адресует баланс и подписки другому человеку."""
        admin = TelegramUserAdmin(TelegramUser, AdminSite())
        request = RequestFactory().get('/')

        self.assertIn('telegram_id', admin.get_readonly_fields(request, obj=self.user))
        self.assertNotIn('telegram_id', admin.get_readonly_fields(request, obj=None))

    def test_the_ledger_is_appendable_but_not_rewritable(self):
        """Баланс — сумма всех строк, поэтому правка старой меняет и историю."""
        inline = TelegramUserAdmin(TelegramUser, AdminSite()).inlines[0]

        self.assertFalse(inline.can_delete)
        self.assertIn('created_at', inline.readonly_fields)
        self.assertIn('amount', inline.fields)


class AdminStaticFilesTests(TestCase):
    """Админка при `DEBUG=False` обязана открываться со стилями.

    nginx проксирует `/static/` в приложение и до файлов внутри контейнера не
    достаёт, поэтому отдавать их должно само приложение. Проверяется связка, а
    не наличие собранных файлов: `collectstatic` выполняется при сборке образа,
    и в чистом чекауте каталога `staticfiles` ещё нет.
    """

    def test_the_login_page_renders_and_asks_for_admin_styles(self):
        response = self.client.get(reverse('admin:login'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('/static/admin/css/', response.content.decode())

    def test_the_application_itself_serves_static(self):
        middleware = list(settings.MIDDLEWARE)

        self.assertIn('whitenoise.middleware.WhiteNoiseMiddleware', middleware)
        self.assertEqual(
            middleware.index('whitenoise.middleware.WhiteNoiseMiddleware'),
            middleware.index('django.middleware.security.SecurityMiddleware') + 1,
        )
        self.assertEqual(
            settings.STORAGES['staticfiles']['BACKEND'],
            'whitenoise.storage.CompressedStaticFilesStorage',
        )

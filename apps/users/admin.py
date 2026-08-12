"""Карточка клиента — то, что оператор поддержки открывает из темы обращения.

Страница собрана под один вопрос: «что происходит у этого человека и что можно
поправить прямо сейчас». Поэтому баланс, подписки с числом привязанных
устройств и последние движения по счёту лежат на ней, а не в трёх соседних
разделах. Ключей доступа здесь нет: ни UUID клиента, ни ссылки на подписку —
они предъявительские, и админка не то место, где их показывают мимоходом.

Правится только то, где ошибка стоит дёшево и обратима. `enabled` у подписки не
правится: строка в базе и клиент в 3x-ui расходятся молча, а обратно их сводит
только суточная задача.
"""

import asyncio

from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join

from apps.analytics.balance_split import split_balance
from apps.payments.models import Transaction
from apps.servers.models import Server
from apps.subscriptions.devices import device_limit_for
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user

from .models import TelegramUser


# Хватает, чтобы увидеть последнее пополнение и несколько суточных списаний.
RECENT_TRANSACTIONS = 10


class TransactionInline(admin.TabularInline):
    """Журнал операций: дописывать можно, переписывать — нет.

    Баланс считается суммой всех строк, поэтому правка старой суммы меняет и
    сегодняшний баланс, и всю историю, которая на него ссылается. Начисление
    оформляется новой строкой — так же, как это делает бот.
    """

    model = Transaction
    fk_name = 'user'
    extra = 0
    can_delete = False
    ordering = ['-created_at']
    fields = ['created_at', 'amount', 'status', 'source', 'from_referral_user']
    readonly_fields = ['created_at']
    verbose_name = 'Операция'
    verbose_name_plural = 'Операции по счёту'

    def get_queryset(self, request):
        return super().get_queryset(request).order_by('-created_at')


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ['telegram_id', 'username', 'is_active_promo', 'vpn_count', 'created_at']
    list_filter = ['is_active_promo', 'created_at']
    search_fields = ['telegram_id', 'username']
    readonly_fields = [
        'created_at',
        'updated_at',
        'subscriptions_overview',
        'balance_split',
        'recent_transactions',
    ]
    inlines = [TransactionInline]

    fieldsets = (
        ('Основная информация', {'fields': ('telegram_id', 'username')}),
        ('Баланс', {'fields': ('balance_split', 'recent_transactions')}),
        ('Реферальная система', {'fields': ('referral_user', 'is_active_promo')}),
        ('Подписки и устройства', {'fields': ('subscriptions_overview',)}),
        ('Системная информация', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['add_vpn_to_selected_users']

    def get_readonly_fields(self, request, obj=None):
        """`telegram_id` правится только при заведении строки.

        Он связывает аккаунт с чатом в Telegram: правка существующего адресует
        баланс и подписки другому человеку, и заметно это станет не сразу.
        """
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly.append('telegram_id')
        return readonly

    def vpn_count(self, obj):
        """Количество VPN подключений пользователя"""
        count = obj.vpn.count()
        if count > 0:
            return format_html('<span style="color: green; font-weight: bold;">{}</span>', count)
        return format_html('<span style="color: gray;">{}</span>', count)

    vpn_count.short_description = 'VPN подключений'

    def balance_split(self, obj):
        """Итог, реальные деньги и подаренный баланс одного аккаунта.

        Итог — то же число, что показывает бот и по которому идёт списание;
        слагаемые считаются по журналу таксономией, отдельного хранилища у них
        нет. Начисления руками попадают в бонус целиком: заплатил ли человек
        мимо провайдера, история не хранит.
        """
        split = split_balance(obj.id)
        return format_html(
            'Итого: <b>{}</b> руб.<br>Реальные деньги: {} руб.<br>Бонусные: {} руб.',
            split.total,
            split.real,
            split.bonus,
        )

    balance_split.short_description = 'Баланс: всего / деньги / бонусы'

    def subscriptions_overview(self, obj):
        """Подписки клиента: тариф, состояние и сколько устройств привязано.

        Число устройств — первое, что спрашивает поддержка на «не подключается с
        нового телефона», и оно же объясняет отказ подписки без единого лога.
        Идентификаторов клиента здесь нет: они предъявительские.
        """
        subscriptions = obj.vpn.select_related('server', 'server__tariff').prefetch_related('devices')
        if not subscriptions:
            return 'Нет подписок'

        rows = [
            (
                subscription.server.name,
                subscription.server.tariff.name,
                subscription.server.tariff.price,
                'Активна' if subscription.enabled else 'Отключена',
                'green' if subscription.enabled else 'red',
                len(subscription.devices.all()),
                device_limit_for(subscription),
            )
            for subscription in subscriptions
        ]

        return format_html(
            '<ul>{}</ul>',
            format_html_join(
                '',
                '<li><strong>{0}</strong> — тариф {1}, {2} руб./сут.<br>'
                '<span style="color: {4};">{3}</span>, устройств: {5} из {6}</li>',
                rows,
            ),
        )

    subscriptions_overview.short_description = 'Подписки и устройства'

    def recent_transactions(self, obj):
        """Последние движения по счёту — чем именно сложился текущий баланс."""
        transactions = obj.transactions.order_by('-created_at')[:RECENT_TRANSACTIONS]
        if not transactions:
            return 'Операций нет'

        return format_html(
            '<ul>{}</ul>',
            format_html_join(
                '',
                '<li>{} — <strong>{}</strong> руб., {}, {}</li>',
                (
                    (
                        transaction.created_at.strftime('%d.%m.%Y %H:%M'),
                        transaction.amount,
                        transaction.get_source_display(),
                        transaction.get_status_display(),
                    )
                    for transaction in transactions
                ),
            ),
        )

    recent_transactions.short_description = f'Последние операции (до {RECENT_TRANSACTIONS})'

    def add_vpn_to_selected_users(self, request, queryset):
        """Действие для добавления VPN выбранным пользователям"""
        # Получаем ID сервера из параметров запроса
        server_id = request.GET.get('server_id')

        if not server_id:
            # Показываем список серверов для выбора
            servers = Server.objects.all()
            if not servers:
                self.message_user(request, 'Нет доступных серверов для создания VPN подключений', level=messages.ERROR)
                return

            # Создаем простой список серверов
            server_list = []
            for server in servers:
                server_list.append(f"{server.id}: {server.name}")

            self.message_user(
                request,
                'Выберите сервер для добавления VPN подключений:\n' + '\n'.join(server_list),
                level=messages.INFO,
            )
            return

        try:
            server = Server.objects.get(id=server_id)
        except Server.DoesNotExist:
            self.message_user(request, f'Сервер с ID {server_id} не найден', level=messages.ERROR)
            return

        # Добавляем VPN подключения
        success_count = 0
        error_count = 0
        errors = []

        for user in queryset:
            # Проверяем, есть ли уже VPN для этого пользователя на этом сервере
            existing_vpn = UserVPN.objects.filter(user=user, server=server).first()
            if existing_vpn:
                errors.append(f'Пользователь {user.username} уже имеет VPN на сервере {server.name}')
                error_count += 1
                continue

            # Запускаем асинхронную задачу для настройки VPN
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(add_vpn_to_user(user, server))
                loop.close()
                success_count += 1
            except Exception as e:
                errors.append(f'Ошибка настройки VPN для {user.username}: {str(e)}')
                error_count += 1

        # Показываем результаты
        if success_count > 0:
            self.message_user(request, f'Успешно добавлено VPN подключений: {success_count}', level=messages.SUCCESS)

        if error_count > 0:
            error_message = f'Ошибок: {error_count}\n' + '\n'.join(errors[:10])
            if len(errors) > 10:
                error_message += f'\n... и еще {len(errors) - 10} ошибок'

            self.message_user(request, error_message, level=messages.ERROR)

    add_vpn_to_selected_users.short_description = 'Добавить VPN подключения выбранным пользователям'

    def get_actions(self, request):
        """Динамически добавляем действия для каждого сервера"""
        actions = super().get_actions(request)

        # Добавляем действия для каждого сервера
        for server in Server.objects.all():
            action_name = f'add_vpn_to_server_{server.id}'
            actions[action_name] = (self._add_vpn_to_server, action_name, f'Добавить VPN на сервер: {server.name}')

        return actions

    def _add_vpn_to_server(self, user, request, queryset):
        """Общий метод для добавления VPN на конкретный сервер"""
        # Извлекаем ID сервера из имени действия
        server_id = request.POST['action'].split('_')[-1]
        try:
            server = Server.objects.get(id=server_id)
        except Server.DoesNotExist:
            self.message_user(request, f'Сервер с ID {server_id} не найден', level=messages.ERROR)
            return

        # Добавляем VPN подключения
        success_count = 0
        error_count = 0
        errors = []

        for user in queryset:
            # Проверяем, есть ли уже VPN для этого пользователя на этом сервере
            existing_vpn = UserVPN.objects.filter(user=user, server=server).first()
            if existing_vpn:
                errors.append(f'Пользователь {user.username} уже имеет VPN на сервере {server.name}')
                error_count += 1
                continue

            # Запускаем асинхронную задачу для настройки VPN
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(add_vpn_to_user(user, server))
                loop.close()
                success_count += 1
            except Exception as e:
                errors.append(f'Ошибка настройки VPN для {user.username}: {str(e)}')
                error_count += 1

        # Показываем результаты
        if success_count > 0:
            self.message_user(
                request,
                f'Успешно добавлено VPN подключений на сервер {server.name}: {success_count}',
                level=messages.SUCCESS,
            )

        if error_count > 0:
            error_message = f'Ошибок: {error_count}\n' + '\n'.join(errors[:10])
            if len(errors) > 10:
                error_message += f'\n... и еще {len(errors) - 10} ошибок'

            self.message_user(request, error_message, level=messages.ERROR)

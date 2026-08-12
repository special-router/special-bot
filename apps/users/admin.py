import asyncio

from django.contrib import admin, messages
from django.utils.html import format_html

from apps.analytics.balance_split import split_balance
from apps.servers.models import Server
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user

from .models import TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ['telegram_id', 'username', 'is_active_promo', 'vpn_count', 'created_at']
    list_filter = ['is_active_promo', 'created_at']
    search_fields = ['telegram_id', 'username']
    readonly_fields = ['created_at', 'updated_at', 'vpn_connections', 'balance_split']

    fieldsets = (
        ('Основная информация', {'fields': ('telegram_id', 'username')}),
        ('Баланс', {'fields': ('balance_split',)}),
        ('Реферальная система', {'fields': ('referral_user', 'is_active_promo')}),
        ('VPN подключения', {'fields': ('vpn_connections',), 'classes': ('collapse',)}),
        ('Системная информация', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    actions = ['add_vpn_to_selected_users']

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

    def vpn_connections(self, obj):
        """Отображение VPN подключений пользователя"""
        vpns = obj.vpn.select_related('server').all()
        if not vpns:
            return 'Нет VPN подключений'

        html = '<ul>'
        for vpn in vpns:
            status_color = 'green' if vpn.enabled else 'red'
            status_text = 'Активно' if vpn.enabled else 'Отключено'
            html += f'''
                <li>
                    <strong>{vpn.server.name}</strong>
                    <span style="color: {status_color};">({status_text})</span>
                    <br>
                    <small>UUID: {vpn.vpn_uuid}</small>
                </li>
            '''
        html += '</ul>'
        return format_html(html)

    vpn_connections.short_description = 'VPN подключения'

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

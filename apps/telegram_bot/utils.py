from apps.users.models import TelegramUser
from telegram import Update


async def get_user(update: Update) -> TelegramUser:
    from_user = update.callback_query.from_user if update.callback_query else update.message.from_user

    user, _ = await TelegramUser.objects.aget_or_create(
    telegram_id= from_user.id,
    defaults={
        'username': from_user.username,
    },
    )

    user: TelegramUser = await TelegramUser.objects.annotate_balance().aget(telegram_id=user.telegram_id)

    return user

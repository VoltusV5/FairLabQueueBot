def is_user_superadmin(func):
    """Декоратор для проверки супер админ пользователь или нет.
    Применяется для функций, с которыми может работать только супер админ
    """
    async def wrapper(message: Message, *args, **kwargs):
        user_message = message.from_user
        if user_message is None or user_message.id not in config.bot.admin_ids:
            await message.reply("Недостаточно прав доступа, вы не супер админ")
            return
        return await func(message, *args, **kwargs)
    return wrapper


def is_user_admin(func):
    """Декоратор для проверки админ пользователь или нет.
    Применяется для функций, с которыми может работать только супер админ
    """
    async def wrapper(message: Message, *args, **kwargs):
        user = message.from_user
        chat_id = message.chat.id
        if user is None:
            return
        user_data = get_user(chat_id, user.id)
        # Если пользователь не админ и не супер админ, то у него нет доступа
        if (not user_data["is_admin"]
                and user_data["id"] not in config.bot.admin_ids):
            await message.reply("Недостаточно прав доступа, вы не админ")
            return
        return await func(message, *args, **kwargs)
    return wrapper


# Этот хэндлер срабатывает на команду /setadmin
@router.message(Command(commands='setadmin'))
@is_user_superadmin
async def process_setadmin_command(message: Message):
    """Позволяет супер админу назначать админов.
    Назначает пользователя админом из-за декоратора is_user_superadmin
    """
    pass
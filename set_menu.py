'''
Меню команд бота
'''

'''
from aiogram import Bot
from aiogram.types import BotCommand

from lexicon.lexicon import LEXICON_COMMANDS_RU


# Функция для настройки кнопки Menu бота
async def set_main_menu(bot: Bot):
    main_menu_commands = [
        BotCommand(
            command=command,
            description=description
        ) for command, description in LEXICON_COMMANDS_RU.items()
    ]
    await bot.set_my_commands(main_menu_commands)


# lexicon

LEXICON_COMMANDS_RU: dict[str, str] = {
    '/command_1': 'command_1 desription',
    '/command_2': 'command_2 desription',
    '/command_3': 'command_3 desription',
    '/command_4': 'command_4 desription'
}

'''

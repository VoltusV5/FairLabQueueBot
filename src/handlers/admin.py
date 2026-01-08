"""
Обработчики команд админа:
создание и закрытие очередей, перемешивание, сброс статистики, экспорт данных.
"""

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)

from src.lexicon import LEXICON_RU

# Инициализируем роутер уровня модуля
router = Router()

# Создаем объект инлайн-кнопок
btn_participate = InlineKeyboardButton(
    text="✅ Участвую", callback_data="confirm_participation"
)

# Создаем объект инлайн-клавиатуры
keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn_participate]])


# Этот хэндлер срабатывает на команду /start
@router.callback_query(CommandStart())
async def process_start_command(callback: CallbackQuery):
    '''Выводит сообщение, что бот запущен.'''

    await callback.answer(text=LEXICON_RU['/start'])


# Этот хэндлер срабатывает на команду /help
@router.callback_query(Command(commands='help'))
async def process_help_command(callback: CallbackQuery):
    '''Выводит информацию о командах бота, доступных админу.'''

    await callback.answer(text=LEXICON_RU['/help'], show_alert=True)


# Этот хэндлер срабатывает на команду /queue
@router.message(Command(commands='queue'))
async def process_queue_command(message: Message):
    '''Отправляет сообщение с записью в очередь'''

    await message.answer(
        text=LEXICON_RU['/queue'], reply_markup=keyboard
    )


# Убирает "часики", которые показывают, что кнопка не работает
@router.callback_query(F.data.in_(["confirm_participation"]))
async def process_buttons_click(callback: CallbackQuery):
    '''Записывает пользователя в очередь'''

    await callback.answer(text="Вы записаны ✅")

    '''Сделать проверку: если пользователь уже записан в очередь:
    сообщение "Вы уже записаны" и ничего не делать
    Если ещё не записан: сообщение "Вы записаны ✅" и записать в БД
    '''
    '''Переместить в файл student.py'''
    ...

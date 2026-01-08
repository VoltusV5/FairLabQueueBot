"""
Обработчики команд админа:
создание и закрытие очередей, перемешивание, сброс статистики, экспорт данных.
"""

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from src.lexicon import LEXICON_RU
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)

# Инициализируем роутер уровня модуля
router = Router()

# Создаем объект инлайн-кнопок
btn_participate = InlineKeyboardButton(
    text="✅ Участвую", callback_data="confirm_participation"
)
# Создаем объект инлайн-клавиатуры
keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn_participate]])


# Этот хэндлер срабатывает на команду /start
@router.message(CommandStart())
async def process_start_command(message: Message):
    await message.answer(text=LEXICON_RU['/start'])


# Этот хэндлер срабатывает на команду /help
@router.message(Command(commands='help'))
async def process_help_command(message: Message):
    await message.answer(text=LEXICON_RU['/help'])


# Этот хэндлер срабатывает на команду /queue
@router.message(Command(commands='queue'))
async def process_queue_command(message: Message):
    await message.answer(
        text=LEXICON_RU['/queue'], reply_markup=keyboard
    )


# Убирает "часики", которые показывают, что кнопка не работает
@router.callback_query(F.data.in_(["confirm_participation"]))
async def process_buttons_click(callback: CallbackQuery):
    await callback.answer(text="Вы записаны ✅")

    '''Сделать проверку: если пользователь уже записан в очередь:
    сообщение "Вы уже записаны" и ничего не делать
    Если ещё не записан: сообщение "Вы записаны ✅" и записать в БД
    '''
    ...

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
from ..db.db import get_db
from ..db.queries import (
    add_user_to_db, add_new_queue, add_new_subject, is_in_db,
    split_queue_command_message, get_subject_id, get_user, is_queue_in_db,
    change_realname, add_tgname_in_queue
)

from ..db.init_db import User, Subject, Queue
from src.services.queue_manager import get_queue
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from enum import Enum
from config import Config, load_config

import logging
from src.lexicon import LEXICON_RU

# Импортируем логгер
logger = logging.getLogger(__name__)

# Инициализируем роутер уровня модуля
router = Router()

# Создаем объект инлайн-кнопок
btn_participate = [
    InlineKeyboardButton(
        text="✅ Участвую", callback_data="confirm_participation"),
    InlineKeyboardButton(text="Завершить досрочно",
                         callback_data="close_queue")
]

# Кнопки для подтверждения завершения очереди
btn_confirm = [
    InlineKeyboardButton(
        text="Да, завершить",
        callback_data="really_close_queue"),
    InlineKeyboardButton(
        text="Нет, отмена",
        callback_data="back_to_queue")
]

# Создаем объект инлайн-клавиатуры
keyboard = InlineKeyboardMarkup(inline_keyboard=[btn_participate])

# Подгружаем Config
config: Config = load_config()


# Статусы для состояний Queue
class QueueStatus(Enum):
    """Статусы для состояний Queue"""

    WAITING_FOR_PARTICIPANTS = "waiting_for_participants"
    WAITING_FOR_LAST_PARTICIPANT = "waiting_for_last_participant"
    COMPLETED = "completed"


# Этот хэндлер срабатывает на команду /start
@router.message(CommandStart())
async def process_start_command(message: Message):
    """Выводит сообщение, что бот запущен."""
    await message.answer(text=LEXICON_RU['/start'])


# Этот хэндлер срабатывает на команду /help
@router.message(Command(commands='help'))
async def process_help_command(message: Message):
    """Выводит информацию о командах бота, доступных админу."""
    await message.answer(text=LEXICON_RU['/help'])


# Этот хэндлер срабатывает на команду /queue
@router.message(Command(commands='queue'))
async def process_queue_command(message: Message):
    """Отправляет сообщение с записью в очередь. Создаёт таблицу в БД Subject
    Создаёт таблицу Queue.
    """
    print(message.message_id)
    try:
        if message.text is None:
            raise ValueError("Отправлено пустое сообщение")

        # Разделение сообщения на предмет, дату, время
        subject_name, subject_date, subject_time = split_queue_command_message(
            message.text)
        chat_id = message.chat.id

        # Проверяем существует ли subject_name в БД
        if not is_in_db(subject_name, Subject, "subject_name"):
            # Заполняем БД subject
            add_new_subject(chat_id, subject_name)

        # Переменные для создания БД Queue
        subject_date_and_time = subject_date + ' ' + subject_time
        subject_id = get_subject_id(chat_id, subject_name)
        chat_id = message.chat.id
        # Сделал костыль не уверен что всегда будет работать, тк разные айдишники у сообшений на 1 отличаются
        message_id = message.message_id + 1
        lesson_date = datetime.strptime(
            subject_date_and_time, "%d.%m.%Y %H:%M")
        close_at = lesson_date - timedelta(hours=1)
        status = QueueStatus.WAITING_FOR_PARTICIPANTS
        usernames: list = list()

        # Проверка на повторное создание такой записи в БД
        existing_queue = is_queue_in_db(subject_id, chat_id, lesson_date)
        if existing_queue:
            await message.answer(
                text=LEXICON_RU["/queue_error_message_UniqueConstraint"])
            return

        # Создаём новую Queue в БД
        add_new_queue(
            subject_id, chat_id, message_id, lesson_date,
            close_at, status.value, usernames)

        # Кидаем сообщение
        await message.answer(
            text=f"📘 Запись на {subject_name}\n"
                 f"📅 {subject_date}\n"
                 f"⏰ {subject_time}",
            reply_markup=keyboard)
    except IntegrityError as error:
        # Сообщение пользователю об ошибке
        await message.answer(
            text=LEXICON_RU["/queue_error_message_UniqueConstraint"],
        )
        logger.error(f"UniqueConstraint queue {error}")
    except ValueError as error:
        # Сообщение пользователю об ошибке
        await message.answer(
            text=LEXICON_RU["/queue_error_message_ValueError"],
        )
        logger.error(f"ValueError queue {error}")

    except Exception as error:
        logger.error(f"Ошибка команды queue {error}")
        raise ValueError(f"Ошибка команды queue {error}")


# Убирает "часики", которые показывают, что кнопка не работает
@router.callback_query(F.data.in_(["confirm_participation"]))
async def process_buttons_click(callback: CallbackQuery):
    """Записывает пользователя в очередь при нажатии кнопки УЧАСТВУЮ."""
    try:
        # Проверка, чтобы сообщение было не пустым
        if callback.message is None:
            raise ValueError("Ошибка при проверке сообщения для голосования")
        # Переменные для БД
        tg_username = callback.from_user.username
        if tg_username is None:
            raise ValueError("tg_username не может быть None")
        chat_id = callback.message.chat.id
        real_name = callback.from_user.full_name
        user_id = callback.from_user.id
        message_id = callback.message.message_id

        # Если пользователя ещё нет в БД - добавить его
        if not is_in_db(user_id, User, "user_id"):
            add_user_to_db(tg_username, chat_id, user_id, real_name)
        else:
            logger.info("Пользователь уже есть в БД")

        # Проверка на существование записи об очереди в БД
        if not is_in_db(message_id, Queue, "message_id"):
            raise ValueError("Предмет не найден, ошибка в добавлении очереди")
        # Добавление пользователя в очередь
        if add_tgname_in_queue(callback.from_user.username, message_id) == -1:
            await callback.answer("Вы уже записаны!")
            logger.info("Пользователь уже есть в БД")
        else:
            await callback.answer(text="Вы записаны ✅")
            logger.info("Пользователь успешно добавлен в список")

    except Exception as error:
        logger.error(f"Ошибка при проверке пользователя {error}")
        raise ValueError(f"Ошибка при проверке пользователя {error}")

    """Сделать проверку: если пользователь уже записан в очередь:
    сообщение "Вы уже записаны" и ничего не делать
    Если ещё не записан: сообщение "Вы записаны ✅" и записать в БД
    """
    """Переместить в файл student.py"""


@router.callback_query(F.data.in_(["close_queue"]))
async def close_queue_start(callback: CallbackQuery):
    """Досрочное завершение очереди подтверждение действия"""
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)

    text = callback.message.text or ""  # Если None, то делаем пустую строку
    keyboard = InlineKeyboardMarkup(inline_keyboard=[btn_confirm])
    await callback.message.edit_text(
        text + "\n\n" + "Вы уверены, что хотите досрочно завершить очередь?",
        reply_markup=keyboard
    )


@router.callback_query(F.data.in_(["back_to_queue"]))
async def close_queue_discard(callback: CallbackQuery):
    """Досрочное завершение очереди подтверждение действия"""
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=False)
        return
    await callback.message.edit_reply_markup(reply_markup=None)

    text = callback.message.text or ""  # Если None, то делаем пустую строку
    splited_text = text.split("\n\n")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[btn_participate])
    await callback.message.edit_text(
        splited_text[0],  # Только информация об очереди
        reply_markup=keyboard
    )


@router.callback_query(F.data.in_(["really_close_queue"]))
async def process_buttons_click(callback: CallbackQuery):
    """Завершение досрочное только админам"""
    # Но фильтр позже добавим чтобы тестить было проще
    # Убираем кнопки
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    text = callback.message.text or ""
    # Разбил сообщение, чтобы сформировать сообщение с записанными людьми
    splited_message = text[text.find("на") + 3:].split("\n")
    # Забрал название предмета для кнопки, а также дату и время
    message_subject, message_date, message_time = splited_message[0:3]
    head = f"Список на {message_subject}\n{message_date}\n{message_time}\n\n"
    queue: str = head + get_queue(callback.message, message_subject)
    print(queue)
    await callback.message.edit_text(queue)
    tg_username = callback.from_user.username
    await callback.message.answer(
        f"Пользователь @{tg_username} завершил очередь досрочно")
    await callback.answer()


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


# Этот хэндлер срабатывает на команду /changename
@router.message(Command(commands='changename'))
async def process_changename_command(message: Message):
    """Пользователь может изменять real_name в БД.
    /changename @tg_username <новое имя>
    """
    try:
        user_command = message.text
        if user_command is None:
            return
        split_user_command = user_command.split()
        tg_username = split_user_command[1]
        new_realname = " ".join(split_user_command[2:])
        change_realname(tg_username, new_realname)
        await message.answer("Ваше имя успешно изменено")
    except Exception as error:
        logger.error(f"Ошибка при смене realname {error}")
        raise ValueError(f"Ошибка при смене realname {error}")

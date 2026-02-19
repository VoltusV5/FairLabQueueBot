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
    change_realname, add_tgname_in_queue, add_history_position,
    remove_tgname_in_queue,
    remove_queue, save_position_not_pass, get_realname

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
    [
        InlineKeyboardButton(
            text="✅ Участвую", callback_data="confirm_participation"),
        InlineKeyboardButton(text="❌ Отменить участие",
                             callback_data="cancel_participation")
    ],
    [
        InlineKeyboardButton(text="Удалить запись",
                             callback_data="del_queue"),
        InlineKeyboardButton(text="Завершить досрочно",
                             callback_data="close_queue")
    ]
]

# Кнопки для подтверждения завершения очереди
btn_confirm_close_queue = [
    InlineKeyboardButton(
        text="Да, завершить",
        callback_data="close_queue_YES"),
    InlineKeyboardButton(
        text="Нет, отмена",
        callback_data="close_queue_NO")
]

# Кнопка, чтобы отметиться последним сдавшим
btn_after_filling_queue = [
    InlineKeyboardButton(
        text="Я последний",
        callback_data="last_participant"),
    InlineKeyboardButton(
        text="Добавить себя в конец очереди",
        callback_data="add_last_user_in_queue")
]

# Кнопки для подтверждения удаления очереди
btn_confirm_del_queue = [
    InlineKeyboardButton(
        text="Да, удалить",
        callback_data="del_queue_YES"),
    InlineKeyboardButton(
        text="Нет, отмена",
        callback_data="del_queue_NO")
]

# Кнопки для подтверждения того, что ты последний в очереди
btn_confirm_last_participant = [
    InlineKeyboardButton(
        text="Да, я последний",
        callback_data="last_participant_YES"),
    InlineKeyboardButton(
        text="Нет, отмена",
        callback_data="last_participant_NO")
]

# Создаем объект инлайн-клавиатуры
keyboard = InlineKeyboardMarkup(inline_keyboard=btn_participate)

# Подгружаем Config
config: Config = load_config()

# Индекс 1ого пользователя в queue. Без служебной информации
# ['"q28"', '📅 08.02.2025', '⏰ 14:30', '', '1. @VoltusV_GG', '2. @VoltusV']
#                                                  ЧЕТЫРЕ
FIRST_USER_IN_QUEUE_NUMBER = 4
HEADER_LINES_COUNT = 3


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

        # Кидаем сообщение
        sent_message: Message = await message.answer(
            text=f"📘 Запись на {subject_name}\n"
            f"📅 {subject_date}\n"
            f"⏰ {subject_time}",
            reply_markup=keyboard)

        real_message_id = sent_message.message_id

        # Создаём новую Queue в БД
        add_new_queue(
            subject_id, chat_id, real_message_id, lesson_date,
            close_at, status.value, usernames)

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


# кнопка "участвую" и "отменить участие"
@router.callback_query(F.data.in_(
    ["confirm_participation", "cancel_participation"]))
async def process_buttons_click(callback: CallbackQuery):
    """Записывает пользователя в очередь при нажатии кнопки УЧАСТВУЮ.
    Или удаляем пользователя из очереди, если человек передумал,
    при нажатии ОТМЕНИТЬ УЧАСТИЕ
    """
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=False)
        return

    action = "add" if callback.data == "confirm_participation" else "remove"
    user_not_in_db = ("Вы записаны ✅"
                      if action == "add" else "Вы и так не в очереди")
    user_is_in_db = ("Участие отменено ❌"
                     if action == "remove" else "Вы уже записаны!")

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
            logger.info("Пользователь добавлен в БД")

        # Проверка на существование записи об очереди в БД
        if not is_in_db(message_id, Queue, "message_id"):
            raise ValueError("Предмет не найден, ошибка в добавлении очереди")
        # Добавление/Удаление пользователя в очереди
        if action == "add":
            success = add_tgname_in_queue(
                callback.from_user.username, message_id)
        else:
            text = callback.message.text or ""
            # Разбил, чтобы сформировать сообщение с записанными людьми
            splited_message = text[text.find("на") + 3:].split("\n")
            # Забрал название предмета
            message_subject = splited_message[0]
            success = remove_tgname_in_queue(
                callback.from_user.username, message_id,
                get_subject_id(chat_id, message_subject), chat_id)
        # Если функция вернула -1, то пользователь есть в БД
        if success == -1:
            await callback.answer(user_is_in_db)
            logger.info(f"Пользователь {user_is_in_db}")

        else:
            await callback.answer(user_not_in_db)
            logger.info(f"Пользователь {user_not_in_db}")

    except Exception as error:
        logger.error(f"Ошибка при проверке пользователя {error}")
        raise ValueError(f"Ошибка при проверке пользователя {error}")


@router.callback_query(F.data.in_({"close_queue",
                                   "close_queue_NO", "close_queue_YES"}))
async def close_queue(callback: CallbackQuery):
    """Досрочное завершение очереди
    в match-case реализовано подтверждение действия
    """
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=False)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    text = callback.message.text or ""  # Если None, то делаем пустую строку

    match callback.data:
        # Нажатие на кнопку "Завершить очередь"
        case "close_queue":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[btn_confirm_close_queue])
            await callback.message.edit_text(
                (text + "\n\n" + "Вы уверены, что "
                    "хотите досрочно завершить очередь?"),
                reply_markup=keyboard
            )
        # Отмена завершения очереди в меню "Да/Нет"
        case "close_queue_NO":
            splited_text = text.split("\n\n")
            keyboard = InlineKeyboardMarkup(inline_keyboard=btn_participate)
            await callback.message.edit_text(
                splited_text[0],  # Только информация об очереди
                reply_markup=keyboard
            )
        # Подтверждение завершения очереди в меню "Да/Нет"
        case "close_queue_YES":
            # Разбил сообщение, чтобы сформировать сообщение с записанными
            splited_message = text[text.find("на") + 3:].split("\n")
            # Забрал название предмета для кнопки, а также дату и время
            message_subject, message_date, message_time = splited_message[0:3]
            head = (f"Список на {message_subject}\n"
                    f"{message_date}\n{message_time}\n\n")

            # Переменная с финальной очередью
            queue = get_queue(callback.message, message_subject)
            # Полное сообщение с финальной очередью
            queue_head = head + queue

            # Кнопка "Я последний" и "добавить себя в конец списка"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[btn_after_filling_queue])

            # Отправляем финальную очередь
            await callback.message.edit_text(queue_head, reply_markup=keyboard)

            tg_username = callback.from_user.username
            await callback.message.answer(
                f"Пользователь @{tg_username} завершил очередь досрочно")


@router.callback_query(F.data.in_({"del_queue",
                                   "del_queue_YES", "del_queue_NO"}))
async def process_del_queue_click(callback: CallbackQuery):
    """Функция для удаления очереди. Если создал запись на предмет,
    а потом понял, что она не нужна.
    Удаление всей строки Queue в БД и сообщения
    """
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=True)
        return
    text = callback.message.text or ""
    match callback.data:
        # Нажатие на кнопку "Завершить очередь"
        case "del_queue":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[btn_confirm_del_queue])
            await callback.message.edit_text(
                (text + "\n\n" + "Вы уверены, что "
                    "хотите удалить эту очередь?"),
                reply_markup=keyboard
            )
        # Отмена удаления очереди в меню "Да/Нет"
        case "del_queue_NO":
            splited_text = text.split("\n\n")
            keyboard = InlineKeyboardMarkup(inline_keyboard=btn_participate)
            await callback.message.edit_text(
                splited_text[0],  # Только информация об очереди
                reply_markup=keyboard
            )
        # Подтверждение удаления очереди в меню "Да/Нет"
        case "del_queue_YES":
            if callback.from_user.username is None:
                return
            username = "@" + callback.from_user.username
            chat_id = callback.message.chat.id
            message_id = callback.message.message_id
            # Разбил сообщение, чтобы сформировать сообщение с записанными
            splited_message = text.split("\n")[:HEADER_LINES_COUNT]
            # Забрал название, дату и время предмета
            queue_subject, queue_date, queue_time = splited_message
            # Очистил от эмодзи:
            queue_subject = queue_subject[queue_subject.find("на") + 3:]
            queue_date, queue_time = queue_date[2:], queue_time[2:]
            subjcet_id = get_subject_id(chat_id, queue_subject)

            text = (f"Пользователь @{callback.from_user.username}\n"
                    f"Удалил очередь на {queue_subject} "
                    f"{queue_date} {queue_time}")
            remove_queue(username, chat_id, message_id, subjcet_id)
            await callback.message.delete()
            await callback.message.answer(text)


@router.callback_query(F.data.in_({"last_participant", "last_participant_NO",
                                   "last_participant_YES"}))
async def process_last_participant_click(callback: CallbackQuery):
    """Функция для отметки последнего участника в очереди,
    который успел сдать предмет
    """
    # ПОменяться сообщение
    # Запомнить номера которые были у людей которые не успели, отметим со *
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=True)
        return
    text = callback.message.text
    if text is None:
        return
    if callback.from_user.username is None:
        return

    match callback.data:
        # Нажатие на кнопку "Завершить очередь"
        case "last_participant":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[btn_confirm_last_participant])
            await callback.message.edit_text(
                (text + "\n\n" + "Вы уверены, что "
                    "вы последний?"),
                reply_markup=keyboard
            )
        # Отмена удаления очереди в меню "Да/Нет"
        case "last_participant_NO":
            splited_text = text.split("\n\n")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[btn_after_filling_queue])
            # Только информация об очереди и очередь
            return_message_text = '\n\n'.join(splited_text[:-1])
            await callback.message.edit_text(
                return_message_text,
                reply_markup=keyboard
            )
        # Подтверждение удаления очереди в меню "Да/Нет"
        case "last_participant_YES":
            tgname = "@" + callback.from_user.username

            # Возвращаем текст без "вы уверены, что вы последний?"
            splited_text = text.split("\n\n")
            return_message_text = '\n\n'.join(splited_text[:-1])
            text = return_message_text

            splited_message = text[text.find("на") + 3:].split("\n")
            # Забрал название предмета для кнопки, а также дату и время
            queue_subject = splited_message[0]

            # int(i.split(".")[0] - номер в списке
            # int(i.split(".")[1] - tg_username
            print(splited_message)
            queue: list[tuple[int, str]] = [
                (int(i.split(".")[0]),
                 i.split(" ")[-1].replace("(", "").replace(")", ""))
                for i in splited_message[FIRST_USER_IN_QUEUE_NUMBER:]
            ]
            print(queue)
            for queue_entry in queue:
                queue_position, queue_username = queue_entry
                print(queue_position, queue_username)
                if queue_username == tgname:
                    # Вычисляем номер человека после последного сдавшего
                    queue = queue[queue_position:]
                    break
            else:
                await callback.answer("Вы не записаны в очередь!")
                logging.info("Такой пользователь уже есть в текущей очереди")
                return

            if queue:
                # Создаём пометку для людей, которые идут после последнего
                save_position_not_pass(queue, get_subject_id(
                    callback.message.chat.id, queue_subject))

            await callback.message.edit_text(
                text + "\n\n" + f"Очередь завершена. "
                f"Последним был @{callback.from_user.username}")


@router.callback_query(F.data.in_(["add_last_user_in_queue"]))
async def add_last_user_in_queue(callback: CallbackQuery):
    """Функция для добавления себя в конец уже сформированной очереди"""
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно или удалено",
                              show_alert=True)
        return
    text = callback.message.text
    if text is None:
        return
    if callback.from_user.username is not None:
        tgname = "@" + callback.from_user.username
    else:
        return

    splited_message = text[text.find("на") + 3:].split("\n")
    # Забрал название предмета для кнопки, а также дату и время
    message_subject = splited_message[0]

    queue: list[tuple[int, str]] = [
        (int(i.split(".")[0]),
         i.split(" ")[-1].replace("(", "").replace(")", ""))
        for i in splited_message[FIRST_USER_IN_QUEUE_NUMBER:]
    ]
    for queue_entry in queue:
        queue_entry_username = queue_entry[1]
        if queue_entry_username == tgname:
            await callback.answer("Вы уже есть в текущей очереди!")
            logging.info("Такой пользователь уже есть в текущей очереди")
            return
    else:
        position_for_new_user = queue[-1][0] + 1 if queue else 1

        # Переменные для БД
        user_id = callback.from_user.id

        # Если пользователя ещё нет в БД - добавить его
        tg_username = tgname[1:]
        if not is_in_db(user_id, User, "user_id"):
            real_name = callback.from_user.full_name
            chat_id = callback.message.chat.id
            add_user_to_db(tg_username, chat_id, user_id, real_name)
            logger.info("Пользователь добавлен в БД")

        add_people = [(position_for_new_user, tgname)]

        # Добавить проверку есть ли он уже в очереди
        add_history_position(
            add_people,
            get_subject_id(callback.message.chat.id, message_subject)
        )
        add_tgname_in_queue(tg_username, callback.message.message_id)

        # Кнопка "Я последний" и "добавить себя в конец списка"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[btn_after_filling_queue])

        # Если в очереди никого нет, то поставить дополнительный \n
        # Чтобы отделить служебную информацию от очереди
        realname = get_realname(tgname)
        if position_for_new_user == 1:
            await callback.message.edit_text(
                f'{text}\n\n{position_for_new_user}. '
                f'{realname} ({tgname})', reply_markup=keyboard)
        else:
            await callback.message.edit_text(
                f'{text}\n{position_for_new_user}. '
                f'{realname} ({tgname})', reply_markup=keyboard)


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

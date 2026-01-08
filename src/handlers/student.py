"""
Обработчики команд студентов:
запись/выход из очереди, просмотр места и статистики, просмотр текущей очереди.
Не уверен, что реально надо
"""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from src.lexicon import LEXICON_RU

# Инициализируем роутер уровня модуля
router = Router()

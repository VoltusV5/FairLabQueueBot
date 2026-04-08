"""Сборка роутеров: student → queue → subscription → vip."""

from aiogram import Router

from .queue_routes import router as queue_router
from .student import router as student_router
from .subscription import router as subscription_router
from .vip import router as vip_router

router = Router()
router.include_router(student_router)
router.include_router(queue_router)
router.include_router(subscription_router)
router.include_router(vip_router)

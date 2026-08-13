import asyncio
import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy import select

from config import config
from database.db_helper import db_helper
from database.models import Base, PartnerChannel
from handlers.user import user_router
from handlers.admin import admin_router
# ДОБАВИЛИ: Импорт нового изолированного роутера реферальной системы
from handlers.referral import referral_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    """Мидлварь для автоматического проброса сессии SQLAlchemy в хендлеры"""
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with db_helper.session_factory() as session:
            data["db_session"] = session
            return await handler(event, data)


async def auto_initialize_system():
    """Автоматически создает таблицы и дефолтный канал поддержки при первом старте"""
    # 1. ЗАМЕНА ALEMBIC: создаем таблицы в PostgreSQL асинхронно силами SQLAlchemy
    async with db_helper.engine.begin() as conn:
        logger.info("⏳ Проверяю структуру БД и создаю таблицы...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ База данных успешно синхронизирована!")

    # 2. Инициализация заглушки саппорта
    async with db_helper.session_factory() as session:
        stmt = select(PartnerChannel).where(PartnerChannel.is_required == True)
        res = await session.execute(stmt)
        main_channel = res.scalar_one_or_none()
        
        if not main_channel:
            logger.info("⚙️ Создаю заглушку главного канала поддержки...")
            default_support = PartnerChannel(
                channel_id=-1000000000000,
                channel_name="Служба поддержки (Настройте в /admin)",
                invite_link="https://t.me",
                is_required=True
            )
            session.add(default_support)
            await session.commit()
            logger.info("✅ Системная заглушка успешно создана.")


async def main():
    logger.info(f"Запуск мультисерверного бота {config.BRAND_NAME}...")
    
    bot = Bot(
        token=config.BOT_TOKEN.get_secret_value(), 
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher(storage=MemoryStorage())
    
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())
    
    # ИСПРАВЛЕНО: Регистрируем роутеры в правильном каскадном порядке приоритетов
    dp.include_router(admin_router)
    dp.include_router(referral_router)  # Подключаем реферальный модуль
    dp.include_router(user_router)
    
    # Запускаем автоинициализацию СУБД и бэкенда
    await auto_initialize_system()
    
    from services.scheduler import setup_scheduler
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Планировщик проверки партнерских подписок успешно запущен.")
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        scheduler.shutdown()
        await bot.session.close()
        logger.info("Бот успешно остановлен.")

if __name__ == "__main__":
    asyncio.run(main())


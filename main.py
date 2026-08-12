# main.py — ЧАСТЬ 1 (ПОЛОВИНА 1.1)
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Callable, Dict, Any, Awaitable

from bot.config import config
from bot.database.db_helper import db_helper
from bot.handlers.user import user_router
from bot.handlers.admin import admin_router
from bot.services.scheduler import setup_scheduler

# Настройка логирования
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

from bot.database.models import PartnerChannel

async def auto_initialize_system():
    """Автоматически создает главный канал техподдержки, если бэкенд пустой"""
    async with db_helper.session_factory() as session:
        # Ищем, есть ли уже обязательный канал саппорта
        stmt = select(PartnerChannel).where(PartnerChannel.is_required == True)
        res = await session.execute(stmt)
        main_channel = res.scalar_one_or_none()
        
        if not main_channel:
            logger.info("⚙️ Первичный запуск: создаю заглушку главного канала поддержки...")
            # Создаем системную заглушку. Админ сможет изменить её ID и ссылку позже через /admin
            default_support = PartnerChannel(
                channel_id=-1000000000000, # Системный ID-заглушка
                channel_name="Служба поддержки (Настройте в /admin)",
                invite_link="https://t.me", # Временная ссылка
                is_required=True
            )
            session.add(default_support)
            await session.commit()

async def main():
    logger.info("Запуск мультисерверного бота Overlord VPN...")
    
    # Инициализация бота и диспетчера
    bot = Bot(token=config.BOT_TOKEN.get_secret_value(), parse_mode="HTML")
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрируем мидлварь базы данных на сообщения и колбэки
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())
    
    # Подключаем роутеры хендлеров
    dp.include_router(admin_router)
    dp.include_router(user_router)
    
    # Инициализируем и запускаем планировщик проверки отписок
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Планировщик проверки партнерских подписок успешно запущен.")
    
    try:
        # Запуск polling
        await dp.start_polling(bot, skip_updates=True)
    finally:
        # Корректное закрытие сессий при остановке бота
        scheduler.shutdown()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

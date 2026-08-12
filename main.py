import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import config

# Настраиваем лаконичный формат логирования для продакшена
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Запуск новой мультисерверной архитектуры бота...")
    
    bot = Bot(token=config.BOT_TOKEN.get_secret_value(), parse_mode="HTML")
    dp = Dispatcher(storage=MemoryStorage())
    
    # Сюда в следующих шагах мы подключим изолированные роутеры и мидлварь БД
    # dp.include_router(admin_router)
    # dp.include_router(user_router)
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

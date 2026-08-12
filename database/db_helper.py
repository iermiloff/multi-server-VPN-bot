# database/db_helper.py — ЧАСТЬ 3 (ПОЛОВИНА 3.1)
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from bot.config import config

logger = logging.getLogger(__name__)

# Формируем URL для асинхронного драйвера asyncpg.
# Пароль извлекаем через .get_secret_value(), чтобы он не светился в логах.
DATABASE_URL = (
    f"postgresql+asyncpg://{config.DB_USER}:{config.DB_PASSWORD.get_secret_value()}"
    f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
)

class DatabaseHelper:
    """Управляющий класс для создания асинхронных сессий к СУБД"""
    
    def __init__(self, url: str, echo: bool = False):
        # Создаем асинхронный движок
        self.engine = create_async_engine(
            url=url,
            echo=echo,  # Если поставить True, в консоль будут сыпаться все сырые SQL-запросы
        )
        # Инициализируем фабрику сессий
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
# database/db_helper.py — ЧАСТЬ 3 (ПОЛОВИНА 3.2)

    async def session_getter(self) -> AsyncSession:
        """Асинхронный генератор сессий для кастомной логики, если потребуется"""
        async with self.session_factory() as session:
            yield session
            await session.commit()

# Создаем единственный глобальный экземпляр хелпера на все приложение
db_helper = DatabaseHelper(url=DATABASE_URL, echo=False)

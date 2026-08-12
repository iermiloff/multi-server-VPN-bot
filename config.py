# config.py
from typing import List, Annotated, Any
from pydantic import SecretStr, BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode

def parse_comma_separated_ids(value: Any) -> List[int]:
    """Безопасно преобразует любую строку вида 123 или 123,456 в список чисел"""
    if not value:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        # Разбиваем по запятой, убираем пробелы и фильтруем только пустые элементы
        return [int(x.strip()) for x in value.split(",") if x.strip()]
    return value

# Создаем кастомный тип: отключаем JSON-декодер и вешаем наш безопасный парсер
AdminIdsList = Annotated[List[int], NoDecode, BeforeValidator(parse_comma_separated_ids)]

class Settings(BaseSettings):
    BOT_TOKEN: SecretStr
    
    # Теперь тип данных защищен от любых форматов ввода пользователя
    ADMIN_IDS: AdminIdsList
    
    # Настройки СУБД PostgreSQL
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: SecretStr
    DB_NAME: str
    
    # Токены CryptoBot
    CRYPTO_BOT_TOKEN: SecretStr
    CRYPTO_BOT_NET: bool = False
    
    # Маркетинг платежей
    PRICE_BASE_1_MONTH: float = 5.0
    PRICE_BASE_3_MONTHS: float = 13.5
    PRICE_BASE_6_MONTHS: float = 24.0
    PRICE_PREMIUM_1_MONTH: float = 8.0
    PRICE_PREMIUM_3_MONTHS: float = 21.6
    PRICE_PREMIUM_6_MONTHS: float = 38.4
    PAYMENT_CURRENCY: str = "USDT"
    BRAND_NAME: str = "Overlord Multi-VPN"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

config = Settings()

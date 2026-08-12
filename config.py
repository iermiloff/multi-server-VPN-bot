# config.py
from typing import List, Any
from pydantic import SecretStr, BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    BOT_TOKEN: SecretStr
    
    # Считываем как строку, чтобы Pydantic не пытался декорировать её как JSON
    ADMIN_IDS: str
    
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

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: Any) -> List[int]:
        """Универсальный парсер: обрабатывает строку, число или уже готовый список"""
        if not value:
            return []
        # Если Pydantic уже распознал это как список 
        if isinstance(value, list):
            return [int(x) for x in value]
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(x.strip()) for x in value.split(",") if x.strip()]
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

config = Settings()

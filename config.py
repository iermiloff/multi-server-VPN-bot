from typing import List
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    BOT_TOKEN: SecretStr
    ADMIN_IDS: List[int]

    # CryptoBot
    CRYPTO_BOT_TOKEN: SecretStr
    CRYPTO_BOT_NET: bool = False

    # Настройки СУБД PostgreSQL
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: SecretStr
    DB_NAME: str
    
    # Маркетинг платежей
    PRICE_BASE_1_MONTH: float = 5.0
    PRICE_BASE_3_MONTHS: float = 13.5
    PRICE_BASE_6_MONTHS: float = 24.0
    PAYMENT_CURRENCY: str = "USDT"
    BRAND_NAME: str = "Overlord Multi-VPN"

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

config = Settings()

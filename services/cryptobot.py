import logging
import aiohttp
from typing import Optional, Dict, Any
from config import config

logger = logging.getLogger(__name__)

class CryptoBotClient:
    """Официальный асинхронный клиент Crypto Pay API для ручной проверки счетов методом пуллинга"""
    
    def __init__(self):
        self.token = config.CRYPTO_BOT_TOKEN.get_secret_value()
        
        # Разделение официальных URL тестнета и майннета (стр. 3-4 документации)
        if config.CRYPTO_BOT_NET:
            self.base_url = "https://pay.crypt.bot/"
        else:
            self.base_url = "https://testnet-pay.crypt.bot/"
            
        self.headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json"
        }

    async def create_invoice(self, amount: float, asset: str, description: str, payload: str) -> Optional[Dict[str, Any]]:
        """Создание инвойса (счета) с автоматической поддержкой Фиата и Крипты (стр. 4)"""
        url = f"{self.base_url.rstrip('/')}/createInvoice"
        
        # Определение типа валюты согласно спецификации на стр. 4
        is_fiat = asset.upper() in ["USD", "EUR", "RUB", "BYN", "UAH", "KZT", "GBP", "CNY", "GEL", "TRY"]
        
        data = {
            "amount": f"{amount:.2f}",
            "description": description[:1024],  # Лимит документации 1024 символа
            "payload": payload[:4096]            # Лимит документации 4 КБ
        }
        
        if is_fiat:
            data["currency_type"] = "fiat"
            data["fiat"] = asset.upper()
            data["accepted_assets"] = "USDT,TON"
        else:
            data["currency_type"] = "crypto"
            data["asset"] = asset.upper()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=data, timeout=10) as response:
                    result = await response.json()
                    if response.status in (200, 201) and result.get("ok"):
                        return result.get("result")
                    logger.error(f"Ошибка CryptoBot API (Status {response.status}): {result}")
                    return None
        except Exception as e:
            logger.error(f"Сетевое исключение при создании инвойса в CryptoBot: {e}")
            return None

    async def get_invoice_status(self, invoice_id: int) -> Optional[str]:
        """
        Запрашивает информацию о конкретном счете методом пуллинга (стр. 6).
        ИСПРАВЛЕНО: Извлекает первый элемент из списка и возвращает его статус ("active", "paid", "expired").
        """
        url = f"{self.base_url.rstrip('/')}/getInvoices"
        params = {"invoice_ids": str(invoice_id)}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params, timeout=10) as response:
                    result = await response.json()
                    if response.status == 200 and result.get("ok"):
                        items = result.get("result", {}).get("items", [])
                        if items and len(items) > 0:
                            # Берем первый найденный инвойс из массива и возвращаем его статус
                            return items[0].get("status")
                    return None
        except Exception as e:
            logger.error(f"Сетевой srv-сбой при проверке статуса инвойса {invoice_id}: {e}")
            return None

# Экземпляр клиента для импорта в хендлеры
cryptobot_client = CryptoBotClient()

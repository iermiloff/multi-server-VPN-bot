# services/lava.py — ОФИЦИАЛЬНЫЙ МОНОЛИТНЫЙ КЛИЕНТ LAVA.TOP V3 API
import logging
import aiohttp
from typing import Optional, Dict, Any
from config import config

logger = logging.getLogger(__name__)

class LavaTopClient:
    """Асинхронный клиент Lava.top V3 с динамическим amount и единым offerId"""
    
    def __init__(self):
        self.api_key = config.LAVA_API_KEY.get_secret_value() if config.LAVA_API_KEY else ""
        self.offer_id = config.LAVA_OFFER_ID or ""  # Единый offerId на весь бот
        self.base_url = "https://gate.lava.top/api"
        
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        }

    async def create_invoice(self, amount: float, client_email: str) -> Optional[Dict[str, Any]]:
        """Создание динамического контракта на покупку подписки (стр. 5, 29)"""
        url = f"{self.base_url}/v3/invoice"
        
        data = {
            "email": client_email,
            "offerId": self.offer_id,  # Передаем наш единственный offerId
            "currency": "RUB",
            "amount": float(f"{amount:.2f}")  # Динамическая цена тарифа в рублях
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=data, timeout=10) as response:
                    res = await response.json()
                    if response.status in (200, 201):
                        return {
                            "invoice_id": res.get("id"),
                            "url": res.get("paymentUrl")
                        }
                    logger.error(f"Ошибка Lava.top API (Status {response.status}): {res}")
                    return None
        except Exception as e:
            logger.error(f"Сетевое исключение при создании счета в Lava.top: {e}")
            return None

    async def get_invoice_status(self, contract_id: str) -> Optional[str]:
        """Получение статуса контракта методом пуллинга по кнопке (стр. 26)"""
        url = f"{self.base_url}/v2/invoices/{contract_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, timeout=10) as response:
                    res = await response.json()
                    if response.status == 200:
                        return res.get("status")  # NEW, IN_PROGRESS, COMPLETED, FAILED
                    return None
        except Exception as e:
            logger.error(f"Сбой пуллинга статуса контракта Lava.top {contract_id}: {e}")
            return None

lava_top_client = LavaTopClient()

# services/cryptobot.py
import logging
import aiohttp
from typing import Optional, Dict, Any
from config import config

logger = logging.getLogger(__name__)

class CryptoBotClient:
    """Асинхронный клиент для выставления счетов в Crypto Pay (@CryptoBot)"""
    
    def __init__(self):
        self.token = config.CRYPTO_BOT_TOKEN.get_secret_value()
        # Выбираем хост: тестовая сеть (@CryptoTestnetBot) или реальная
        if config.CRYPTO_BOT_NET:
            self.base_url = "https://cryptomus.com" # или официальный pay.cryptoboss
            self.base_url = "https://cryptobot.sh"
        else:
            self.base_url = "https://cryptobot.sh"
            
        self.headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json"
        }

    async def create_invoice(self, amount: float, asset: str, description: str, payload: str) -> Optional[Dict[str, Any]]:
        """Создание нового счета (инвойса) согласно официальной документации"""
        url = f"{self.base_url}/createInvoice"
        data = {
            "asset": asset.upper(),
            "amount": str(amount),
            "description": description,
            "payload": payload
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=data, timeout=10) as response:
                    result = await response.json()
                    if response.status == 200 and result.get("ok"):
                        return result.get("result")
                    logger.error(f"Ошибка CryptoBot API: {result}")
                    return None
        except Exception as e:
            logger.error(f"Сетевая ошибка при создании счета CryptoBot: {e}")
            return None

# Создаем синглтон-экземпляр клиента для импорта в хендлеры
cryptobot_client = CryptoBotClient()

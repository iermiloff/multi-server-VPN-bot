import logging
import aiohttp
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

class XUIMultiClient:
    """Универсальный клиент для работы с API 3x-ui панелей через Bearer-токены"""
    
    def __init__(self, api_url: str, api_token: str):
        # Отрезаем лишние слэши на конце URL для предсказуемости путей
        self.base_url = api_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
    async def _request(self, method: str, path: str, json_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=self.headers, json=json_data, timeout=10) as response:
                    # ИСПРАВЛЕНО: проверяем успешные HTTP-статусы (200, 201)
                    if response.status in (200, 201):
                        return await response.json()
                    logger.error(f"Ошибка API 3x-ui ({url}): Статус {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Сетевое исключение при запросе к ноде {url}: {e}")
            return None

    async def get_inbounds(self) -> List[Dict[str, Any]]:
        """Получение списка инбаундов ноды (Страница 2 API)"""
        result = await self._request("GET", "panel/api/inbounds/list/slim")
        if result and isinstance(result, list):
            return result
        return result.get("obj", []) if result and "obj" in result else []

    async def add_client(self, email: str, sub_id: str, inbound_ids: List[int], expires_days: int) -> bool:
        """
        Создание клиента и привязка его сразу к пулу инбаундов (Страница 9 API).
        Генерирует единый профиль подписки.
        """
        import datetime
        # Вычисляем timestamp истечения в миллисекундах
        expiry_time = int((datetime.datetime.utcnow() + datetime.timedelta(days=expires_days)).timestamp() * 1000)
        
        # Конструируем payload согласно Schema: Client из документации
        payload = {
            "client": {
                "email": email,
                "subId": sub_id,
                "expiryTime": expiry_time,
                "enable": True,
                "totalGB": 0 # 0 означает безлимитный трафик по умолчанию
            },
            "inboundIds": inbound_ids
        }
        
        result = await self._request("POST", "panel/api/clients/add", payload)
        return result is not None and result.get("success", False)

    async def delete_client(self, email: str) -> bool:
        """Полное каскадное удаление клиента со всех инбаундов ноды (Страница 9 API)"""
        result = await self._request("POST", f"panel/api/clients/del/{email}")
        return result is not None and result.get("success", False)

    async def set_client_status(self, email: str, enable: bool) -> bool:
        """Массовое включение/выключение клиента на ноде (Страница 10 API)"""
        path = "bulkEnable" if enable else "bulkDisable"
        payload = {"emails": [email]}
        result = await self._request("POST", f"panel/api/clients/{path}", payload)
        return result is not None

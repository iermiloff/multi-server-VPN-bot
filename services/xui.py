# services/xui.py — ШАГ 1 ИЗ 2
import logging
import aiohttp
import json
import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class XUIMultiClient:
    def __init__(self, api_url: str, api_token: str):
        self.base_url = api_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    async def _request(self, method: str, path: str, json_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.request(method, url, headers=self.headers, json=json_data, timeout=10) as response:
                    if response.status in (200, 201):
                        return await response.json()
                    logger.error(f"Ошибка API 3x-ui ({url}): Статус {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Сетевое исключение при запросе к ноде {url}: {e}")
            return None

    async def get_inbounds(self) -> List[Dict[str, Any]]:
        res = await self._request("GET", "panel/api/inbounds/list/slim")
        if res and res.get("success"):
            return res.get("obj", [])
        return []

    async def add_client(self, email: str, sub_id: str, inbound_ids: List[int], expires_days: int) -> bool:
        """Создание клиента пачкой во всех инбаундах согласно OAS 3.0 документации"""
        expiry_time = int((datetime.datetime.utcnow() + datetime.timedelta(days=expires_days)).timestamp() * 1000)
        
        payload = {
            "id": sub_id,
            "email": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": expiry_time,
            "enable": True,
            "subId": sub_id,
            "inboundIds": inbound_ids  # Массив числовых ID инбаундов
        }
        
        path = "panel/api/clients/add"
        res = await self._request("POST", path, json_data=payload)
        
        if res and res.get("success"):
            return True
            
        # АВТО-ЗАЩИТА: Если email уже занят в глобальной базе, вызываем принудительное обновление
        logger.warning(f"Панель отклонила создание {email} (дубликат). Запускаю принудительное обновление параметров...")
        return await self.update_client_expiry_by_payload(email, payload)

# services/xui.py — ШАГ 2 ИЗ 2

    async def update_client_expiry_by_payload(self, email: str, payload: Dict[str, Any]) -> bool:
        """Прямое обновление существующего клиента по email готовым payload-объектом"""
        path = f"panel/api/clients/update/{email}"
        res = await self._request("POST", path, json_data=payload)
        return res is not None and res.get("success", False)

    async def update_client_expiry(self, email: str, expiry_time: int) -> bool:
        """Точечное продление времени действия существующего клиента по его email"""
        # Сначала запрашиваем детальную информацию о клиенте из глобальной базы панели
        path_get = f"panel/api/clients/get/{email}"
        res_get = await self._request("GET", path_get)
        
        if not (res_get and res_get.get("success") and res_get.get("obj")):
            logger.error(f"Не удалось получить карточку клиента {email} для продления.")
            return False
            
        # Извлекаем текущий полный JSON-объект клиента из панели
        client_data = res_get.get("obj")
        
        # Меняем только таймштамп окончания подписки (в миллисекундах)
        client_data["expiryTime"] = expiry_time
        
        # Отправляем обновленный монолитный объект обратно согласно спецификации API
        path_update = f"panel/api/clients/update/{email}"
        res = await self._request("POST", path_update, json_data=client_data)
        return res is not None and res.get("success", False)


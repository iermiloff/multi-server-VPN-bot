# services/xui.py — ШАГ 1 ИЗ 2 (НОВЫЙ СТАНДАРТ МУЛЬТИ-API)
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
        """ИСПРАВЛЕНО ПО ДОКУМЕНТАЦИИ: создание клиента сразу во всех инбаундах"""
        expiry_time = int((datetime.datetime.utcnow() + datetime.timedelta(days=expires_days)).timestamp() * 1000)
        
        # Согласно скриншоту, отправляем один универсальный запрос
        payload = {
            "id": sub_id,
            "email": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": expiry_time,
            "enable": True,
            "subId": sub_id,
            "inboundIds": inbound_ids  # Передаем массив портов одной пачкой!
        }
        
        # Новый эндпоинт со скриншота
        path = "panel/api/clients/add"
        res = await self._request("POST", path, json_data=payload)
        return res is not None and res.get("success", False)

# services/xui.py — ШАГ 2 ИЗ 2 (ПРОДЛЕНИЕ ПО НОВОМУ СТАНДАРТУ)

    async def update_client_expiry(self, email: str, expiry_time: int) -> bool:
        """Обновление времени действия клиента по новому стандарту мульти-API"""
        inbounds = await self.get_inbounds()
        if not inbounds:
            return False
            
        for ib in inbounds:
            settings = ib.get("settings", {})
            if isinstance(settings, str):
                try: settings = json.loads(settings)
                except Exception: continue
                
            clients = settings.get("clients", [])
            for client in clients:
                if client.get("email") == email:
                    # Нашли клиента — отправляем обновление параметров на новый эндпоинт
                    client_id = client.get("id")
                    client["expiryTime"] = expiry_time
                    
                    # Используем современный эндпоинт обновления мульти-клиента
                    path = f"panel/api/clients/update/{client_id}"
                    res = await self._request("POST", path, json_data=client)
                    return res is not None and res.get("success", False)
        return False

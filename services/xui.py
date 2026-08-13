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


    async def add_client(self, email: str, sub_id: str, inbound_ids: List[int], expires_days: int, plan_type: str = "base") -> bool:
        """Добавление мульти-клиента во все инбаунды по спецификации с лимитами IP и трафика"""
        expiry_time = int((datetime.datetime.utcnow() + datetime.timedelta(days=expires_days)).timestamp() * 1000)
        
        # Рассчитываем лимит трафика в байтах: 150 ГБ для BASE, 300 ГБ для PREMIUM
        gb_limit = 300 if plan_type.lower() == "premium" else 150
        total_bytes = gb_limit * 1024 * 1024 * 1024
        
        payload = {
            "client": {
                "id": sub_id,
                "email": email,
                "limitIp": 3,          # Жесткий лимит: не более 3-х одновременных IP устройств
                "totalGB": total_bytes, # Лимит трафика в байтах
                "expiryTime": expiry_time,
                "enable": True,
                "subId": sub_id
            },
            "inboundIds": inbound_ids
        }
        
        path = "panel/api/clients/add"
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.request("POST", url, headers=self.headers, json=payload, timeout=10) as response:
                    res = await response.json()
                    if response.status in (200, 201) and res.get("success"):
                        logger.info(f"✅ Мульти-клиент {email} успешно создан с лимитом {gb_limit} ГБ и 3 IP")
                        return True
                    logger.error(f"❌ Панель отклонила создание клиента {email}: {res}")
                    return False
        except Exception as e:
            logger.error(f"Сетевой сбой при добавлении мульти-клиента: {e}")
            return False


# services/xui.py — ШАГ 2 ИЗ 2

    async def update_client_expiry_by_payload(self, email: str, payload: Dict[str, Any]) -> bool:
        """Прямое обновление существующего клиента по email готовым payload-объектом"""
        path = f"panel/api/clients/update/{email}"
        res = await self._request("POST", path, json_data=payload)
        return res is not None and res.get("success", False)

    async def update_client_expiry(self, email: str, expiry_time: int) -> bool:
        """Точечное продление времени действия существующего клиента по его UUID"""
        # Сначала находим карточку клиента, чтобы не затереть его текущие inboundIds
        inbounds = await self.get_inbounds()
        if not inbounds: return False
        
        import json
        target_client = None
        
        for ib in inbounds:
            settings = ib.get("settings", {})
            if isinstance(settings, str):
                try: settings = json.loads(settings)
                except Exception: continue
            for client in settings.get("clients", []):
                if client.get("email") == email:
                    target_client = client
                    break
            if target_client: break
            
        if not target_client:
            logger.error(f"Клиент {email} не найден в инбаундах панели для продления времени.")
            return False
            
        # Меняем timestamp и забираем UUID (id) клиента в панели
        target_client["expiryTime"] = expiry_time
        client_uuid = target_client.get("id")
        
        # ИСПРАВЛЕНО: Стучимся на эндпоинт обновления строго по UUID клиента
        path_update = f"panel/api/clients/update/{client_uuid}"
        res = await self._request("POST", path_update, json_data=target_client)
        return res is not None and res.get("success", False)


# services/xui.py — ИСПРАВЛЕННЫЙ МЕТОД ОБНОВЛЕНИЯ МУЛЬТИ-КЛИЕНТА

    async def attach_client_inbounds(self, email: str, inbound_ids: List[int]) -> bool:
        """Официальный метод привязки существующего клиента к новым инбаундам (стр. 9)"""
        payload = {
            "inboundIds": inbound_ids  # Массив портов, которые нужно ДОБАВИТЬ клиенту
        }
        path = f"panel/api/clients/{email}/attach"
        res = await self._request("POST", path, json_data=payload)
        return res is not None and res.get("success", False)

    async def update_client_expiry(self, email: str, expiry_time: int) -> bool:
        """Обновление строки клиента строго по схеме со скриншота документации"""
        # Формируем тело запроса в точности как на скриншоте API
        payload = {
            "email": email,
            "totalGB": 0,
            "expiryTime": expiry_time, # Чистый таймштамп 60 дней премиума
            "tgId": "",
            "enable": True
        }
        path = f"panel/api/clients/update/{email}"
        res = await self._request("POST", path, json_data=payload)
        return res is not None and res.get("success", False)

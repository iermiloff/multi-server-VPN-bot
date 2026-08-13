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

# services/xui.py — ИСПРАВЛЕННЫЙ МЕТОД СОГЛАСНО СПЕЦИФИКАЦИИ ПАНЕЛИ

# services/xui.py — ВНУТРИ КЛАССА XUIMultiClient

    async def add_client(self, email: str, sub_id: str, inbound_ids: List[int], expires_days: int) -> bool:
        """Добавление мульти-клиента во все инбаунды по официальной спецификации"""
        expiry_time = int((datetime.datetime.utcnow() + datetime.timedelta(days=expires_days)).timestamp() * 1000)
        
        # ИСПРАВЛЕНО: Обернули параметры в объект "client" согласно JSON-схеме панели
        payload = {
            "client": {
                "id": sub_id,        # Уникальный UUID ключа
                "email": email,      # Email пользователя
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": expiry_time,
                "enable": True,
                "subId": sub_id      # Хвост ссылки подписки
            },
            "inboundIds": inbound_ids  # Список портов тарифа на верхнем уровне
        }
        
        path = "panel/api/clients/add"
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.request("POST", url, headers=self.headers, json=payload, timeout=10) as response:
                    res = await response.json()
                    
                    if response.status in (200, 201) and res.get("success"):
                        logger.info(f"✅ Клиент {email} успешно создан в глобальной базе панели пачкой на порты {inbound_ids}")
                        return True
                        
                    logger.error(f"❌ Панель отклонила создание клиента {email}. Ответ панели: {res}")
                    
                    # Запасной путь обновления, если клиент уже существовал
                    return await self.update_client_expiry_by_payload(email, payload["client"])
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

    async def update_client_inbounds(self, email: str, sub_id: str, inbound_ids: List[int], expiry_time: int) -> bool:
        """Перенарезает список инбаундов и выставляет точное время для существующего мульти-клиента"""
        payload = {
            "id": sub_id,
            "email": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": expiry_time, # Чистый таймштамп 60 дней премиума
            "enable": True,
            "subId": sub_id,
            "inboundIds": inbound_ids
        }
        
        path = f"panel/api/clients/update/{sub_id}"
        res = await self._request("POST", path, json_data=payload)
        return res is not None and res.get("success", False)


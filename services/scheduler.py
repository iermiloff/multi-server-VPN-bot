# services/scheduler.py — ПОЛНЫЙ КОД ВОРКЕРА С АВТОАКТИВАЦИЕЙ ТАРИФОВ ИЗ ОЧЕРЕДИ
import logging
import datetime
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database.db_helper import db_helper
from database.models import Subscription, VPNKey, TariffInbound, Server, SubscriptionType
from services.xui import XUIMultiClient

logger = logging.getLogger(__name__)

async def deactivate_user_on_servers(session, user_id: int):
    """Каскадный снос аккаунтов со всех панелей 3x-ui"""
    keys_stmt = select(VPNKey).join(Subscription).where(Subscription.user_id == user_id).options(selectinload(VPNKey.server))
    keys_res = await session.execute(keys_stmt)
    keys = keys_res.scalars().all()
    
    for key in keys:
        if key.server:
            # Напрямую удаляем по API
            import json
            xui = XUIMultiClient(api_url=key.server.api_url, api_token=key.server.api_token)
            inbounds = await xui.get_inbounds()
            for ib in inbounds:
                settings = ib.get("settings", {})
                if isinstance(settings, str):
                    try: settings = json.loads(settings)
                    except Exception: continue
                clients = settings.get("clients", [])
                for client in clients:
                    if client.get("email") == key.client_email:
                        path = f"panel/api/inbounds/deleteClient/{ib.get('id')}/{client.get('id')}"
                        await xui._request("POST", path)
            await session.delete(key)

async def check_partner_subscriptions_job(bot: Bot):
    """Проверяет сроки подписок и активирует отложенные тарифы из очереди"""
    now = datetime.datetime.utcnow()
    
    async with db_helper.session_factory() as session:
        stmt = (
            select(Subscription)
            .where(Subscription.is_active == True, Subscription.is_pending == False, Subscription.expires_at <= now)
            .options(selectinload(Subscription.keys).selectinload(VPNKey.server))
        )
        res = await session.execute(stmt)
        expired_subs = res.scalars().all()

        for sub in expired_subs:
            user_id = sub.user_id
            
            queue_stmt = select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.is_active == True,
                Subscription.is_pending == True
            ).limit(1)
            q_res = await session.execute(queue_stmt)
            pending_sub = q_res.scalar_one_or_none()

            if pending_sub:
                saved_days = (pending_sub.expires_at - sub.created_at).days
                if saved_days <= 0: saved_days = 30
                    
                pending_sub.is_pending = False
                pending_sub.expires_at = now + datetime.timedelta(days=saved_days)
                sub.is_active = False
                
                for key in sub.keys:
                    if key.server and key.server.is_active:
                        if pending_sub.plan_type == SubscriptionType.PREMIUM:
                            ib_stmt = select(TariffInbound).where(TariffInbound.server_id == key.server_id, TariffInbound.plan_type.in_([SubscriptionType.BASE, SubscriptionType.PREMIUM]))
                        else:
                            ib_stmt = select(TariffInbound).where(TariffInbound.server_id == key.server_id, TariffInbound.plan_type == SubscriptionType.BASE)
                            
                        ib_res = await session.execute(ib_stmt)
                        inbound_ids = [ib.inbound_id for ib in ib_res.scalars().all()]
                        
                        if inbound_ids:
                            xui = XUIMultiClient(api_url=key.server.api_url, api_token=key.server.api_token)
                            expiry_timestamp = int(pending_sub.expires_at.timestamp() * 1000)
                            await xui.add_client(email=key.client_email, sub_id=key.sub_id, inbound_ids=inbound_ids, expires_days=1)
                            await xui.update_client_expiry(email=key.client_email, expiry_time=expiry_timestamp)
                try:
                    await bot.send_message(chat_id=user_id, text=f"🔄 <b>Смена тарифа!</b>\n\nСрок действия тарифа {sub.plan_type.upper()} истек. Автоматически активирован отложенный тариф <b>{pending_sub.plan_type.upper()}</b> на {saved_days} дней!")
                except Exception: pass
            else:
                sub.is_active = False
                await deactivate_user_on_servers(session, user_id)
                try:
                    await bot.send_message(chat_id=user_id, text="⚠️ <b>Срок действия подписки истек!</b>\n\nДоступ к VPN-серверам заблокирован. Продлите подписку в главном меню бота.")
                except Exception: pass
                
        await session.commit()

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Инициализация и запуск единого центра фоновых задач бота"""
    scheduler = AsyncIOScheduler()

    # 1. СТАРАЯ ЗАДАЧА: Проверка протухших дней (каждый час)
    scheduler.add_job(
        check_partner_subscriptions_job,
        trigger="interval",
        hours=1,
        args=[bot],
        id="check_subs"
    )

    # 2. НОВАЯ ЗАЗАЧА: Обнуление трафика 150/300 ГБ (1-го числа каждого месяца в 00:00)
    scheduler.add_job(
        monthly_traffic_reset_job,
        trigger="cron",
        day=1,
        hour=0,
        minute=0,
        id="monthly_traffic_reset"
    )

    logger.info("🚀 [APScheduler] Обе фоновые задачи биллинга (дни + трафик) успешно зарегистрированы!")
    return scheduler

# services/scheduler.py — ЕЖЕМЕСЯЧНОЕ ОБНУЛЕНИЕ ТРАФИКА И ВЫСТАВЛЕНИЕ ЛИМИТОВ 1-ГО ЧИСЛА

async def monthly_traffic_reset_job():
    """Запускается 1-го числа каждого месяца: обновляет лимиты и обнуляет счетчики трафика активных юзеров"""
    logger.info("🕒 Запуск ежемесячного воркера обновления лимитов трафика...")
    now = datetime.datetime.utcnow()
    
    async with db_helper.session_factory() as session:
        # Вытаскиваем все активные ноды
        servers_res = await session.execute(select(Server).where(Server.is_active == True))
        servers = servers_res.scalars().all()
        
        # Вытаскиваем все активные подписки из СУБД бота
        stmt = select(Subscription).where(Subscription.is_active == True, Subscription.is_pending == False, Subscription.expires_at > now).options(selectinload(Subscription.keys))
        res = await session.execute(stmt)
        active_subs = res.scalars().all()
        
        # Группируем пользователей по их текущему активному тарифу
        user_plans = {}
        for sub in active_subs:
            user_plans[sub.user_id] = sub.plan_type

        for srv in servers:
            xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
            inbounds = await xui.get_inbounds()
            
            import json
            for ib in inbounds:
                settings = ib.get("settings", {})
                if isinstance(settings, str):
                    try: settings = json.loads(settings)
                    except Exception: continue
                    
                for client in settings.get("clients", []):
                    email = client.get("email", "")
                    
                    # Фильтруем только наших системных пользователей бота
                    if email.startswith("usr_"):
                        try:
                            # Вытаскиваем Telegram ID из строки email (usr_TELEGRAMID_хэш)
                            tg_id = int(email.split("_")[1])
                        except (IndexError, ValueError):
                            continue
                            
                        # Определяем, какой лимит выставить на новый месяц
                        user_plan = user_plans.get(tg_id, SubscriptionType.BASE)
                        gb_limit = 300 if user_plan == SubscriptionType.PREMIUM else 150
                        target_bytes = gb_limit * 1024 * 1024 * 1024
                        
                        # Сохраняем текущие параметры карточки
                        client["totalGB"] = target_bytes
                        client["limitIp"] = 3
                        
                        # 1. Жестко прописываем лимит на панели на новый месяц
                        path_update = f"panel/api/clients/update/{client.get('id')}"
                        await xui._request("POST", path_update, json_data=client)
                        
                        # 2. Обнуляем счетчик скачанных гигабайт (стр. 12 документации)
                        path_reset = f"panel/api/clients/resetTraffic/{email}"
                        await xui._request("POST", path_reset)
                        
    logger.info("✅ Все счетчики трафика успешно обнулены, лимиты 150/300 ГБ обновлены!")

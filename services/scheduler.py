# services/scheduler.py — ПОЛНЫЙ КОД ВОРКЕРА С АВТОАКТИВАЦИЕЙ ТАРИФОВ ИЗ ОЧЕРЕДИ
import logging
import datetime
from aiogram import Bot
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
    """Инициализация планировщика задач"""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_partner_subscriptions_job,
        "interval",
        hours=2,
        args=[bot]
    )
    return scheduler

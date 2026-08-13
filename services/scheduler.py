import logging
import datetime
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.db_helper import db_helper
from database.models import User, Subscription, VPNKey, Server, TariffInbound, SubscriptionType
from services.xui import XUIMultiClient

logger = logging.getLogger(__name__)

async def deactivate_user_on_servers(session, user_id: int):
    """Полное каскадное удаление мульти-клиента по email со всех нод сети 3x-ui"""
    keys_stmt = select(VPNKey).join(Subscription).where(Subscription.user_id == user_id).options(selectinload(VPNKey.server))
    keys_res = await session.execute(keys_stmt)
    keys = keys_res.scalars().all()
    
    for key in keys:
        if key.server and key.server.is_active:
            try:
                xui = XUIMultiClient(api_url=key.server.api_url, api_token=key.server.api_token)
                path_delete = f"panel/api/clients/del/{key.client_email}"
                await xui._request("POST", path_delete)
            except Exception as e:
                logger.error(f"Ошибка удаления {key.client_email} на сервере {key.server.name}: {e}")
            
            await session.delete(key)

async def check_partner_subscriptions_job(bot: Bot):
    """Почасовая проверка протухших подписок и автоматическое размораживание очереди"""
    now = datetime.datetime.utcnow()
    async with db_helper.session_factory() as session:
        stmt = select(Subscription).where(Subscription.is_active == True, Subscription.expires_at <= now)
        expired_subs = (await session.execute(stmt)).scalars().all()
        
        for sub in expired_subs:
            sub.is_active = False
            await deactivate_user_on_servers(session, sub.user_id)
            try:
                await bot.send_message(chat_id=sub.user_id, text=f"❌ Срок действия вашей подписки {sub.plan_type.upper()} истек.")
            except Exception:
                pass
                
        # Размораживаем отложенную очередь тарифов
        stmt_pending = select(Subscription).where(Subscription.is_active == True, Subscription.is_pending == True).order_by(Subscription.created_at.asc())
        pending_subs = (await session.execute(stmt_pending)).scalars().all()
        
        for p_sub in pending_subs:
            # Проверяем, нет ли уже запущенного тарифа у этого юзера
            stmt_active_check = select(Subscription).where(Subscription.user_id == p_sub.user_id, Subscription.is_active == True, Subscription.is_pending == False, Subscription.expires_at > now)
            if not (await session.execute(stmt_active_check)).scalar_one_or_none():
                saved_days = (p_sub.expires_at - p_sub.created_at).days or 30
                p_sub.is_pending = False
                p_sub.expires_at = now + datetime.timedelta(days=saved_days)
                try:
                    await bot.send_message(chat_id=p_sub.user_id, text=f"🚀 Ваша отложенная подписка {p_sub.plan_type.upper()} автоматически активирована на {saved_days} дней!")
                except Exception:
                    pass
        await session.commit()

async def monthly_traffic_reset_job():
    """Запускается 1-го числа каждого месяца в 00:00: обновляет лимиты и обнуляет счетчики"""
    logger.info("🕒 Запуск ежемесячного воркера обновления лимитов трафика...")
    now = datetime.datetime.utcnow()
    import json
    
    async with db_helper.session_factory() as session:
        servers = (await session.execute(select(Server).where(Server.is_active == True))).scalars().all()
        active_subs = (await session.execute(select(Subscription).where(Subscription.is_active == True, Subscription.is_pending == False, Subscription.expires_at > now))).scalars().all()
        
        user_plans = {sub.user_id: sub.plan_type for sub in active_subs}

        for srv in servers:
            try:
                xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
                inbounds = await xui.get_inbounds()
                if not inbounds: continue
                
                for ib in inbounds:
                    settings = ib.get("settings", {})
                    if isinstance(settings, str):
                        try: settings = json.loads(settings)
                        except Exception: continue
                        
                    for client in settings.get("clients", []):
                        email = client.get("email", "")
                        if email.startswith("usr_"):
                            try: tg_id = int(email.split("_")[1])
                            except (IndexError, ValueError): continue
                                
                            user_plan = user_plans.get(tg_id, SubscriptionType.BASE)
                            gb_limit = 300 if user_plan == SubscriptionType.PREMIUM else 150
                            target_bytes = gb_limit * 1024 * 1024 * 1024
                            
                            client["totalGB"] = target_bytes
                            client["limitIp"] = 3
                            
                            await xui._request("POST", f"panel/api/clients/update/{client.get('id')}", json_data=client)
                            await xui._request("POST", f"panel/api/clients/resetTraffic/{email}")
            except Exception as e:
                logger.error(f"🚨 Ошибка ежемесячного сброса на ноде {srv.name}: {e}")
                continue
                
    logger.info("✅ Все счетчики трафика успешно обнулены, лимиты 150/300 ГБ обновлены!")

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_partner_subscriptions_job, trigger="interval", hours=1, args=[bot], id="check_subs")
    scheduler.add_job(monthly_traffic_reset_job, trigger="cron", day=1, hour=0, minute=0, id="monthly_traffic_reset")
    return scheduler

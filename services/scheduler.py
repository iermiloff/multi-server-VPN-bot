# services/scheduler.py — ЧАСТЬ 1
import logging
import datetime
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, PartnerChannel, Subscription, VPNKey, Server
from bot.services.xui import XUIMultiClient

logger = logging.getLogger(__name__)

async def deactivate_user_on_servers(session: AsyncSession, user_id: int):
    """Полностью удаляет клиента со всех серверов при отписке"""
    # Ищем все ключи (подписки), привязанные к пользователю
    stmt = (
        select(VPNKey)
        .join(Subscription)
        .where(Subscription.user_id == user_id)
        .options(selectinload(VPNKey.server))
    )
    result = await session.execute(stmt)
    keys = result.scalars().all()
    
    for key in keys:
        if key.server and key.server.is_active:
            # Инициализируем наш новый мультиклиент для конкретной ноды
            xui = XUIMultiClient(key.server.api_url, key.server.api_token)
            # Удаляем по email (каскадный метод v3.5.0 со стр. 9 API)
            success = await xui.delete_client(email=key.client_email)
            if success:
                logger.info(f"Удален клиент {key.client_email} с ноды {key.server.name}")
                await session.delete(key)

# services/scheduler.py — ЧАСТЬ 2
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.database.db_helper import db_helper

async def check_partner_subscriptions_job(bot: Bot):
    """Фоновая задача: проверяет условия по бонусным подпискам"""
    async with db_helper.session_factory() as session:
        # Получаем каналы, обязательные для проверки
        channels_res = await session.execute(select(PartnerChannel))
        channels = channels_res.scalars().all()
        if not channels:
            return

        # Ищем юзеров, у которых активен бонус от партнеров
        users_res = await session.execute(
            select(User)
            .where(User.has_active_partner_bonus == True)
            .options(selectinload(User.subscriptions))
        )
        users = users_res.scalars().all()

        for user in users:
            is_unsubscribed = False
            for ch in channels:
                try:
                    member = await bot.get_chat_member(chat_id=ch.channel_id, user_id=user.telegram_id)
                    if member.status in ["left", "kicked"]:
                        is_unsubscribed = True
                        break
                except Exception as e:
                    logger.warning(f"Ошибка проверки подписки {user.telegram_id} на {ch.channel_id}: {e}")
            
            # Если отписался — аннулируем
            if is_unsubscribed:
                logger.warning(f"Пользователь {user.telegram_id} отписался от партнеров! Отключаем.")
                user.has_active_partner_bonus = False
                
                # Деактивируем подписки в СУБД
                for sub in user.subscriptions:
                    sub.is_active = False
                
                # Удаляем с панелей 3x-ui
                await deactivate_user_on_servers(session, user.telegram_id)
                
                # Уведомляем пользователя
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text="⚠️ <b>Доступ заблокирован!</b>\n\nВы отписались от одного из каналов наших партнеров. Бесплатный месяц аннулирован."
                    )
                except Exception: pass
                
        await session.commit()

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Инициализация планировщика задач"""
    scheduler = AsyncIOScheduler()
    # Запускаем проверку каждые 2 часа (можно изменить по желанию)
    scheduler.add_job(
        check_partner_subscriptions_job,
        "interval",
        hours=2,
        args=[bot]
    )
    return scheduler

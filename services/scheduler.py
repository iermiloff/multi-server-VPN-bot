# services/scheduler.py
import logging
import datetime
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database.db_helper import db_helper
from database.models import User, Subscription, VPNKey
from services.xui import XUIMultiClient

logger = logging.getLogger(__name__)

async def deactivate_user_on_servers(session: AsyncSession, user_id: int):
    """Полностью удаляет клиента со всех серверов при отписке"""
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
            xui = XUIMultiClient(key.server.api_url, key.server.api_token)
            success = await xui.delete_client(email=key.client_email)
            if success:
                logger.info(f"Удален клиент {key.client_email} с ноды {key.server.name}")
                await session.delete(key)

async def check_partner_subscriptions_job(bot: Bot):
    """Фоновая задача: проверяет подписки юзеров на ИХ персональный список спонсоров"""
    async with db_helper.session_factory() as session:
        # Загружаем юзеров вместе с закрепленными за ними каналами
        users_res = await session.execute(
            select(User)
            .where(User.has_active_partner_bonus == True)
            .options(selectinload(User.subscriptions), selectinload(User.required_channels))
        )
        users = users_res.scalars().all()

        for user in users:
            # Если за юзером по какой-то причине нет закрепленных каналов, пропускаем
            if not user.required_channels:
                continue

            is_unsubscribed = False
            
            # Проверяем строго те каналы, на которые он подписывался изначально
            for ch in user.required_channels:
                try:
                    member = await bot.get_chat_member(chat_id=ch.channel_id, user_id=user.telegram_id)
                    if member.status in ["left", "kicked"]:
                        is_unsubscribed = True
                        break
                except Exception as e:
                    # ПРЕЗУМПЦИЯ НЕВИНОВНОСТИ: Если спонсор ушел и удалил бота из админов,
                    # мы НЕ наказываем пользователя. Проверка этого канала просто пропускается.
                    logger.warning(
                        f"Канал {ch.channel_id} недоступен для проверки бота. "
                        f"Пропускаем этот канал для юзера {user.telegram_id}. Ошибка: {e}"
                    )
                    continue 
            
            # Если факт отписки от работающего канала подтвердился — отключаем
            if is_unsubscribed:
                logger.warning(f"Пользователь {user.telegram_id} отписался от партнеров! Отключаем.")
                user.has_active_partner_bonus = False
                user.required_channels = [] # Сбрасываем связи
                
                for sub in user.subscriptions:
                    sub.is_active = False
                
                await deactivate_user_on_servers(session, user.telegram_id)
                
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text="⚠️ <b>Доступ заблокирован!</b>\n\nВы отписались от одного из каналов наших партнеров. Бесплатный месяц аннулирован."
                    )
                except Exception: 
                    pass
                
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

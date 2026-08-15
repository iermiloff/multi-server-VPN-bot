import logging
import datetime
import uuid
from urllib.parse import urlparse
from typing import Any

from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import Server, TariffInbound, PartnerChannel, SubscriptionType, User, Subscription, VPNKey
from services.xui import XUIMultiClient
from services.scheduler import deactivate_user_on_servers

logger = logging.getLogger(__name__)
admin_router = Router()

admin_router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

class AdminServerStates(StatesGroup):
    wait_for_name = State()
    wait_for_url = State()
    wait_for_token = State()
    wait_for_sub_port = State()
    wait_for_sub_path = State()

class AdminServerEditStates(StatesGroup):
    wait_for_name = State()
    wait_for_url = State()
    wait_for_token = State()
    wait_for_sub_port = State()
    wait_for_sub_path = State()

class AdminPartnerStates(StatesGroup):
    wait_for_id = State()
    wait_for_name = State()
    wait_for_link = State()
    wait_for_type = State()

class AdminCrmStates(StatesGroup):
    wait_for_search_id = State()
    wait_for_days_count = State()

async def get_admin_dashboard_text(db_session: AsyncSession) -> str:
    total_users = await db_session.scalar(select(func.count(User.telegram_id)))
    now = datetime.datetime.utcnow()
    active_subs = await db_session.scalar(select(func.count(Subscription.id)).where(Subscription.is_active == True, Subscription.is_pending == False, Subscription.expires_at > now))
    total_servers = await db_session.scalar(select(func.count(Server.id)))
    total_channels = await db_session.scalar(select(func.count(PartnerChannel.id)))
    return (
        f"👑 <b>Панель управления {config.BRAND_NAME}</b>\n\n"
        f"📊 <b>Статистика СУБД:</b>\n"
        f"├ Всего пользователей: <code>{total_users or 0}</code>\n"
        f"├ Активных подписок: <code>{active_subs or 0}</code>\n"
        f"├ Нод 3x-ui сети: <code>{total_servers or 0}</code>\n"
        f"└ Каналов-партнеров: <code>{total_channels or 0}</code>\n\n"
        f"<i>Используйте меню ниже для администрирования:</i>"
    )

def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Управление пользователями (CRM)", callback_data="adm_crm_menu")],
        [InlineKeyboardButton(text="💰 Выгрузить выплаты (Pro)", callback_data="adm_ref_payouts_list")],
        [InlineKeyboardButton(text="🖥 Управление серверами 3x-ui", callback_data="adm_servers_list")],
        [InlineKeyboardButton(text="🔗 Моя партнерская ссылка", callback_data="adm_my_ref_link")],
        [InlineKeyboardButton(text="📢 Настройка каналов и спонсоров", callback_data="adm_partners_list")]
    ])
# handlers/admin.py — ХЕНДЛЕР ПАРТНЕРСКОГО КАБИНЕТА ДЛЯ АДМИНИСТРАЦИИ

# handlers/admin.py — БЕЗОПАСНЫЙ ХЕНДЛЕР С АВТОРЕГИСТРАЦИЕЙ АДМИНИСТРАТОРА

@admin_router.callback_query(F.data == "adm_my_ref_link")
async def cb_adm_my_ref_link(callback: CallbackQuery, db_session: AsyncSession):
    """Вывод партнерской ссылки менеджера/админа с авторегистрацией в СУБД"""
    await callback.answer()
    user_id = callback.from_user.id
    
    # 1. Загружаем данные админа из СУБД
    user = (await db_session.execute(
        select(User).where(User.telegram_id == user_id)
    )).scalar_one_or_none()
    
    # ИСПРАВЛЕНО: Если админа нет в таблице users, регистрируем его на лету!
    if not user:
        user = User(
            telegram_id=user_id,
            username=callback.from_user.username,
            referred_by=None,
            registered_at=datetime.datetime.utcnow()
        )
        db_session.add(user)
        await db_session.flush()  # Мгновенно выталкиваем в память сессии
    
    # Считаем рефералов менеджера
    ref_count = await db_session.scalar(
        select(func.count(User.telegram_id)).where(User.referred_by == user_id)
    ) or 0
    
    # Сборка ссылки
    bot_res = await callback.bot.get_me()
    ref_link = f"https://t.me/{bot_res.username}?start=ref{user_id}"
    
    # Проверка статуса (теперь ошибка NoneType полностью невозможна)
    ref_status = "👑 PRO-Партнер (10% CPA в USD)" if user.is_pro_ref else "👥 Обычный (1:1 бонусные дни)"
    wallet_str = f"<code>{user.crypto_wallet}</code>" if user.crypto_wallet else "<i>не привязан</i>"
    
    text = (
        f"🔗 <b>Партнерский кабинет менеджера</b>\n\n"
        f"• <b>Твой статус:</b> {ref_status}\n"
        f"• <b>Приглашено рефералов:</b> <code>{ref_count}</code> чел.\n"
        f"• <b>Текущий баланс PRO:</b> <code>${user.partner_balance_usd or 0.0:.2f}</code>\n"
        f"• <b>Выплатной TON-кошелек:</b> {wallet_str}\n\n"
        f"📋 <b>Твоя персональная ссылка для привлечения клиентов:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"💡 <i>Нажмите на ссылку выше, чтобы скопировать её. Все переходы и оплаты будут "
        f"фиксироваться за вашим ID!</i>"
    )
    
    kb = [[InlineKeyboardButton(text="◀️ Назад в админку", callback_data="adm_main_menu")]]
    
    try:
        await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception:
        pass

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, db_session: AsyncSession):
    await message.answer(text=await get_admin_dashboard_text(db_session), reply_markup=get_admin_main_keyboard())

@admin_router.callback_query(F.data == "adm_main_menu")
async def cb_admin_main_menu(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    await callback.message.edit_text(text=await get_admin_dashboard_text(db_session), reply_markup=get_admin_main_keyboard())


@admin_router.callback_query(F.data.startswith("adm_crm_menu"))
async def cb_adm_crm_menu(callback: CallbackQuery, db_session: AsyncSession):
    """Постраничное отображение базы пользователей для удобного администрирования"""
    await callback.answer()
    
    # Парсим номер страницы из callback_data (если суффикса нет — открываем страницу 0)
    parts = callback.data.split("_")
    page = int(parts[-1]) if parts[-1].isdigit() else 0
    
    limit = 5  # Выводим аккуратно по 5 человек, чтобы кнопки не слипались
    offset = page * limit
    
    # Считаем общее количество живых клиентов в базе данных
    total_users = await db_session.scalar(select(func.count(User.telegram_id))) or 0
    total_pages = (total_users + limit - 1) // limit
    
    # Делаем выборку среза пользователей под текущую страницу
    res = await db_session.execute(
        select(User)
        .order_by(User.registered_at.desc())
        .limit(limit)
        .offset(offset)
    )
    users = res.scalars().all()
    
    text = (
        f"👥 <b>CRM: Управление пользователями</b>\n"
        f"📋 Страница: <code>{page + 1}</code> из <code>{max(1, total_pages)}</code>\n"
        f"📊 Всего клиентов в СУБД: <code>{total_users}</code>\n\n"
        f"Последние зарегистрированные аккаунты:"
    )
    
    kb = []
    for u in users:
        u_str = f" (@{u.username})" if u.username else " (нет юзернейма)"
        kb.append([InlineKeyboardButton(text=f"⚙️ {u.telegram_id}{u_str}", callback_data=f"adm_user_card_{u.telegram_id}")])
    
    # Навигационный ряд стрелочек (появляются автоматически в зависимости от размера базы)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_crm_menu_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"adm_crm_menu_{page + 1}"))
        
    if nav_row:
        kb.append(nav_row)
        
    # Кнопки поиска и возврата в главное меню админки
    kb.append([
        InlineKeyboardButton(text="🔍 Поиск по ID", callback_data="adm_user_search_start"), 
        InlineKeyboardButton(text="◀️ Меню", callback_data="adm_main_menu")
    ])
    
    try:
        await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception:
        pass


@admin_router.callback_query(F.data == "adm_user_search_start")
async def cb_adm_user_search_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminCrmStates.wait_for_search_id)
    await callback.message.edit_text("🔍 Введите точный цифровой <b>Telegram ID</b> пользователя:")

@admin_router.message(AdminCrmStates.wait_for_search_id)
async def msg_adm_user_search_id(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    try: target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен состоять только из цифр:"); return
    await state.clear()
    if not (await db_session.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none():
        await message.answer("❌ Пользователь не найден.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В CRM", callback_data="adm_crm_menu")]]))
        return
    await render_user_card(message, db_session, target_id)

async def render_user_card(message_obj: Any, db_session: AsyncSession, target_id: int, is_callback: bool = False):
    stmt = select(User).where(User.telegram_id == target_id).options(selectinload(User.subscriptions).selectinload(Subscription.keys))
    user = (await db_session.execute(stmt)).scalar_one()
    now = datetime.datetime.utcnow()
    
    u_str = f"@{user.username}" if user.username else "нет"
    wallet_str = f"<code>{user.crypto_wallet}</code>" if user.crypto_wallet else "не указан"
    ref_status = "👑 PRO (10% CPA)" if user.is_pro_ref else "👥 Обычный (1:1 дни)"
    
    text = (
        f"👤 <b>Карточка пользователя: {user.telegram_id}</b>\n\n"
        f"• <b>Юзернейм:</b> {u_str}\n"
        f"• <b>Регистрация:</b> {user.registered_at.strftime('%d.%m.%Y')}\n"
        f"• <b>Реф. программа:</b> {ref_status}\n"
        f"• <b>Баланс PRO:</b> ${user.partner_balance_usd or 0.0:.2f}\n"
        f"• <b>Кошелек TON:</b> {wallet_str}\n\n"
        f"📋 <b>Действующие подписки:</b>\n"
    )
    for s in user.subscriptions:
        if s.is_active:
            status = " В очереди" if s.is_pending else " Активна" if s.expires_at > now else "❌ Истекла"
            text += f" ├ <code>{s.plan_type.upper()}</code> -> {status} (До: {s.expires_at.strftime('%d.%m.%Y %H:%M')})\n"
            
    ref_btn_text = "👥 Вернуть обычный статус" if user.is_pro_ref else "👑 Сделать PRO-партнером (10%)"
    kb = [
        [InlineKeyboardButton(text="➕ Начислить / Продлить подписку", callback_data=f"adm_crm_add_days_{target_id}")],
        [InlineKeyboardButton(text="🛑 Аннулировать все подписки", callback_data=f"adm_crm_wipe_subs_{target_id}")],
        [InlineKeyboardButton(text=ref_btn_text, callback_data=f"adm_crm_toggle_pro_{target_id}")],
        [InlineKeyboardButton(text="🗑 Полностью удалить клиента из СУБД", callback_data=f"adm_crm_delete_user_{target_id}")],
        [InlineKeyboardButton(text="◀️ Назад в CRM", callback_data="adm_crm_menu")]
    ]
    if is_callback: await message_obj.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else: await message_obj.answer(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data.startswith("adm_user_card_"))
async def cb_adm_user_card(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    await render_user_card(callback.message, db_session, int(callback.data.split("_")[-1]), is_callback=True)

@admin_router.callback_query(F.data.startswith("adm_crm_add_days_"))
async def cb_adm_crm_add_days_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    target_id = int(callback.data.split("_")[-1])
    await state.update_data(target_id=target_id)
    await state.set_state(AdminCrmStates.wait_for_days_count)
    kb = [[InlineKeyboardButton(text="⚙️ Тариф BASE", callback_data="add_plan_base")], [InlineKeyboardButton(text="👑 Тариф PREMIUM", callback_data="add_plan_premium")]]
    await callback.message.edit_text(text=f"Выбери тариф для начисления пользователю <code>{target_id}</code>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(AdminCrmStates.wait_for_days_count, F.data.startswith("add_plan_"))
async def cb_adm_crm_select_plan(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    plan_type = callback.data.split("_")[-1]
    await state.update_data(plan_type=plan_type)
    data = await state.get_data()
    await callback.message.edit_text(text=f"Введите количество дней подписки (числом) для тарифа <b>{plan_type.upper()}</b> пользователю <code>{data['target_id']}</code>:")

@admin_router.message(AdminCrmStates.wait_for_days_count)
async def msg_adm_crm_save_days(message: Message, state: FSMContext, db_session: AsyncSession):
    """Ручное начисление дней подписки из CRM админки с умной очередью тарифов"""
    if message.text.startswith("/"):
        await state.clear()
        await cmd_admin(message, db_session)
        return
        
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число дней:")
        return
 
    data = await state.get_data()
    await state.clear()
    
    target_id, plan_type = data["target_id"], data["plan_type"]
    now = datetime.datetime.utcnow()
    
    # Извлекаем последние подписки пользователя для расчета заморозок
    base_sub = (await db_session.execute(
        select(Subscription)
        .where(Subscription.user_id == target_id, Subscription.plan_type == SubscriptionType.BASE)
        .order_by(Subscription.expires_at.desc()).limit(1)
    )).scalar_one_or_none()
    
    prem_sub = (await db_session.execute(
        select(Subscription)
        .where(Subscription.user_id == target_id, Subscription.plan_type == SubscriptionType.PREMIUM)
        .order_by(Subscription.expires_at.desc()).limit(1)
    )).scalar_one_or_none()
    
    base_active = base_sub and base_sub.is_active and base_sub.expires_at > now and not base_sub.is_pending
    prem_active = prem_sub and prem_sub.is_active and prem_sub.expires_at > now and not prem_sub.is_pending
    msg_status = ""

    # СЦЕНАРИЙ 1: Апгрейд подписки (Начисляем PREMIUM, замораживаем BASE в очередь)
    if plan_type == "premium" and base_active:
        base_sub.is_pending = True
        if prem_sub:
            prem_sub.expires_at = now + datetime.timedelta(days=days)
            prem_sub.is_pending = False
            prem_sub.is_active = True
        else:
            prem_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.PREMIUM, expires_at=now + datetime.timedelta(days=days), is_pending=False)
            db_session.add(prem_sub)
        msg_status = "PREMIUM активирован! Базовый тариф заморожен в очередь."
        
    # СЦЕНАРИЙ 2: Даунгрейд подписки (Начисляем BASE при активном PREMIUM -> отправляем BASE в очередь)
    elif plan_type == "base" and prem_active:
        if base_sub:
            if base_sub.is_pending:
                base_sub.expires_at += datetime.timedelta(days=days)
            else:
                base_sub.expires_at = now + datetime.timedelta(days=days)
                base_sub.is_pending = True
        else:
            base_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.BASE, expires_at=now + datetime.timedelta(days=days), is_pending=True)
            db_session.add(base_sub)
        msg_status = "PREMIUM активен. Новый базовый тариф убран в отложенную очередь."
        
    # СЦЕНАРИЙ 3: Прямое продление текущего активного или истекшего тарифа
    else:
        target_sub = prem_sub if plan_type == "premium" else base_sub
        if target_sub and target_sub.expires_at > now and not target_sub.is_pending:
            target_sub.expires_at += datetime.timedelta(days=days)
        else:
            if plan_type == "premium":
                prem_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.PREMIUM, expires_at=now + datetime.timedelta(days=days), is_pending=False)
                db_session.add(prem_sub)
            else:
                base_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.BASE, expires_at=now + datetime.timedelta(days=days), is_pending=False)
                db_session.add(base_sub)
        msg_status = f"Успешное прямое продление тарифа {plan_type.upper()}."

    await db_session.flush()
    nodes_synced = 0
    active_subscription_object = prem_sub if (prem_sub and not prem_sub.is_pending and prem_sub.expires_at > now) else base_sub
    now_active_plan = active_subscription_object.plan_type if active_subscription_object else None
# handlers/admin.py — ОКОНЧАНИЕ ФУНКЦИИ msg_adm_crm_save_days

    if now_active_plan:
        servers = (await db_session.execute(select(Server).where(Server.is_active == True))).scalars().all()
        existing_keys = {k.server_id: k for k in (await db_session.execute(select(VPNKey).join(Subscription).where(Subscription.user_id == target_id))).scalars().all()}
        active_end_date = active_subscription_object.expires_at
        expiry_timestamp = int(active_end_date.timestamp() * 1000)
        
        keys_list = list(existing_keys.values()) if existing_keys else []
        first_key = keys_list[0] if (keys_list and len(channels_list if 'channels_list' in locals() else keys_list) > 0) else None
        shared_email = first_key.client_email if first_key else f"usr_{target_id}_{uuid.uuid4().hex[:4]}"
        shared_sub_id = first_key.sub_id if first_key else uuid.uuid4().hex

        for srv in servers:
            try:
                ib_stmt = select(TariffInbound).where(
                    TariffInbound.server_id == srv.id, 
                    TariffInbound.plan_type.in_(["base", "premium"] if now_active_plan == SubscriptionType.PREMIUM else ["base"])
                )
                inbound_ids = [ib.inbound_id for ib in (await db_session.execute(ib_stmt)).scalars().all()]
                if not inbound_ids: 
                    continue
                    
                xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
                
                # Если ключа для этой подписки на сервере еще нет — создаем с нуля
                if srv.id not in existing_keys:
                    # Передаем shared_email и shared_sub_id на все ноды
                    if await xui.add_client(email=shared_email, sub_id=shared_sub_id, inbound_ids=inbound_ids, expires_days=days, plan_type=plan_type):
                        new_crm_key = VPNKey(
                            subscription_id=active_subscription_object.id, server_id=srv.id, 
                            client_email=shared_email, sub_id=shared_sub_id, config_data=shared_sub_id
                        )
                        db_session.add(new_crm_key)
                        
                        # Мгновенно выталкиваем в СУБД, обновляя словарь для блокировки дубликатов
                        await db_session.flush()
                        existing_keys[srv.id] = new_crm_key
                        nodes_synced += 1
                else:
                    # Если ключ уже был (продление текущего тарифа), просто обновляем лимиты и время
                    current_key = existing_keys[srv.id]
                    target_bytes = (300 if now_active_plan == SubscriptionType.PREMIUM else 150) * 1024 * 1024 * 1024
                    
                    await xui.attach_client_inbounds(email=current_key.client_email, inbound_ids=inbound_ids)
                    res_get = await xui._request("GET", f"panel/api/clients/get/{current_key.client_email}")
                    
                    if res_get and res_get.get("success") and res_get.get("obj"):
                        p_data = res_get.get("obj")
                        payload_up = {
                            "id": p_data.get("id"), "email": current_key.client_email, "totalGB": target_bytes, 
                            "expiryTime": expiry_timestamp, "subId": p_data.get("subId"), "limitIp": 3, "enable": True
                        }
                        await xui._request("POST", f"panel/api/clients/update/{current_key.client_email}", json_data=payload_up)
                        await xui._request("POST", f"panel/api/clients/resetTraffic/{current_key.client_email}")
                    else:
                        await xui.update_client_expiry(current_key.client_email, expiry_time=expiry_timestamp)
                    nodes_synced += 1
                    
            except Exception as srv_err:
                logger.error(f"🚨 Сбой ручного пуша на ноду {srv.name} (ID: {srv.id}): {srv_err}")
                continue

    await db_session.commit()
    await message.answer(text=f"👑 <b>Синхронизация завершена!</b>\n\n• Начислено: <code>{plan_type.upper()}</code> на <b>{days} дн.</b>\n• Статус: <i>{msg_status}</i>\n• Синхронизировано нод: <b>{nodes_synced} шт.</b>")
    await render_user_card(message, db_session, target_id)


@admin_router.callback_query(F.data.startswith("adm_crm_wipe_subs_"))
async def cb_adm_crm_wipe_subs(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    target_id = int(callback.data.split("_")[-1])
    await deactivate_user_on_servers(db_session, target_id)
    for s in (await db_session.execute(select(Subscription).where(Subscription.user_id == target_id))).scalars().all(): s.is_active = False
    await db_session.commit(); await callback.answer("Все доступы аннулированы!", show_alert=True)
    await render_user_card(callback.message, db_session, target_id, is_callback=True)

@admin_router.callback_query(F.data.startswith("adm_crm_delete_user_"))
async def cb_adm_crm_delete_user_finalize(callback: CallbackQuery, db_session: AsyncSession):
    target_id = int(callback.data.split("_")[-1])
    try: await deactivate_user_on_servers(db_session, target_id)
    except Exception as e: logger.error(f"Каскад-ошибка: {e}")
    user_obj = (await db_session.execute(select(User).where(User.telegram_id == target_id).options(selectinload(User.subscriptions)))).scalar_one_or_none()
    if user_obj:
        for sub in user_obj.subscriptions: await db_session.delete(sub)
        await db_session.delete(user_obj); await db_session.commit()
        await callback.answer("Клиент полностью удален!", show_alert=True)
    else: await callback.answer("❌ Не найден.", show_alert=True)
    await cb_adm_crm_menu(callback, db_session)

@admin_router.callback_query(F.data == "adm_servers_list")
async def cb_adm_servers_list(callback: CallbackQuery, db_session: AsyncSession):
    if callback: await callback.answer()
    servers = (await db_session.execute(select(Server))).scalars().all()
    text = "🖥 <b>Список нод сети 3x-ui:</b>\n\n"
    kb = []
    for s in servers:
        status = "✅" if s.is_active else "❌"
        text += f"{status} <b>{s.name}</b> (Port: {s.sub_port}, Path: /{s.sub_path})\n<code>{s.api_url}</code>\n\n"
        kb.append([InlineKeyboardButton(text=f"⚙️ Настроить {s.name}", callback_data=f"adm_srv_manage_{s.id}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="adm_srv_add_start")])
    kb.append([InlineKeyboardButton(text="◀️ Панель админа", callback_data="adm_main_menu")])
    if callback: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)

@admin_router.callback_query(F.data == "adm_srv_add_start")
async def cb_adm_srv_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.set_state(AdminServerStates.wait_for_name)
    await callback.message.edit_text("<b>Шаг 1/5:</b> Введите название ноды:")

@admin_router.message(AdminServerStates.wait_for_name)
async def msg_srv_name(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(name=message.text.strip()); await state.set_state(AdminServerStates.wait_for_url)
    await message.answer("<b>Шаг 2/5:</b> Введите URL API панели:")

@admin_router.message(AdminServerStates.wait_for_url)
async def msg_srv_url(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(api_url=message.text.strip()); await state.set_state(AdminServerStates.wait_for_token)
    await message.answer("<b>Шаг 3/5:</b> Введите API Token (Bearer):")

@admin_router.message(AdminServerStates.wait_for_token)
async def msg_srv_token(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(api_token=message.text.strip()); await state.set_state(AdminServerStates.wait_for_sub_port)
    await message.answer("<b>Шаг 4/5:</b> Введите порт подписки (дефолт 2096):")

@admin_router.message(AdminServerStates.wait_for_sub_port)
async def msg_srv_sub_port(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    try: sub_port = int(message.text.strip())
    except ValueError: await message.answer("❌ Должно быть числом!:"); return
    await state.update_data(sub_port=sub_port); await state.set_state(AdminServerStates.wait_for_sub_path)
    await message.answer("<b>Шаг 5/5:</b> Введите путь маскировки (например stats):")

@admin_router.message(AdminServerStates.wait_for_sub_path)
async def msg_srv_sub_path(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    sub_path = message.text.strip().strip("/")
    data = await state.get_data(); await state.clear()
    db_session.add(Server(name=data["name"], api_url=data["api_url"], api_token=data["api_token"], sub_port=data["sub_port"], sub_path=sub_path))
    await db_session.commit()
    t, km = await cb_adm_servers_list(None, db_session)
    await message.answer(text=f"✅ <b>Сервер сохранен!</b>\n\n{t}", reply_markup=km)


@admin_router.callback_query(F.data.startswith("adm_srv_manage_"))
async def cb_adm_srv_manage(callback: CallbackQuery, db_session: AsyncSession):
    """Кабинет управления нодой с кнопкой 1-click синхронизации и цветными маркерами"""
    if callback: await callback.answer()
    server_id = int(callback.data.split("_")[-1])
    srv = (await db_session.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    
    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    all_inbounds = await xui.get_inbounds()
    
    if not all_inbounds:
        await callback.message.edit_text(
            text=f"❌ <b>Нода {srv.name} недоступна по API!</b>", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"adm_srv_edit_{srv.id}")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_servers_list")]
            ])
        )
        return
        
    db_inbounds = {i.inbound_id: i.plan_type for i in (await db_session.execute(select(TariffInbound).where(TariffInbound.server_id == srv.id))).scalars().all()}
    text = f"🖥 <b>Управление нодой: {srv.name}</b>\n└ URL: <code>{srv.api_url}</code>\n\n⚙️ <b>Настройка тарифов портов Xray:</b>"
    
    kb = [
        [InlineKeyboardButton(text="🔄 Синхронизировать активных клиентов", callback_data=f"adm_srv_sync_users_{srv.id}")],
        [InlineKeyboardButton(text="✏️ Редактировать ноду", callback_data=f"adm_srv_edit_{srv.id}"), 
         InlineKeyboardButton(text="🗑 Удалить ноду", callback_data=f"adm_srv_del_{srv.id}")]
    ]
    
    for ib in all_inbounds:
        ib_id = int(ib.get("id"))
        current_plan = db_inbounds.get(ib_id, "❌ОТКЛЮЧЕН")
        badge = "🟢 BASE" if current_plan == "base" else "🔴 PREMIUM" if current_plan == "premium" else "⚫ НЕАКТИВЕН"
        kb.append([InlineKeyboardButton(text=f"[{ib_id}] {ib.get('protocol','').upper()} ({ib.get('remark','')}) -> {badge}", callback_data=f"adm_toggle_ib_{srv.id}_{ib_id}")])
        
    kb.append([InlineKeyboardButton(text="◀️ Назад к серверам", callback_data="adm_servers_list")])
    try: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception: pass


@admin_router.callback_query(F.data.startswith("adm_srv_sync_users_"))
async def cb_adm_srv_sync_users(callback: CallbackQuery, db_session: AsyncSession):
    """Массовый накат клиентов на новую ноду с абсолютной защитой по user_id"""
    await callback.answer()
    server_id = int(callback.data.split("_")[-1])
    now = datetime.datetime.utcnow()
    
    srv = (await db_session.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    db_inbounds = (await db_session.execute(select(TariffInbound).where(TariffInbound.server_id == server_id))).scalars().all()
    
    base_inbound_ids = [ib.inbound_id for ib in db_inbounds if ib.plan_type == "base"]
    premium_inbound_ids = [ib.inbound_id for ib in db_inbounds if ib.plan_type == "premium"]
    if not base_inbound_ids and not premium_inbound_ids:
        await callback.message.answer("⚠️ Сначала настройте тарифы портов xray ниже!", show_alert=True)
        return
        
    await callback.message.edit_text(f"⏳ <b>Массовая синхронизация...</b>\nНакатываем базу на ноду <code>{srv.name}</code>.")
    
    active_subs = (await db_session.execute(select(Subscription).where(
        Subscription.is_active == True, Subscription.is_pending == False, Subscription.expires_at > now
    ).options(selectinload(Subscription.keys)))).scalars().all()
    
    synced_count = 0
    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    
    for sub in active_subs:
        try:
            # 🔥 ЖЕСТКАЯ ПРОВЕРКА ПО ВСЕЙ ТАБЛИЦЕ КЛЮЧЕЙ:
            # Ищем, есть ли у этого конкретного пользователя (sub.user_id) ХОТЬ ОДИН ключ на ЭТОМ сервере (server_id)
            key_exists = await db_session.scalar(
                select(func.count(VPNKey.id))
                .join(Subscription)
                .where(
                    Subscription.user_id == sub.user_id,
                    VPNKey.server_id == server_id
                )
            ) or 0
            
            # Если у пользователя уже нарезан доступ на этой ноде — намертво пропускаем его, исключая дубли!
            if key_exists > 0:
                continue
                
            existing_key = sub.keys[0] if (sub.keys and len(sub.keys) > 0) else None
            email = existing_key.client_email if existing_key else f"usr_{sub.user_id}_{uuid.uuid4().hex[:4]}"
            sub_id = existing_key.sub_id if existing_key else uuid.uuid4().hex
            
            target_inbounds = (base_inbound_ids + premium_inbound_ids) if sub.plan_type == SubscriptionType.PREMIUM else base_inbound_ids
            if not target_inbounds: 
                continue
            
            days_left = max(1, (sub.expires_at - now).days)
            if await xui.add_client(email=email, sub_id=sub_id, inbound_ids=target_inbounds, expires_days=days_left, plan_type=sub.plan_type):
                db_session.add(VPNKey(subscription_id=sub.id, server_id=server_id, client_email=email, sub_id=sub_id, config_data=sub_id))
                
                # Мгновенно выталкиваем транзакцию в СУБД, чтобы count() при следующем шаге увидел запись
                await db_session.flush()
                synced_count += 1
                
        except Exception as push_err:
            logger.error(f"🚨 Ошибка наката реферала {sub.user_id} на ноду {server_id}: {push_err}")
            continue
            
    await db_session.commit()
    await callback.message.answer(text=f"✅ <b>Синхронизация завершена!</b>\nНа ноду <code>{srv.name}</code> добавлено <b>{synced_count} клиентов</b>.")
    await cb_adm_srv_manage(callback, db_session)

@admin_router.callback_query(F.data.startswith("adm_crm_toggle_pro_"))
async def cb_adm_crm_toggle_pro(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer(); target_id = int(callback.data.split("_")[-1])
    user = (await db_session.execute(select(User).where(User.telegram_id == target_id))).scalar_one()
    user.is_pro_ref = not user.is_pro_ref; await db_session.commit()
    await callback.answer(f"Статус изменен!", show_alert=True)
    await render_user_card(callback.message, db_session, target_id, is_callback=True)


@admin_router.callback_query(F.data == "adm_ref_payouts_list")
async def cb_adm_ref_payouts_list(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    partners = (await db_session.execute(select(User).where(User.is_pro_ref == True, User.partner_balance_usd > 0.0).order_by(User.partner_balance_usd.desc()))).scalars().all()
    text = "💰 <b>Ведомость выплат Pro-партнерам:</b>\n\n"
    if not partners: text += "<i>Начислений к выплате не найдено.</i>"
    else:
        total = 0.0
        for i, p in enumerate(partners, 1):
            u_str = f"@{p.username}" if p.username else f"ID: {p.telegram_id}"
            w_str = f"<code>{p.crypto_wallet}</code>" if p.crypto_wallet else "⚠️ <i>не указан</i>"
            text += f"{i}. 👤 <b>{u_str}</b>\n├ К выплате: <b>${p.partner_balance_usd:.2f}</b>\n└ TON: {w_str}\n\n"
            total += p.partner_balance_usd
        text += f"📊 <b>Итого к выплате по сети:</b> <code>${total:.2f}</code>"
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Меню", callback_data="adm_main_menu")]]))


@admin_router.callback_query(F.data.startswith("adm_toggle_ib_"))
async def cb_adm_toggle_ib(callback: CallbackQuery, db_session: AsyncSession):
    """Мгновенное переключение тарифов инбаундов Xray с автообновлением экрана в реальном эфире"""
    raw_data = callback.data.replace("adm_toggle_ib_", "")
    parts = raw_data.split("_", 1)
    server_id = int(parts[0])
    ib_id = int(parts[1].strip())
    
    srv = (await db_session.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    all_inbounds = await xui.get_inbounds()
    target_ib = next((i for i in all_inbounds if i.get("id") == ib_id), None)
    ib_record = (await db_session.execute(select(TariffInbound).where(TariffInbound.server_id == server_id, TariffInbound.inbound_id == ib_id))).scalar_one_or_none()
    
    if not ib_record:
        db_session.add(TariffInbound(server_id=server_id, plan_type=SubscriptionType.BASE, inbound_id=ib_id, protocol_name=target_ib.get("protocol", "unknown"), port=target_ib.get("port", 0), remark=target_ib.get("remark", "")))
        await callback.answer("🟩 Переведено в тариф BASE")
    elif ib_record.plan_type == SubscriptionType.BASE:
        ib_record.plan_type = SubscriptionType.PREMIUM
        await callback.answer("👑 Переведено в тариф PREMIUM")
    else:
        await db_session.delete(ib_record)
        await callback.answer("⚫ Инбаунд деактивирован")
        
    await db_session.commit()
    db_inbounds_new = {i.inbound_id: i.plan_type for i in (await db_session.execute(select(TariffInbound).where(TariffInbound.server_id == server_id))).scalars().all()}
    text = f"🖥 <b>Управление нодой: {srv.name}</b>\n└ URL: <code>{srv.api_url}</code>\n\n⚙️ <b>Настройка тарифов портов Xray:</b>"
    
    kb = [
        [InlineKeyboardButton(text="🔄 Синхронизировать активных клиентов", callback_data=f"adm_srv_sync_users_{srv.id}")],
        [InlineKeyboardButton(text="✏️ Редактировать ноду", callback_data=f"adm_srv_edit_{srv.id}"), 
         InlineKeyboardButton(text="🗑 Удалить ноду", callback_data=f"adm_srv_del_{srv.id}")]
    ]
    for ib in all_inbounds:
        current_ib_id = int(ib.get("id"))
        current_plan = db_inbounds_new.get(current_ib_id, "❌ОТКЛЮЧЕН")
        badge = "🟢 BASE" if current_plan == "base" else "🔴 PREMIUM" if current_plan == "premium" else "⚫ НЕАКТИВЕН"
        kb.append([InlineKeyboardButton(text=f"[{current_ib_id}] {ib.get('protocol','').upper()} ({ib.get('remark','')}) -> {badge}", callback_data=f"adm_toggle_ib_{srv.id}_{current_ib_id}")])
        
    kb.append([InlineKeyboardButton(text="◀️ Назад к серверам", callback_data="adm_servers_list")])
    try: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception: pass


@admin_router.callback_query(F.data == "adm_partners_list")
async def cb_adm_partners_list(callback: CallbackQuery, db_session: AsyncSession):
    if callback: await callback.answer()
    channels = (await db_session.execute(select(PartnerChannel))).scalars().all()
    text = "📢 <b>Управление каналами и подписками:</b>\n\n"
    kb = []
    if not channels: text += "<i>Список каналов пока пуст.</i>"
    else:
        for ch in channels:
            role = " Саппорт" if ch.is_required else " Спонсор"
            text += f"• <b>{ch.channel_name}</b> [{role}]\nID: <code>{ch.channel_id}</code>\n\n"
            kb.append([InlineKeyboardButton(text=f"❌ Удалить {ch.channel_name}", callback_data=f"adm_part_del_{ch.id}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="adm_part_add_start"), InlineKeyboardButton(text=" Меню", callback_data="adm_main_menu")])
    if callback: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@admin_router.callback_query(F.data == "adm_part_add_start")
async def cb_adm_part_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await state.set_state(AdminPartnerStates.wait_for_id)
    await callback.message.edit_text("<b>Шаг 1/4:</b> Введите цифровой <b>Telegram ID канала</b>:")


@admin_router.message(AdminPartnerStates.wait_for_id)
async def msg_part_id(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    try: ch_id = int(message.text.strip())
    except ValueError: await message.answer("❌ ID должен быть числом!:"); return
    await state.update_data(channel_id=ch_id); await state.set_state(AdminPartnerStates.wait_for_name)
    await message.answer("<b>Шаг 2/4:</b> Введите название канала:")


@admin_router.message(AdminPartnerStates.wait_for_name)
async def msg_part_name(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(channel_name=message.text.strip()); await state.set_state(AdminPartnerStates.wait_for_link)
    await message.answer("<b>Шаг 3/4:</b> Введите ссылку-приглашение:")


@admin_router.message(AdminPartnerStates.wait_for_link)
async def msg_part_link(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"): await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(invite_link=message.text.strip()); await state.set_state(AdminPartnerStates.wait_for_type)
    kb = [[InlineKeyboardButton(text=" Спонсор", callback_data="role_sponsor"), InlineKeyboardButton(text=" Саппорт", callback_data="role_support")]]
    await message.answer("<b>Шаг 4/4:</b> Выберите назначение канала:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@admin_router.callback_query(AdminPartnerStates.wait_for_type, F.data.startswith("role_"))
async def cb_part_finalize(callback: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    await callback.answer(); is_required = (callback.data == "role_support")
    data = await state.get_data(); await state.clear()
    db_session.add(PartnerChannel(channel_id=data["channel_id"], channel_name=data["channel_name"], invite_link=data["invite_link"], is_required=is_required))
    await db_session.commit()
    await cb_adm_partners_list(callback, db_session)


@admin_router.callback_query(F.data.startswith("adm_part_del_"))
async def cb_part_del(callback: CallbackQuery, db_session: AsyncSession):
    ch_id = int(callback.data.split("_")[-1])
    channel = (await db_session.execute(select(PartnerChannel).where(PartnerChannel.id == ch_id))).scalar_one_or_none()
    if channel:
        await db_session.delete(channel); await db_session.commit()
        await callback.answer("Канал успешно удален!")
    await cb_adm_partners_list(callback, db_session)

@admin_router.callback_query(F.data.startswith("adm_srv_del_"))
async def cb_adm_srv_delete_finalize(callback: CallbackQuery, db_session: AsyncSession):
    """Полное каскадное удаление ноды из СУБД бота и очистка связанных портов"""
    server_id = int(callback.data.split("_")[-1])
    
    # 1. Ищем сервер в базе данных
    srv = (await db_session.execute(
        select(Server).where(Server.id == server_id)
    )).scalar_one_or_none()
    
    if not srv:
        await callback.answer("❌ Сервер уже удален или не найден.", show_alert=True)
        return
        
    # 2. Очищаем все тарифные порты xray, привязанные к этому серверу
    inbounds_to_del = (await db_session.execute(
        select(TariffInbound).where(TariffInbound.server_id == server_id)
    )).scalars().all()
    
    for ib in inbounds_to_del:
        await db_session.delete(ib)
        
    # 3. Полностью удаляем сам сервер из базы данных
    await db_session.delete(srv)
    await db_session.commit()
    
    await callback.answer(f"🗑 Нода {srv.name} полностью удалена из системы!", show_alert=True)
    
    # 4. Обновляем интерфейс и возвращаем админа к списку серверов
    await cb_adm_servers_list(callback, db_session)

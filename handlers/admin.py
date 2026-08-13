# handlers/admin.py — ШАГ 3 ИЗ 7
import logging
import datetime
import uuid
from urllib.parse import urlparse
from typing import List, Any, Union
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select, func, String
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import Server, TariffInbound, PartnerChannel, SubscriptionType, User, Subscription, VPNKey
from services.xui import XUIMultiClient
from services.scheduler import deactivate_user_on_servers

logger = logging.getLogger(__name__)
admin_router = Router()

# Фильтр безопасности роутера
admin_router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

# FSM Группы состояний
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

# handlers/admin.py — ШАГ 4 ИЗ 7

async def get_admin_dashboard_text(db_session: AsyncSession) -> str:
    """Живая статистика СУБД для дашборда"""
    total_users = await db_session.scalar(select(func.count(User.telegram_id)))
    now = datetime.datetime.utcnow()
    active_subs = await db_session.scalar(
        select(func.count(Subscription.id))
        .where(Subscription.is_active == True, Subscription.is_pending == False, Subscription.expires_at > now)
    )
    total_servers = await db_session.scalar(select(func.count(Server.id)))
    total_channels = await db_session.scalar(select(func.count(PartnerChannel.id)))

    return (
        f"👑 <b>Панель управления {config.BRAND_NAME}</b>\n\n"
        f"📊 <b>Глобальная статистика СУБД:</b>\n"
        f"├ 👥 Всего пользователей: <code>{total_users or 0}</code>\n"
        f"├ 🟢 Активных подписок: <code>{active_subs or 0}</code>\n"
        f"├ 🖥 Подключено нод 3x-ui: <code>{total_servers or 0}</code>\n"
        f"└ 📢 Каналов-партнеров: <code>{total_channels or 0}</code>\n\n"
        f"💬 <i>Используйте меню ниже для полноценного администрирования:</i>"
    )

def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Управление пользователями (CRM)", callback_data="adm_crm_menu")],
        [InlineKeyboardButton(text="🖥 Управление серверами 3x-ui", callback_data="adm_servers_list")],
        [InlineKeyboardButton(text="📢 Настройка каналов и спонсоров", callback_data="adm_partners_list")]
    ])

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, db_session: AsyncSession):
    await message.answer(text=await get_admin_dashboard_text(db_session), reply_markup=get_admin_main_keyboard())

@admin_router.callback_query(F.data == "adm_main_menu")
async def cb_admin_main_menu(callback: CallbackQuery, db_session: AsyncSession):
    if callback:
        await callback.answer()
        await callback.message.edit_text(text=await get_admin_dashboard_text(db_session), reply_markup=get_admin_main_keyboard())

@admin_router.callback_query(F.data == "adm_crm_menu")
async def cb_adm_crm_menu(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    res = await db_session.execute(select(User).order_by(User.registered_at.desc()).limit(10))
    users = res.scalars().all()
    text = "👥 <b>CRM: Управление пользователями</b>\n\nПоследние клиенты:\n"
    kb = []
    for u in users:
        u_str = f"(@{u.username})" if u.username else ""
        text += f"• <code>{u.telegram_id}</code> {u_str}\n"
        kb.append([InlineKeyboardButton(text=f"👤 Управлять {u.telegram_id}", callback_data=f"adm_user_card_{u.telegram_id}")])
    kb.append([InlineKeyboardButton(text="🔍 Поиск юзера по ID", callback_data="adm_user_search_start"), InlineKeyboardButton(text="◀️ Меню", callback_data="adm_main_menu")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data == "adm_user_search_start")
async def cb_adm_user_search_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminCrmStates.wait_for_search_id)
    await callback.message.edit_text("🔍 Введите точный цифровой <b>Telegram ID</b> пользователя:")

@admin_router.message(AdminCrmStates.wait_for_search_id)
async def msg_adm_user_search_id(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear()
        await cmd_admin(message, db_session)
        return
    try: target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен состоять только из цифр:")
        return
    await state.clear()
    res = await db_session.execute(select(User).where(User.telegram_id == target_id))
    if not res.scalar_one_or_none():
        await message.answer("❌ Пользователь не найден.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В CRM", callback_data="adm_crm_menu")]]))
        return
    await render_user_card(message, db_session, target_id)

async def render_user_card(message_obj: Any, db_session: AsyncSession, target_id: int, is_callback: bool = False):
    stmt = select(User).where(User.telegram_id == target_id).options(selectinload(User.subscriptions).selectinload(Subscription.keys))
    res = await db_session.execute(stmt)
    user = res.scalar_one()
    now = datetime.datetime.utcnow()
    u_str = f"@{user.username}" if user.username else "нет"
    text = f"👤 <b>Карточка пользователя: {user.telegram_id}</b>\n\n• <b>Юзернейм:</b> {u_str}\n• <b>Регистрация:</b> {user.registered_at.strftime('%d.%m.%Y')}\n"
    
    for s in user.subscriptions:
        if s.is_active:
            status = "💤 В очереди" if s.is_pending else "🟢 Активна" if s.expires_at > now else "❌ Истекла"
            text += f"  ├ <code>{s.plan_type.upper()}</code> -> {status} (До: {s.expires_at.strftime('%d.%m.%Y %H:%M')})\n"
            
    kb = [[InlineKeyboardButton(text="➕ Начислить подписку", callback_data=f"adm_crm_add_days_{target_id}")],
          [InlineKeyboardButton(text="🛑 Аннулировать подписки", callback_data=f"adm_crm_wipe_subs_{target_id}")],
          [InlineKeyboardButton(text="◀️ Назад в CRM", callback_data="adm_crm_menu")]]
    if is_callback: await message_obj.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else: await message_obj.answer(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data.startswith("adm_user_card_"))
async def cb_adm_user_card(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    await render_user_card(callback.message, db_session, int(callback.data.split("_")[-1]), is_callback=True)

# handlers/admin.py — ШАГ 5 ИЗ 7

@admin_router.callback_query(F.data.startswith("adm_crm_add_days_"))
async def cb_adm_crm_add_days_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    target_id = int(callback.data.split("_")[-1])
    await state.update_data(target_id=target_id)
    await state.set_state(AdminCrmStates.wait_for_days_count)
    kb = [
        [InlineKeyboardButton(text="⚡ Тариф BASE", callback_data="add_plan_base")],
        [InlineKeyboardButton(text="🔥 Тариф PREMIUM", callback_data="add_plan_premium")]
    ]
    await callback.message.edit_text(text=f"📅 Выберите тариф для начисления пользователю <code>{target_id}</code>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(AdminCrmStates.wait_for_days_count, F.data.startswith("add_plan_"))
async def cb_adm_crm_select_plan(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    plan_type = callback.data.split("_")[-1]
    await state.update_data(plan_type=plan_type)
    data = await state.get_data()
    await callback.message.edit_text(text=f"⏳ Введите количество дней подписки (числом) для тарифа <b>{plan_type.upper()}</b> пользователю <code>{data['target_id']}</code>:")

# handlers/admin.py — ОКОНЧАТЕЛЬНАЯ СИНХРОНИЗАЦИЯ СЕРВЕРОВ ПРИ АПГРЕЙДЕ С ОЧЕРЕДЬЮ ТАРИФОВ

@admin_router.message(AdminCrmStates.wait_for_days_count)
async def msg_adm_crm_save_days(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return

    try: days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Количество дней должно быть целым числом! Попробуйте еще раз:")
        return
        
    data = await state.get_data()
    await state.clear()
    
    target_id = data["target_id"]
    plan_type = data["plan_type"] # Тариф, который мы НАЧИСЛЯЕМ (base или premium)
    now = datetime.datetime.utcnow()
    
    stmt_base = select(Subscription).where(Subscription.user_id == target_id, Subscription.plan_type == SubscriptionType.BASE).order_by(Subscription.expires_at.desc()).limit(1)
    stmt_prem = select(Subscription).where(Subscription.user_id == target_id, Subscription.plan_type == SubscriptionType.PREMIUM).order_by(Subscription.expires_at.desc()).limit(1)
    
    b_res = await db_session.execute(stmt_base)
    p_res = await db_session.execute(stmt_prem)
    
    base_sub = b_res.scalar_one_or_none()
    prem_sub = p_res.scalar_one_or_none()
    
    base_active = base_sub and base_sub.is_active and base_sub.expires_at > now and not base_sub.is_pending
    prem_active = prem_sub and prem_sub.is_active and prem_sub.expires_at > now and not prem_sub.is_pending

    msg_status = ""
    active_subscription_object = None

    # СЦЕНАРИЙ 1: Апгрейд (Начисляем PREMIUM, а у юзера горит активный BASE) -> Замораживаем БАЗУ
    if plan_type == "premium" and base_active:
        # 1. Замораживаем базовые 30 дней и убираем их таймер в очередь
        base_sub.is_pending = True
        
        # 2. Активируем Премиум прямо сейчас на новые 60 дней
        if prem_sub:
            prem_sub.expires_at = now + datetime.timedelta(days=days)
            prem_sub.is_pending = False
            prem_sub.is_active = True
            active_subscription_object = prem_sub
        else:
            prem_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.PREMIUM, expires_at=now + datetime.timedelta(days=days), is_pending=False)
            db_session.add(prem_sub)
            active_subscription_object = prem_sub
        msg_status = "🔥 PREMIUM активирован на 60 дней! Базовый тариф на 30 дней успешно заморожен и убран в очередь."

    # СЦЕНАРИЙ 2: Даунгрейд (Начисляем BASE, а у юзера горит активный PREMIUM) -> Отправляем БАЗУ в очередь
    elif plan_type == "base" and prem_active:
        if base_sub:
            if base_sub.is_pending: base_sub.expires_at += datetime.timedelta(days=days)
            else: base_sub.expires_at = now + datetime.timedelta(days=days)
            base_sub.is_pending = True
            base_sub.is_active = True
        else:
            base_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.BASE, expires_at=now + datetime.timedelta(days=days), is_pending=True)
            db_session.add(base_sub)
        active_subscription_object = prem_sub # Главным остается текущий премиум
        msg_status = "💤 PREMIUM активен. Новый базовый тариф добавлен в очередь отложенного старта."

    # СЦЕНАРИЙ 3: Стандартное начисление / Продление
    else:
        target_sub = prem_sub if plan_type == "premium" else base_sub
        if target_sub and target_sub.expires_at > now and not target_sub.is_pending:
            target_sub.expires_at += datetime.timedelta(days=days)
            active_subscription_object = target_sub
        else:
            if plan_type == "premium":
                prem_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.PREMIUM, expires_at=now + datetime.timedelta(days=days), is_pending=False)
                db_session.add(prem_sub)
                active_subscription_object = prem_sub
            else:
                base_sub = Subscription(user_id=target_id, plan_type=SubscriptionType.BASE, expires_at=now + datetime.timedelta(days=days), is_pending=False)
                db_session.add(base_sub)
                active_subscription_object = base_sub
        msg_status = f"✅ Успешное прямое продление тарифа {plan_type.upper()}."


    await db_session.flush()
    nodes_synced = 0
    
    # Определяем, какая подписка главная и горит прямо сейчас
    active_subscription_object = prem_sub if (prem_sub and not prem_sub.is_pending and prem_sub.expires_at > now) else base_sub
    now_active_plan = active_subscription_object.plan_type if active_subscription_object else None

    if now_active_plan:
        servers_res = await db_session.execute(select(Server).where(Server.is_active == True))
        servers = servers_res.scalars().all()
        
        email = f"usr_{target_id}_{uuid.uuid4().hex[:4]}"
        sub_id = uuid.uuid4().hex
        
        keys_stmt = select(VPNKey).join(Subscription).where(Subscription.user_id == target_id)
        keys_res = await db_session.execute(keys_stmt)
        existing_keys = {k.server_id: k for k in keys_res.scalars().all()}
        
        # Вычисляем дедлайн для панели 3x-ui (60 дней премиума)
        active_end_date = active_subscription_object.expires_at
        expiry_timestamp = int(active_end_date.timestamp() * 1000)

        for srv in servers:
            # Четко собираем инбаунды под текущий активный тариф сети
            if now_active_plan == SubscriptionType.PREMIUM:
                ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id, TariffInbound.plan_type.in_([SubscriptionType.BASE, SubscriptionType.PREMIUM]))
            else:
                ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id, TariffInbound.plan_type == SubscriptionType.BASE)
                
            ib_res = await db_session.execute(ib_stmt)
            inbound_ids = [ib.inbound_id for ib in ib_res.scalars().all()]
            
            if not inbound_ids: continue
            xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
            
            if srv.id not in existing_keys:
                # Нарезка с нуля на новой ноде (Reserve-1)
                success = await xui.add_client(email=email, sub_id=sub_id, inbound_ids=inbound_ids, expires_days=days)
                if success:
                    protocol = "https" if srv.api_url.startswith("https") else "http"
                    parsed_url = urlparse(srv.api_url)
                    clean_domain = parsed_url.hostname
                    subscribe_url = f"{protocol}://{clean_domain}:{srv.sub_port}/{srv.sub_path}/{sub_id}"
                    
                    # Пишем ключ строго к ID подписки PREMIUM
                    key_record = VPNKey(subscription_id=prem_sub.id, server_id=srv.id, client_email=email, sub_id=sub_id, config_data=subscribe_url)
                    db_session.add(key_record)
                    nodes_synced += 1
            else:
                # ОСТАВЛЯЕМ КЛЮЧ В БД КАК ЕСТЬ (БЕЗ ПЕРЕПРИВЯЗОК ID)
                current_key = existing_keys[srv.id]       
                expiry_timestamp = int(active_end_date.timestamp() * 1000)
                await xui.update_client_inbounds(email=current_key.client_email, sub_id=current_key.sub_id, inbound_ids=inbound_ids, expiry_time=expiry_timestamp)
                nodes_synced += 1

    await db_session.commit()
    await message.answer(text=f"⚡ <b>CRM Синхронизация завершена!</b>\n\n• Начислено: <code>{plan_type.upper()}</code> на <b>{days} дн.</b>\n• Статус: <i>{msg_status}</i>\n• Синхронизировано нод: <b>{nodes_synced} шт.</b>")
    await render_user_card(message, db_session, target_id)


@admin_router.callback_query(F.data.startswith("adm_crm_wipe_subs_"))
async def cb_adm_crm_wipe_subs(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    target_id = int(callback.data.split("_")[-1])
    await deactivate_user_on_servers(db_session, target_id)
    stmt = select(Subscription).where(Subscription.user_id == target_id)
    res = await db_session.execute(stmt)
    for s in res.scalars().all(): s.is_active = False
    await db_session.commit()
    await callback.answer("🗑 Все доступы аннулированы!", show_alert=True)
    await render_user_card(callback.message, db_session, target_id, is_callback=True)

# handlers/admin.py — ШАГ 6 ИЗ 5

@admin_router.callback_query(F.data == "adm_servers_list")
async def cb_adm_servers_list(callback: CallbackQuery, db_session: AsyncSession):
    if callback: await callback.answer()
    res = await db_session.execute(select(Server))
    servers = res.scalars().all()
    text = "🖥 <b>Список подключенных серверов 3x-ui:</b>\n\n"
    kb = []
    if not servers: text += "<i>Ноды еще не добавлены.</i>\n\n"
    for s in servers:
        status = "✅" if s.is_active else "❌"
        text += f"{status} <b>{s.name}</b> (Порт: {s.sub_port}, Путь: /{s.sub_path})\n<code>{s.api_url}</code>\n\n"
        kb.append([InlineKeyboardButton(text=f"⚙️ Настроить {s.name}", callback_data=f"adm_srv_manage_{s.id}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="adm_srv_add_start")])
    kb.append([InlineKeyboardButton(text="◀️ Панель управления", callback_data="adm_main_menu")])
    if callback: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)

# --- Сценарий добавления сервера ---
@admin_router.callback_query(F.data == "adm_srv_add_start")
async def cb_adm_srv_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminServerStates.wait_for_name)
    await callback.message.edit_text("📝 <b>Шаг 1/5:</b> Введите название ноды:")

@admin_router.message(AdminServerStates.wait_for_name)
async def msg_srv_name(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_url)
    await message.answer("🔗 <b>Шаг 2/5:</b> Введите URL API панели:")

@admin_router.message(AdminServerStates.wait_for_url)
async def msg_srv_url(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(api_url=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_token)
    await message.answer("🔑 <b>Шаг 3/5:</b> Введите API Token (Bearer) панели:")

@admin_router.message(AdminServerStates.wait_for_token)
async def msg_srv_token(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(api_token=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_sub_port)
    await message.answer("🔌 <b>Шаг 4/5:</b> Введите порт подписки для этого сервера (дефолт 2096):")

@admin_router.message(AdminServerStates.wait_for_sub_port)
async def msg_srv_sub_port(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    try: sub_port = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Порт должен быть числом!:")
        return
    await state.update_data(sub_port=sub_port)
    await state.set_state(AdminServerStates.wait_for_sub_path)
    await message.answer("🎭 <b>Шаг 5/5:</b> Введите путь подписки маскировки (например stats):")

@admin_router.message(AdminServerStates.wait_for_sub_path)
async def msg_srv_sub_path(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    sub_path = message.text.strip().strip("/")
    data = await state.get_data()
    await state.clear()
    new_server = Server(name=data["name"], api_url=data["api_url"], api_token=data["api_token"], sub_port=data["sub_port"], sub_path=sub_path)
    db_session.add(new_server)
    await db_session.commit()
    t, km = await cb_adm_servers_list(None, db_session)
    await message.answer(text=f"✅ <b>Сервер '{data['name']}' успешно сохранен!</b>\n\n{t}", reply_markup=km)

# --- Сценарий безопасного редактирования через UPDATE ---
@admin_router.callback_query(F.data.startswith("adm_srv_edit_"))
async def cb_adm_srv_edit_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(edit_server_id=int(callback.data.split("_")[-1]))
    await state.set_state(AdminServerEditStates.wait_for_name)
    await callback.message.edit_text("✏️ <b>Редактирование. Шаг 1/5:</b> Введите НОВОЕ название ноды:")

@admin_router.message(AdminServerEditStates.wait_for_name)
async def msg_srv_edit_name(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminServerEditStates.wait_for_url)
    await message.answer("🔗 <b>Шаг 2/5:</b> Введите НОВЫЙ URL API панели:")

@admin_router.message(AdminServerEditStates.wait_for_url)
async def msg_srv_edit_url(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(api_url=message.text.strip())
    await state.set_state(AdminServerEditStates.wait_for_token)
    await message.answer("🔑 <b>Шаг 3/5:</b> Введите НОВЫЙ API Token панели:")

@admin_router.message(AdminServerEditStates.wait_for_token)
async def msg_srv_edit_token(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(api_token=message.text.strip())
    await state.set_state(AdminServerEditStates.wait_for_sub_port)
    await message.answer("🔌 <b>Шаг 4/5:</b> Введите НОВЫЙ порт подписки:")

@admin_router.message(AdminServerEditStates.wait_for_sub_port)
async def msg_srv_edit_sub_port(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    try: sub_port = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Порт должен быть числом!:")
        return
    await state.update_data(sub_port=sub_port)
    await state.set_state(AdminServerEditStates.wait_for_sub_path)
    await message.answer("🎯 <b>Шаг 5/5:</b> Введите НОВЫЙ путь маскировки подписки:")

@admin_router.message(AdminServerEditStates.wait_for_sub_path)
async def msg_srv_edit_finalize(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    sub_path = message.text.strip().strip("/")
    data = await state.get_data()
    await state.clear()
    
    stmt = select(Server).where(Server.id == data["edit_server_id"])
    res = await db_session.execute(stmt)
    srv = res.scalar_one()
    
    srv.name = data["name"]
    srv.api_url = data["api_url"]
    srv.api_token = data["api_token"]
    srv.sub_port = data["sub_port"]
    srv.sub_path = sub_path
    await db_session.commit()
    
    t, km = await cb_adm_servers_list(None, db_session)
    await message.answer(text=f"✅ <b>Параметры ноды успешно обновлены!</b>\n\n{t}", reply_markup=km)

@admin_router.callback_query(F.data.startswith("adm_srv_del_"))
async def cb_adm_srv_del(callback: CallbackQuery, db_session: AsyncSession):
    server_id = int(callback.data.split("_")[-1])
    srv = (await db_session.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if srv:
        await db_session.delete(srv)
        await db_session.commit()
        await callback.answer(f"🗑 Сервер '{srv.name}' полностью удален!", show_alert=True)
    await cb_adm_servers_list(callback, db_session)

# handlers/admin.py — ШАГ 7 ИЗ 7

@admin_router.callback_query(F.data.startswith("adm_srv_manage_"))
async def cb_adm_srv_manage(callback: CallbackQuery, db_session: AsyncSession):
    """Экран управления конкретной нодой, её тарифами и системными параметрами"""
    await callback.answer()
    server_id = int(callback.data.split("_")[-1])
    
    stmt = select(Server).where(Server.id == server_id)
    res = await db_session.execute(stmt)
    srv = res.scalar_one_or_none()
    
    if not srv:
        await callback.message.edit_text("❌ Сервер базы данных не найден.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К списку серверов", callback_data="adm_servers_list")]]))
        return

    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    all_inbounds = await xui.get_inbounds()
    
    if not all_inbounds:
        text = f"❌ <b>Нода {srv.name} недоступна по API!</b>\n\nПроверьте настройки сети или измените параметры:"
        kb = [
            [InlineKeyboardButton(text="✏️ Редактировать параметры", callback_data=f"adm_srv_edit_{srv.id}")],
            [InlineKeyboardButton(text="🗑 Удалить сервер из СУБД", callback_data=f"adm_srv_del_{srv.id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_servers_list")]
        ]
        await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        return

    ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id)
    ib_res = await db_session.execute(ib_stmt)
    db_inbounds = {i.inbound_id: i.plan_type for i in ib_res.scalars().all()}

    text = f"🖥 <b>Управление нодой: {srv.name}</b>\n└ URL: <code>{srv.api_url}</code>\n\n⚙️ <b>Настройка тарифов портов Xray:</b>"
    kb = [[InlineKeyboardButton(text="✏️ Редактировать ноду", callback_data=f"adm_srv_edit_{srv.id}"), InlineKeyboardButton(text="🗑 Удалить ноду", callback_data=f"adm_srv_del_{srv.id}")]]

    for ib in all_inbounds:
        ib_id = int(ib.get("id"))
        current_plan = db_inbounds.get(ib_id, "❌ ОТКЛЮЧЕН")
        badge = "🟢 BASE" if current_plan == "base" else "💎 PREMIUM" if current_plan == "premium" else "⚫ НЕАКТИВЕН"
        btn_text = f"[{ib_id}] {ib.get('protocol', '').upper()} ({ib.get('remark', '')}) -> {badge}"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"adm_toggle_ib_{srv.id}_{ib_id}")])

    kb.append([InlineKeyboardButton(text="◀️ Назад к серверам", callback_data="adm_servers_list")])
    try: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception: pass

@admin_router.callback_query(F.data.startswith("adm_toggle_ib_"))
async def cb_adm_toggle_ib(callback: CallbackQuery, db_session: AsyncSession):
    """Чистое переключение тарифов инбаундов с изолированной сессией обновления"""
    raw_data = callback.data.replace("adm_toggle_ib_", "")
    parts = raw_data.split("_", 1)
    
    # ИСПРАВЛЕНО: Четко указываем индексы элементов списка split
    server_id = int(parts[0])
    ib_id = int(parts[1].strip())

    stmt = select(Server).where(Server.id == server_id)
    res = await db_session.execute(stmt)
    srv = res.scalar_one_or_none()

    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    all_inbounds = await xui.get_inbounds()
    target_ib = next((i for i in all_inbounds if i.get("id") == ib_id), None)

    ib_stmt = select(TariffInbound).where(TariffInbound.server_id == server_id, TariffInbound.inbound_id == ib_id)
    ib_res = await db_session.execute(ib_stmt)
    ib_record = ib_res.scalar_one_or_none()

    if not ib_record:
        new_record = TariffInbound(server_id=server_id, plan_type=SubscriptionType.BASE, inbound_id=ib_id, protocol_name=target_ib.get("protocol", "unknown"), port=target_ib.get("port", 0), remark=target_ib.get("remark", ""))
        db_session.add(new_record)
        await callback.answer("🟢 В тариф BASE")
    elif ib_record.plan_type == SubscriptionType.BASE:
        ib_record.plan_type = SubscriptionType.PREMIUM
        await callback.answer("💎 В тариф PREMIUM")
    else:
        await db_session.delete(ib_record)
        await callback.answer("⚫ Деактивирован")

    await db_session.commit()
    
    ib_stmt_new = select(TariffInbound).where(TariffInbound.server_id == srv.id)
    ib_res_new = await db_session.execute(ib_stmt_new)
    db_inbounds = {i.inbound_id: i.plan_type for i in ib_res_new.scalars().all()}

    text = f"🖥 <b>Управление нодой: {srv.name}</b>\n└ URL: <code>{srv.api_url}</code>\n\n⚙️ <b>Настройка тарифов портов Xray:</b>"
    kb = [[InlineKeyboardButton(text="✏️ Редактировать ноду", callback_data=f"adm_srv_edit_{srv.id}"), InlineKeyboardButton(text="🗑 Удалить ноду", callback_data=f"adm_srv_del_{srv.id}")]]

    for ib in all_inbounds:
        current_ib_id = int(ib.get("id"))
        current_plan = db_inbounds.get(current_ib_id, "❌ ОТКЛЮЧЕН")
        badge = "🟢 BASE" if current_plan == "base" else "💎 PREMIUM" if current_plan == "premium" else "⚫ НЕАКТИВЕН"
        btn_text = f"[{current_ib_id}] {ib.get('protocol', '').upper()} ({ib.get('remark', '')}) -> {badge}"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"adm_toggle_ib_{srv.id}_{current_ib_id}")])

    kb.append([InlineKeyboardButton(text="◀️ Назад к серверам", callback_data="adm_servers_list")])
    try: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except Exception: pass

@admin_router.callback_query(F.data == "adm_partners_list")
async def cb_adm_partners_list(callback: CallbackQuery, db_session: AsyncSession):
    if callback: await callback.answer()
    res = await db_session.execute(select(PartnerChannel))
    channels = res.scalars().all()
    text = "📢 <b>Управление каналами и подписками:</b>\n\n"
    kb = []
    if not channels: text += "<i>Список каналов пока пуст.</i>"
    else:
        for ch in channels:
            role = "🛠 Саппорт" if ch.is_required else "🎁 Спонсор"
            text += f"• <b>{ch.channel_name}</b> [{role}]\nID: <code>{ch.channel_id}</code>\n\n"
            kb.append([InlineKeyboardButton(text=f"❌ Удалить {ch.channel_name}", callback_data=f"adm_part_del_{ch.id}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="adm_part_add_start"), InlineKeyboardButton(text="◀️ Меню", callback_data="adm_main_menu")])
    if callback: await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)

@admin_router.callback_query(F.data == "adm_part_add_start")
async def cb_adm_part_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminPartnerStates.wait_for_id)
    await callback.message.edit_text("🆔 <b>Шаг 1/4:</b> Введите цифровой <b>Telegram ID канала</b>:")

@admin_router.message(AdminPartnerStates.wait_for_id)
async def msg_part_id(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    try: ch_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом! Попробуйте еще раз:")
        return
    await state.update_data(channel_id=ch_id)
    await state.set_state(AdminPartnerStates.wait_for_name)
    await message.answer("📝 <b>Шаг 2/4:</b> Введите название канала:")

@admin_router.message(AdminPartnerStates.wait_for_name)
async def msg_part_name(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(channel_name=message.text.strip())
    await state.set_state(AdminPartnerStates.wait_for_link)
    await message.answer("🔗 <b>Шаг 3/4:</b> Введите ссылку-приглашение:")

@admin_router.message(AdminPartnerStates.wait_for_link)
async def msg_part_link(message: Message, state: FSMContext, db_session: AsyncSession):
    if message.text.startswith("/"):
        await state.clear(); await cmd_admin(message, db_session); return
    await state.update_data(invite_link=message.text.strip())
    await state.set_state(AdminPartnerStates.wait_for_type)
    kb = [[InlineKeyboardButton(text="🎁 Спонсор", callback_data="role_sponsor"), InlineKeyboardButton(text="🛠 Саппорт", callback_data="role_support")]]
    await message.answer("🎯 <b>Шаг 4/4:</b> Выберите назначение канала:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(AdminPartnerStates.wait_for_type, F.data.startswith("role_"))
async def cb_part_finalize(callback: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    await callback.answer()
    is_required = (callback.data == "role_support")
    data = await state.get_data()
    await state.clear()
    new_channel = PartnerChannel(channel_id=data["channel_id"], channel_name=data["channel_name"], invite_link=data["invite_link"], is_required=is_required)
    db_session.add(new_channel)
    await db_session.commit()
    t, km = await cb_adm_partners_list(None, db_session)
    await callback.message.edit_text(text=f"✅ <b>Канал успешно сохранен!</b>\n\n{t}", reply_markup=km)

@admin_router.callback_query(F.data.startswith("adm_part_del_"))
async def cb_part_del(callback: CallbackQuery, db_session: AsyncSession):
    ch_id = int(callback.data.split("_")[-1])
    stmt = select(PartnerChannel).where(PartnerChannel.id == ch_id)
    res = await db_session.execute(stmt)
    channel = res.scalar_one_or_none()
    if channel:
        await db_session.delete(channel)
        await db_session.commit()
        await callback.answer("🗑 Канал успешно удален!")
    await cb_adm_partners_list(callback, db_session)


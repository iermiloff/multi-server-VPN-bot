# handlers/admin.py — ШАГ 1 ИЗ 4
import logging
import datetime
import uuid
from typing import List, Any
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import (
    Server, TariffInbound, PartnerChannel, SubscriptionType, 
    User, Subscription, VPNKey
)
from services.xui import XUIMultiClient
from services.scheduler import deactivate_user_on_servers

logger = logging.getLogger(__name__)
admin_router = Router()

# Жесткий фильтр безопасности роутера
admin_router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

# FSM Состояния
class AdminServerStates(StatesGroup):
    wait_for_name = State()
    wait_for_url = State()
    wait_for_token = State()
    wait_for_sub_port = State()

class AdminPartnerStates(StatesGroup):
    wait_for_id = State()
    wait_for_name = State()
    wait_for_link = State()
    wait_for_type = State()

class AdminCrmStates(StatesGroup):
    wait_for_search_id = State()
    wait_for_days_count = State()

async def get_admin_dashboard_text(db_session: AsyncSession) -> str:
    """Собирает полную живую статистику СУБД для вывода в дашборд"""
    total_users = await db_session.scalar(select(func.count(User.telegram_id)))
    
    now = datetime.datetime.utcnow()
    active_subs = await db_session.scalar(
        select(func.count(Subscription.id))
        .where(Subscription.is_active == True, Subscription.expires_at > now)
    )
    
    total_servers = await db_session.scalar(select(func.count(Server.id)))
    total_channels = await db_session.scalar(select(func.count(PartnerChannel.id)))

    text = (
        f"👑 <b>Панель управления {config.BRAND_NAME}</b>\n\n"
        f"📊 <b>Глобальная статистика СУБД:</b>\n"
        f"├ 👥 Всего пользователей: <code>{total_users or 0}</code>\n"
        f"├ 🟢 Активных подписок: <code>{active_subs or 0}</code>\n"
        f"├ 🖥 Подключено нод 3x-ui: <code>{total_servers or 0}</code>\n"
        f"└ 📢 Каналов-партнеров: <code>{total_channels or 0}</code>\n\n"
        f"💬 <i>Используйте меню ниже для полноценного администрирования вашей SaaS-инфраструктуры:</i>"
    )
    return text

def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура монолитной админки"""
    keyboard = [
        [InlineKeyboardButton(text="👥 Управление пользователями (CRM)", callback_data="adm_crm_menu")],
        [InlineKeyboardButton(text="🖥 Управление серверами 3x-ui", callback_data="adm_servers_list")],
        [InlineKeyboardButton(text="📢 Настройка каналов и спонсоров", callback_data="adm_partners_list")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# handlers/admin.py — ШАГ 2 ИЗ 4

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, db_session: AsyncSession):
    """Точка входа: вызов админки через команду"""
    text = await get_admin_dashboard_text(db_session)
    await message.answer(text=text, reply_markup=get_admin_main_keyboard())

@admin_router.callback_query(F.data == "adm_main_menu")
async def cb_admin_main_menu(callback: CallbackQuery, db_session: AsyncSession):
    """Сквозной возврат на главный дашборд админки"""
    await callback.answer()
    text = await get_admin_dashboard_text(db_session)
    await callback.message.edit_text(text=text, reply_markup=get_admin_main_keyboard())

@admin_router.callback_query(F.data == "adm_crm_menu")
async def cb_adm_crm_menu(callback: CallbackQuery, db_session: AsyncSession):
    """Главный экран CRM-модуля управления пользователями"""
    await callback.answer()
    
    # Берем последних 10 зарегистрированных пользователей для быстрого вывода
    stmt = select(User).order_by(User.registered_at.desc()).limit(10)
    res = await db_session.execute(stmt)
    users = res.scalars().all()
    
    text = "👥 <b>CRM: Управление пользователями</b>\n\nПоследние зарегистрированные клиенты:\n"
    kb = []
    
    for u in users:
        username_str = f"(@{u.username})" if u.username else ""
        text += f"• <code>{u.telegram_id}</code> {username_str}\n"
        kb.append([InlineKeyboardButton(text=f"👤 Управлять {u.telegram_id}", callback_data=f"adm_user_card_{u.telegram_id}")])
        
    text += "\n🔍 Вы можете найти любого пользователя в базе по его цифровому Telegram ID."
    
    kb.append([InlineKeyboardButton(text="🔍 Поиск юзера по ID", callback_data="adm_user_search_start")])
    kb.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="adm_main_menu")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data == "adm_user_search_start")
async def cb_adm_user_search_start(callback: CallbackQuery, state: FSMContext):
    """Запуск FSM поиска пользователя"""
    await callback.answer()
    await state.set_state(AdminCrmStates.wait_for_search_id)
    await callback.message.edit_text("🔍 Введите точный цифровой <b>Telegram ID</b> пользователя для поиска:")

@admin_router.message(AdminCrmStates.wait_for_search_id)
async def msg_adm_user_search_id(message: Message, state: FSMContext, db_session: AsyncSession):
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен состоять только из цифр! Попробуйте еще раз:")
        return
        
    await state.clear()
    stmt = select(User).where(User.telegram_id == target_id)
    res = await db_session.execute(stmt)
    user = res.scalar_one_or_none()
    
    if not user:
        await message.answer(
            text=f"❌ Пользователь с ID <code>{target_id}</code> не найден в базе данных.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ В CRM меню", callback_data="adm_crm_menu")
            ]])
        )
        return
        
    # Если нашли — перенаправляем на рендеринг карточки
    await render_user_card(message, db_session, target_id)

async def render_user_card(message_obj: Any, db_session: AsyncSession, target_id: int, is_callback: bool = False):
    """Вспомогательная функция отрисовки детальной карточки управления клиентом"""
    stmt = (
        select(User)
        .where(User.telegram_id == target_id)
        .options(selectinload(User.subscriptions).selectinload(Subscription.keys))
    )
    res = await db_session.execute(stmt)
    user = res.scalar_one()
    
    now = datetime.datetime.utcnow()
    active_subs = [s for s in user.subscriptions if s.is_active and s.expires_at > now]
    
    username_str = f"@{user.username}" if user.username else "нет"
    text = (
        f"👤 <b>Карточка пользователя: {user.telegram_id}</b>\n\n"
        f"• <b>Юзернейм:</b> {username_str}\n"
        f"• <b>Дата регистрации:</b> {user.registered_at.strftime('%d.%m.%Y %H:%M')}\n"
    )
    
    if not active_subs:
        text += "• <b>Статус подписки:</b> ❌ Не активна\n"
    else:
        text += "• <b>Активные доступы:</b>\n"
        for s in active_subs:
            exp = s.expires_at.strftime('%d.%m.%Y %H:%M')
            text += f"  ├ <code>{s.plan_type.upper()}</code> (До: {exp})\n"
            
    kb = [
        [InlineKeyboardButton(text="➕ Начислить / Продлить подписку", callback_data=f"adm_crm_add_days_{target_id}")],
        [InlineKeyboardButton(text="🛑 Аннулировать все подписки", callback_data=f"adm_crm_wipe_subs_{target_id}")],
        [InlineKeyboardButton(text="◀️ Назад в CRM", callback_data="adm_crm_menu")]
    ]
    
    if is_callback:
        await message_obj.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await message_obj.answer(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data.startswith("adm_user_card_"))
async def cb_adm_user_card(callback: CallbackQuery, db_session: AsyncSession):
    """Вызов карточки пользователя через inline-кнопку"""
    await callback.answer()
    target_id = int(callback.data.split("_")[-1])
    await render_user_card(callback.message, db_session, target_id, is_callback=True)

# handlers/admin.py — ШАГ 3 ИЗ 4

@admin_router.callback_query(F.data.startswith("adm_crm_add_days_"))
async def cb_adm_crm_add_days_start(callback: CallbackQuery, state: FSMContext):
    """Старт начисления дней: запрашиваем количество дней"""
    await callback.answer()
    target_id = int(callback.data.split("_")[-1])
    await state.update_data(target_id=target_id)
    await state.set_state(AdminCrmStates.wait_for_days_count)
    
    kb = [
        [InlineKeyboardButton(text="⚡ Тариф BASE", callback_data="add_plan_base")],
        [InlineKeyboardButton(text="🔥 Тариф PREMIUM", callback_data="add_plan_premium")]
    ]
    await callback.message.edit_text(
        text=f"📅 Выберите тарифный план для начисления пользователю <code>{target_id}</code>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@admin_router.callback_query(AdminCrmStates.wait_for_days_count, F.data.startswith("add_plan_"))
async def cb_adm_crm_select_plan(callback: CallbackQuery, state: FSMContext):
    """Выбор тарифа и запрос количества дней"""
    await callback.answer()
    plan_type = callback.data.split("_")[-1]
    await state.update_data(plan_type=plan_type)
    
    data = await state.get_data()
    await callback.message.edit_text(
        text=f"⏳ Введите количество дней подписки (числом) для начисления тарифа <b>{plan_type.upper()}</b> пользователю <code>{data['target_id']}</code>:"
    )

@admin_router.message(AdminCrmStates.wait_for_days_count)
async def msg_adm_crm_save_days(message: Message, state: FSMContext, db_session: AsyncSession):
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Количество дней должно быть целым числом! Попробуйте еще раз:")
        return
        
    data = await state.get_data()
    await state.clear()
    
    target_id = data["target_id"]
    plan_type = data["plan_type"]
    now = datetime.datetime.utcnow()
    
    # Ищем, есть ли уже активная подписка этого типа, чтобы продлить её
    stmt = (
        select(Subscription)
        .where(Subscription.user_id == target_id, Subscription.plan_type == plan_type)
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    res = await db_session.execute(stmt)
    existing_sub = res.scalar_one_or_none()
    
    if existing_sub and existing_sub.expires_at > now:
        existing_sub.expires_at += datetime.timedelta(days=days)
        end_date = existing_sub.expires_at
    else:
        new_sub = Subscription(
            user_id=target_id,
            plan_type=plan_type,
            expires_at=now + datetime.timedelta(days=days)
        )
        db_session.add(new_sub)
        end_date = new_sub.expires_at
        
    await db_session.commit()
    await message.answer(f"✅ Успешно начислено <b>{days} дн.</b> тарифа {plan_type.upper()}! Новая дата окончания: <code>{end_date.strftime('%d.%m.%Y %H:%M')}</code>")
    await render_user_card(message, db_session, target_id)

@admin_router.callback_query(F.data.startswith("adm_crm_wipe_subs_"))
async def cb_adm_crm_wipe_subs(callback: CallbackQuery, db_session: AsyncSession):
    """Полное аннулирование всех подписок и удаление ключей со всех нод 3x-ui"""
    await callback.answer()
    target_id = int(callback.data.split("_")[-1])
    
    # 1. Вызываем функцию каскадного удаления со всех серверов
    await deactivate_user_on_servers(db_session, target_id)
    
    # 2. Переводим все подписки пользователя в статус неактивных
    stmt = select(Subscription).where(Subscription.user_id == target_id)
    res = await db_session.execute(stmt)
    subs = res.scalars().all()
    
    for s in subs:
        s.is_active = False
        
    await db_session.commit()
    await callback.answer("🗑 Все доступы аннулированы, клиент очищен с серверов!", show_alert=True)
    await render_user_card(callback.message, db_session, target_id, is_callback=True)

# handlers/admin.py — ШАГ 4.1

@admin_router.callback_query(F.data == "adm_servers_list")
async def cb_adm_servers_list(callback: CallbackQuery, db_session: AsyncSession):
    """Вывод списка всех зарегистрированных нод 3x-ui"""
    await callback.answer()
    res = await db_session.execute(select(Server))
    servers = res.scalars().all()
    
    text = "🖥 <b>Список подключенных серверов 3x-ui:</b>\n\n"
    kb = []
    
    if not servers:
        text += "<i>Ноды еще не добавлены. Нажмите кнопку ниже для настройки.</i>\n\n"
    for s in servers:
        status = "✅" if s.is_active else "❌"
        text += f"{status} <b>{s.name}</b> (Порт подписки: {s.sub_port})\n<code>{s.api_url}</code>\n\n"
        kb.append([InlineKeyboardButton(text=f"⚙️ Настроить {s.name}", callback_data=f"adm_srv_manage_{s.id}")])
        
    kb.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="adm_srv_add_start")])
    kb.append([InlineKeyboardButton(text="◀️ Главное меню админа", callback_data="adm_main_menu")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data == "adm_srv_add_start")
async def cb_adm_srv_add_start(callback: CallbackQuery, state: FSMContext):
    """Старт FSM сценария добавления сервера"""
    await callback.answer()
    await state.set_state(AdminServerStates.wait_for_name)
    await callback.message.edit_text("📝 <b>Шаг 1/4:</b> Введите название ноды (например: <code>Германия #1</code>):")

@admin_router.message(AdminServerStates.wait_for_name)
async def msg_srv_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_url)
    await message.answer("🔗 <b>Шаг 2/4:</b> Введите URL API панели (например: <code>https://123.45.67.89:2053</code>):")

@admin_router.message(AdminServerStates.wait_for_url)
async def msg_srv_url(message: Message, state: FSMContext):
    await state.update_data(api_url=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_token)
    await message.answer("🔑 <b>Шаг 3/4:</b> Введите <b>API Token (Bearer)</b> панели.\n\n<i>Найти его можно в панели: Настройки -> Безопасность -> API Token.</i>")

@admin_router.message(AdminServerStates.wait_for_token)
async def msg_srv_token(message: Message, state: FSMContext):
    await state.update_data(api_token=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_sub_port)
    
    hint_text = (
        "🔌 <b>Шаг 4/4: Введите порт подписки для этого сервера</b>\n\n"
        "ℹ <i>По умолчанию используется порт <b>2096</b>. Вы можете найти его в панели, "
        "перейдя в <b>'Настройки панели' -> вкладка 'Подписка'</b>.</i>"
    )
    await message.answer(text=hint_text)

@admin_router.message(AdminServerStates.wait_for_sub_port)
async def msg_srv_sub_port(message: Message, state: FSMContext, db_session: AsyncSession):
    try:
        sub_port = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Порт должен быть числом! Попробуйте еще раз:")
        return
        
    data = await state.get_data()
    await state.clear()
    
    new_server = Server(
        name=data["name"],
        api_url=data["api_url"],
        api_token=data["api_token"],
        sub_port=sub_port
    )
    db_session.add(new_server)
    await db_session.commit()
    await message.answer(f"✅ <b>Сервер '{data['name']}' успешно сохранен!</b>\n\nЗайдите в управление нодами, чтобы распределить входящие порты по тарифам.")

# handlers/admin.py — ШАГ 4.2

@admin_router.callback_query(F.data.startswith("adm_srv_manage_"))
async def cb_adm_srv_manage(callback: CallbackQuery, db_session: AsyncSession):
    """Экран управления конкретной нодой и её портами с поддержкой двух тарифов"""
    await callback.answer()
    server_id = int(callback.data.split("_")[-1])
    
    stmt = select(Server).where(Server.id == server_id)
    res = await db_session.execute(stmt)
    srv = res.scalar_one_or_none()
    
    if not srv:
        await callback.message.edit_text("❌ Сервер не найден.")
        return

    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    all_inbounds = await xui.get_inbounds()
    
    if not all_inbounds:
        await callback.message.edit_text(
            text=f"❌ Не удалось получить инбаунды с ноды <b>{srv.name}</b>.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="adm_servers_list")
            ]])
        )
        return

    ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id)
    ib_res = await db_session.execute(ib_stmt)
    db_inbounds = {i.inbound_id: i.plan_type for i in ib_res.scalars().all()}

    text = (
        f"⚙️ <b>Настройка тарифов для ноды: {srv.name}</b>\n\n"
        f"Кликните по инбаунду, чтобы изменить его тарифный план. "
        f"Пользователи тарифа PREMIUM автоматически получают доступ и к портам тарифа BASE:\n\n"
    )
    kb = []

    for ib in all_inbounds:
        ib_id = ib.get("id")
        current_plan = db_inbounds.get(ib_id, "❌ ОТКЛЮЧЕН")
        
        if current_plan == "base":
            badge = "🟢 BASE"
        elif current_plan == "premium":
            badge = "💎 PREMIUM"
        else:
            badge = "⚫ НЕАКТИВЕН"
        
        btn_text = f"[{ib_id}] {ib.get('protocol', '').upper()} ({ib.get('remark', '')}) -> {badge}"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"adm_toggle_ib_{srv.id}_{ib_id}")])

    kb.append([InlineKeyboardButton(text="◀️ Назад к серверам", callback_data="adm_servers_list")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# handlers/admin.py — ИСПРАВЛЕННЫЙ ХЕНДЛЕР С УЧЕТОМ ДЛИННЫХ UUID СЭНДВИЧЕЙ

@admin_router.callback_query(F.data.startswith("adm_toggle_ib_"))
async def cb_adm_toggle_ib(callback: CallbackQuery, db_session: AsyncSession):
    """Циклическое переключение тарифа инбаунда: НЕАКТИВЕН -> BASE -> PREMIUM -> НЕАКТИВЕН"""
    # Удаляем префикс, чтобы осталась только дата: "IDСЕРВЕРА_IDИНБАУНДА"
    raw_data = callback.data.replace("adm_toggle_ib_", "")
    
    # Разбиваем строго по первому знаку подчеркивания
    # maxsplit=1 гарантирует, что даже если в UUID инбаунда куча подчеркиваний, они не разобьются
    parts = raw_data.split("_", 1)
    
    server_id = int(parts[0])
    ib_id = int(parts[1]) if parts[1].isdigit() else parts[1] # Поддержка и чисел, и строковых UUID

    stmt = select(Server).where(Server.id == server_id)
    res = await db_session.execute(stmt)
    srv = res.scalar_one_or_none()

    if not srv:
        await callback.answer("❌ Сервер базы данных не найден!", show_alert=True)
        return

    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    all_inbounds = await xui.get_inbounds()
    
    # Ищем инбаунд в панели (сравниваем типы данных динамически)
    target_ib = next((i for i in all_inbounds if str(i.get("id")) == str(ib_id)), None)

    if not target_ib:
        await callback.answer("❌ Инбаунд не найден в панели 3x-ui!", show_alert=True)
        return

    # В таблице БД inbound_id храним как String, чтобы поддерживать UUID-панели
    ib_stmt = select(TariffInbound).where(
        TariffInbound.server_id == server_id, 
        func.cast(TariffInbound.inbound_id, String) == str(ib_id)
    )
    ib_res = await db_session.execute(ib_stmt)
    ib_record = ib_res.scalar_one_or_none()

    if not ib_record:
        # 1. Если порта нет в БД -> переводим в BASE
        new_record = TariffInbound(
            server_id=server_id, plan_type=SubscriptionType.BASE, inbound_id=str(ib_id),
            protocol_name=target_ib.get("protocol", "unknown"), port=target_ib.get("port", 0),
            remark=target_ib.get("remark", "")
        )
        db_session.add(new_record)
        await callback.answer("🟢 Переведено в тариф BASE")
    elif ib_record.plan_type == SubscriptionType.BASE:
        # 2. Если порт в BASE -> переводим в PREMIUM
        ib_record.plan_type = SubscriptionType.PREMIUM
        await callback.answer("💎 Переведено в тариф PREMIUM")
    else:
        # 3. Если порт в PREMIUM -> удаляем привязку (НЕАКТИВЕН)
        await db_session.delete(ib_record)
        await callback.answer("⚫ Порт полностью деактивирован")

    await db_session.commit()
    
    # Обновляем экран, передавая аргументы в правильном позиционном порядке
    await cb_adm_srv_manage(callback, db_session)


# handlers/admin.py — ШАГ 4.3

@admin_router.callback_query(F.data == "adm_partners_list")
async def cb_adm_partners_list(callback: CallbackQuery, db_session: AsyncSession):
    """Список всех каналов в системе"""
    await callback.answer()
    res = await db_session.execute(select(PartnerChannel))
    channels = res.scalars().all()
    
    text = "📢 <b>Управление каналами и подписками:</b>\n\n"
    kb = []
    
    if not channels:
        text += "<i>Список каналов пока пуст.</i>"
    else:
        for ch in channels:
            role = "🛠 Саппорт" if ch.is_required else "🎁 Спонсор"
            text += f"• <b>{ch.channel_name}</b> [{role}]\nID: <code>{ch.channel_id}</code>\n\n"
            kb.append([InlineKeyboardButton(text=f"❌ Удалить {ch.channel_name}", callback_data=f"adm_part_del_{ch.id}")])
            
    kb.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="adm_part_add_start")])
    kb.append([InlineKeyboardButton(text="◀️ В главное меню админа", callback_data="adm_main_menu")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data == "adm_part_add_start")
async def cb_adm_part_add_start(callback: CallbackQuery, state: FSMContext):
    """Старт сценария добавления канала"""
    await callback.answer()
    await state.set_state(AdminPartnerStates.wait_for_id)
    await callback.message.edit_text(
        "🆔 <b>Шаг 1/4:</b> Введите цифровой <b>Telegram ID канала</b>:\n"
        "<i>(Должен начинаться на -100..., бот должен быть админом в этом канале)</i>"
    )

@admin_router.message(AdminPartnerStates.wait_for_id)
async def msg_part_id(message: Message, state: FSMContext):
    try:
        ch_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом! Попробуйте еще раз:")
        return
    await state.update_data(channel_id=ch_id)
    await state.set_state(AdminPartnerStates.wait_for_name)
    await message.answer("📝 <b>Шаг 2/4:</b> Введите название канала для отображения:")

@admin_router.message(AdminPartnerStates.wait_for_name)
async def msg_part_name(message: Message, state: FSMContext):
    await state.update_data(channel_name=message.text.strip())
    await state.set_state(AdminPartnerStates.wait_for_link)
    await message.answer("🔗 <b>Шаг 3/4:</b> Введите ссылку-приглашение (Invite Link):")

@admin_router.message(AdminPartnerStates.wait_for_link)
async def msg_part_link(message: Message, state: FSMContext):
    await state.update_data(invite_link=message.text.strip())
    await state.set_state(AdminPartnerStates.wait_for_type)
    
    kb = [
        [InlineKeyboardButton(text="🎁 Спонсор (для бонусов)", callback_data="role_sponsor")],
        [InlineKeyboardButton(text="🛠 Обязательный (саппорт-канал)", callback_data="role_support")]
    ]
    await message.answer("🎯 <b>Шаг 4/4:</b> Выберите назначение канала:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(AdminPartnerStates.wait_for_type, F.data.startswith("role_"))
async def cb_part_finalize(callback: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """Сохранение канала в БД"""
    await callback.answer()
    is_required = (callback.data == "role_support")
    
    data = await state.get_data()
    await state.clear()
    
    new_channel = PartnerChannel(
        channel_id=data["channel_id"],
        channel_name=data["channel_name"],
        invite_link=data["invite_link"],
        is_required=is_required
    )
    db_session.add(new_channel)
    await db_session.commit()
    
    await callback.message.edit_text(f"✅ <b>Канал '{data['channel_name']}' успешно сохранен!</b>")
    await cb_adm_partners_list(callback, db_session)

@admin_router.callback_query(F.data.startswith("adm_part_del_"))
async def cb_adm_part_del(callback: CallbackQuery, db_session: AsyncSession):
    """Удаление партнерского канала"""
    ch_id = int(callback.data.split("_")[-1])
    stmt = select(PartnerChannel).where(PartnerChannel.id == ch_id)
    res = await db_session.execute(stmt)
    channel = res.scalar_one_or_none()
    
    if channel:
        await db_session.delete(channel)
        await db_session.commit()
        await callback.answer("🗑 Канал успешно удален!")
    else:
        await callback.answer("❌ Канал не найден.", show_alert=True)
        
    await cb_adm_partners_list(callback, db_session)

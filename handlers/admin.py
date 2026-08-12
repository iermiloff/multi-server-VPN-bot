# handlers/admin.py — ЧАСТЬ 1
import logging
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import Server, TariffInbound, PartnerChannel, SubscriptionType
from services.xui import XUIMultiClient

logger = logging.getLogger(__name__)
admin_router = Router()

# Жесткий фильтр: только для ADMIN_IDS из .env
admin_router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

class AdminServerStates(StatesGroup):
    wait_for_name = State()     # Название сервера
    wait_for_url = State()      # URL панели
    wait_for_token = State()    # Bearer токен
    wait_for_sub_port = State() # Порт подписки

# Исправленный хендлер в handlers/admin.py (Часть 1)
@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Главное меню админ-панели с динамическим брендом"""
    kb = [
        [InlineKeyboardButton(text="🖥 Управление серверами", callback_data="adm_servers_list")],
        [InlineKeyboardButton(text="📢 Каналы-партнеры", callback_data="adm_partners_list")]
    ]
    # Теперь имя бренда берется строго из настроек окружения
    await message.answer(f"👑 <b>Панель администратора {config.BRAND_NAME}</b>", 
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data == "adm_servers_list")
async def cb_adm_servers_list(callback: CallbackQuery, db_session: AsyncSession):
    """Вывод списка всех подключенных нод"""
    await callback.answer()
    res = await db_session.execute(select(Server))
    servers = res.scalars().all()
    
    text = "🖥 <b>Список подключенных серверов 3x-ui:</b>\n\n"
    kb = []
    
    for s in servers:
        status = "✅" if s.is_active else "❌"
        text += f"{status} <b>{s.name}</b> (Порт подписки: {s.sub_port})\n<code>{s.api_url}</code>\n\n"
        kb.append([InlineKeyboardButton(text=f"⚙️ Настроить {s.name}", callback_data=f"adm_srv_manage_{s.id}")])
        
    kb.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="adm_srv_add_start")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data == "adm_srv_add_start")
async def cb_adm_srv_add_start(callback: CallbackQuery, state: FSMContext):
    """Старт сценария добавления сервера"""
    await callback.answer()
    await state.set_state(AdminServerStates.wait_for_name)
    await callback.message.edit_text("📝 <b>Шаг 1/4:</b> Введите понятное название для сервера (например: <code>Германия #1</code>):")

@admin_router.message(AdminServerStates.wait_for_name)
async def msg_srv_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_url)
    await message.answer("🔗 <b>Шаг 2/4:</b> Введите полный URL API панели (например: <code>https://123.45.67.89:2053</code>):")

@admin_router.message(AdminServerStates.wait_for_url)
async def msg_srv_url(message: Message, state: FSMContext):
    await state.update_data(api_url=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_token)
    await message.answer("🔑 <b>Шаг 3/4:</b> Введите <b>API Token (Bearer)</b> панели.\n\n<i>Его можно взять в панели 3x-ui: Настройки панели -> Безопасность -> API Token.</i>")

@admin_router.message(AdminServerStates.wait_for_token)
async def msg_srv_token(message: Message, state: FSMContext):
    await state.update_data(api_token=message.text.strip())
    await state.set_state(AdminServerStates.wait_for_sub_port)
    
    # Даем подсказку администратору на основе скриншота настроек подписки
    hint_text = (
        "🔌 <b>Шаг 4/4: Введите порт подписки для этого сервера</b>\n\n"
        "ℹ️ <i>По умолчанию в 3x-ui используется порт <b>2096</b>. Вы можете найти его в панели, "
        "перейдя в <b>'Настройки панели' -> вкладка 'Подписка'</b>. Смотрите на значение в поле "
        "'Порт подписки по умолчанию'.</i>"
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
    
    # Сохраняем новую ноду в базу данных бота
    new_server = Server(
        name=data["name"],
        api_url=data["api_url"],
        api_token=data["api_token"],
        sub_port=sub_port
    )
    db_session.add(new_server)
    await db_session.commit()
    
    await message.answer(f"✅ <b>Сервер '{data['name']}' успешно добавлен!</b>\n\nТеперь перейдите в его настройки, чтобы распределить локальные инбаунды по тарифам.")

# handlers/admin.py — ЧАСТЬ 2

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

    # Загружаем текущие привязки инбаундов из БД
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
        
        # Красивые статус-бэджи для меню
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


@admin_router.callback_query(F.data.startswith("adm_toggle_ib_"))
async def cb_adm_toggle_ib(callback: CallbackQuery, db_session: AsyncSession):
    """Циклическое переключение тарифа инбаунда: НЕАКТИВЕН -> BASE -> PREMIUM -> НЕАКТИВЕН"""
    parts = callback.data.split("_")
    server_id = int(parts[3])
    ib_id = int(parts[4])

    stmt = select(Server).where(Server.id == server_id)
    res = await db_session.execute(stmt)
    srv = res.scalar_one()

    xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
    all_inbounds = await xui.get_inbounds()
    target_ib = next((i for i in all_inbounds if i.get("id") == ib_id), None)

    if not target_ib:
        await callback.answer("❌ Инбаунд не найден в панели!", show_alert=True)
        return

    ib_stmt = select(TariffInbound).where(TariffInbound.server_id == server_id, TariffInbound.inbound_id == ib_id)
    ib_res = await db_session.execute(ib_stmt)
    ib_record = ib_res.scalar_one_or_none()

    if not ib_record:
        # 1. Если был отключен -> переводим в BASE
        new_record = TariffInbound(
            server_id=server_id, plan_type=SubscriptionType.BASE, inbound_id=ib_id,
            protocol_name=target_ib.get("protocol", "unknown"), port=target_ib.get("port", 0),
            remark=target_ib.get("remark", "")
        )
        db_session.add(new_record)
        await callback.answer("🟢 Переведено в тариф BASE")
    elif ib_record.plan_type == SubscriptionType.BASE:
        # 2. If был в BASE -> переводим в PREMIUM
        ib_record.plan_type = SubscriptionType.PREMIUM
        await callback.answer("💎 Переведено в тариф PREMIUM")
    else:
        # 3. Если был в PREMIUM -> полностью отключаем
        await db_session.delete(ib_record)
        await callback.answer("⚫ Порт полностью деактивирован")

    await db_session.commit()
    await cb_adm_srv_manage(callback, db_session)


# handlers/admin.py — ЧАСТЬ 3

class AdminPartnerStates(StatesGroup):
    wait_for_id = State()     # Telegram ID канала (отрицательный)
    wait_for_name = State()   # Название для отображения
    wait_for_link = State()   # Инвайт-ссылка
    wait_for_type = State()   # Тип: спонсор или саппорт

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
    kb.append([InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]) # использует глобальный триггер возврата
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data == "adm_part_add_start")
async def cb_adm_part_add_start(callback: CallbackQuery, state: FSMContext):
    """Старт сценария добавления канала"""
    await callback.answer()
    await state.set_state(AdminPartnerStates.wait_for_id)
    await callback.message.edit_text(
        "🆔 <b>Шаг 1/4:</b> Введите цифровой <b>Telegram ID канала</b>:\n"
        "<i>(Обычно начинается на -100..., бот должен быть добавлен в этот канал как администратор)</i>"
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
    await message.answer("📝 <b>Шаг 2/4:</b> Введите название канала для отображения пользователям:")

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
        [InlineKeyboardButton(text="🎁 Спонсор (для бесплатного месяца)", callback_data="role_sponsor")],
        [InlineKeyboardButton(text="🛠 Обязательный (техподдержка при старте)", callback_data="role_support")]
    ]
    await message.answer("🎯 <b>Шаг 4/4:</b> Выберите назначение канала:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(AdminPartnerStates.wait_for_type, F.data.startswith("role_"))
async def cb_part_finalize(callback: CallbackQuery, state: FSMContext, db_session: AsyncSession):
    """Финал сохранения канала в БД"""
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
    
    await callback.message.edit_text(f"✅ <b>Канал '{data['channel_name']}' успешно зарегистрирован в СУБД!</b>")
    # Возвращаем админа к общему списку каналов
    await cb_adm_partners_list(callback, db_session)

@admin_router.callback_query(F.data.startswith("adm_part_del_"))
async def cb_adm_part_del(callback: CallbackQuery, db_session: AsyncSession):
    """Удаление канала спонсора/поддержки из системы"""
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

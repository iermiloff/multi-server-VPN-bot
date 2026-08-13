# handlers/user.py — ШАГ 1 ИЗ 4
import logging
import uuid
import datetime
from aiogram import Router, Bot, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import (
    User, PartnerChannel, Subscription, 
    VPNKey, Server, TariffInbound, SubscriptionType
)
from services.xui import XUIMultiClient
from services.cryptobot import cryptobot_client 

logger = logging.getLogger(__name__)
user_router = Router()

async def check_main_channel_sub(bot: Bot, session: AsyncSession, user_id: int) -> bool:
    """Проверяет обязательную подписку на главный канал (is_required=True)"""
    stmt = select(PartnerChannel).where(PartnerChannel.is_required == True)
    res = await session.execute(stmt)
    main_channel = res.scalar_one_or_none()
    
    if not main_channel or main_channel.channel_id == -1000000000000:
        return True
        
    try:
        member = await bot.get_chat_member(chat_id=main_channel.channel_id, user_id=user_id)
        if member.status not in ["left", "kicked"]:
            return True
    except Exception as e:
        logger.error(f"Ошибка проверки главной подписки: {e}")
        return True
    return False

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Генерация кнопок главного меню для обычных пользователей"""
    keyboard = [
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
        [InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu_buy")],
        [InlineKeyboardButton(text="🎁 Месяц от партнеров", callback_data="menu_partner_gift")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Кнопки внутри личного кабинета пользователя"""
    keyboard = [
        [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="menu_profile")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# handlers/user.py — ШАГ 2 ИЗ 4

@user_router.message(CommandStart())
async def cmd_start(message: Message, db_session: AsyncSession, bot: Bot):
    """Регистрация нового пользователя и жесткое разделение потоков"""
    # РАЗДЕЛЕНИЕ ПОТОКОВ: Если пишет админ — сразу перенаправляем его в админку
    if message.from_user.id in config.ADMIN_IDS:
        from handlers.admin import cmd_admin
        await cmd_admin(message, db_session)
        return

    stmt = select(User).where(User.telegram_id == message.from_user.id)
    res = await db_session.execute(stmt)
    user = res.scalar_one_or_none()
    
    if not user:
        user = User(telegram_id=message.from_user.id, username=message.from_user.username)
        db_session.add(user)
        await db_session.commit()

    if not await check_main_channel_sub(bot, db_session, message.from_user.id):
        stmt = select(PartnerChannel).where(PartnerChannel.is_required == True)
        ch_res = await db_session.execute(stmt)
        channel = ch_res.scalar_one()
        
        kb = [
            [InlineKeyboardButton(text="📢 Подписаться", url=channel.invite_link)],
            [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub_again")]
        ]
        await message.answer(
            text=f"❌ <b>Доступ ограничен!</b>\n\nДля использования <b>{config.BRAND_NAME}</b> необходимо подписаться на наш официальный канал:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
        return

    await message.answer(
        text=f"👋 Добро пожаловать в <b>{config.BRAND_NAME}</b>!\n\nИспользуйте меню ниже для управления вашим доступом:",
        reply_markup=get_main_menu_keyboard()
    )

@user_router.callback_query(F.data == "check_sub_again")
async def cb_check_sub_again(callback: CallbackQuery, db_session: AsyncSession, bot: Bot):
    """Повторная проверка подписки на саппорт-канал при клике"""
    if await check_main_channel_sub(bot, db_session, callback.from_user.id):
        await callback.answer("✅ Подписка подтверждена!")
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="🎉 Отлично! Доступ открыт. Выберите нужный раздел:",
            reply_markup=get_main_menu_keyboard()
        )
        await callback.message.delete()
    else:
        await callback.answer("❌ Вы всё еще не подписались на канал техподдержки!", show_alert=True)

@user_router.callback_query(F.data == "menu_profile")
async def cb_menu_profile(callback: CallbackQuery, db_session: AsyncSession):
    await callback.answer()
    now = datetime.datetime.utcnow()
    
    stmt = (
        select(User)
        .where(User.telegram_id == callback.from_user.id)
        .options(
            selectinload(User.subscriptions)
            .selectinload(Subscription.keys)
            .selectinload(VPNKey.server)
        )
    )
    result = await db_session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        await callback.message.edit_text("❌ Ошибка профиля. Перезапустите бота через /start")
        return
        
    premium_sub = next((s for s in user.subscriptions if s.plan_type == SubscriptionType.PREMIUM and s.is_active and not s.is_pending and s.expires_at > now), None)
    profile_text = f"👤 <b>Личный кабинет</b>\n\n• Твой Telegram ID: <code>{user.telegram_id}</code>\n"
    active_exists = any(s.is_active and s.expires_at > now for s in user.subscriptions)
    
    if not active_exists:
        profile_text += "• Статус подписки: ❌ <b>Не активна</b>\n\nУ вас пока нет активных подключений."
    else:
        profile_text += "• Статус подписки: ✅ <b>Активна</b>\n\n🔗 <b>Ваши доступы и ссылки подписки:</b>\n"
        
        all_user_keys = []
        for s in user.subscriptions:
            for k in s.keys:
                all_user_keys.append(k)
                
        sorted_subs = sorted(user.subscriptions, key=lambda x: 1 if x.plan_type == SubscriptionType.PREMIUM else 2)
        
        for sub in sorted_subs:
            if not sub.is_active:
                continue
                
            expires_str = sub.expires_at.strftime("%d.%m.%Y %H:%M")
            
            # 1. Если тариф заморожен в очереди
            if sub.is_pending:
                saved_days = (sub.expires_at - sub.created_at).days
                if saved_days <= 0: saved_days = 30
                
                if premium_sub:
                    prem_end_str = premium_sub.expires_at.strftime("%d.%m.%Y %H:%M")
                    profile_text += (
                        f"\n⏳ <b>Тариф: {sub.plan_type.upper()} (В очереди: {saved_days} дней)</b>\n"
                        f"└ 💤 <i>Запустится автоматически <code>{prem_end_str}</code>.</i>\n"
                    )
                continue
                
            # 2. Если тариф активен прямо сейчас
            if sub.expires_at <= now: 
                continue
                
            profile_text += f"\nТариф: <b>{sub.plan_type.upper()}</b> (До: <code>{expires_str}</code>)\n"
            
            # --- РАСЧЕТ ЖИВОГО ТРАФИКА С ПАНЕЛИ ПО API ---
            traffic_text = "📊 Трафик: <i>⌛ Загружаем счетчики...</i>\n"
            if all_user_keys:
                # Берем первый доступный ключ, чтобы считать данные с живой ноды
                target_key = all_user_keys[0]
                if target_key.server:
                    xui = XUIMultiClient(api_url=target_key.server.api_url, api_token=target_key.server.api_token)
                    stats = await xui.get_client_traffic(target_key.client_email)
                    
                    if stats:
                        # Извлекаем байты (up + down)
                        bytes_used = stats.get("up", 0) + stats.get("down", 0)
                        
                        # Переводим байты в Гигабайты с округлением до 1 знака
                        gb_used = round(bytes_used / (1024 * 1024 * 1024), 1)
                        gb_limit = 300 if sub.plan_type == SubscriptionType.PREMIUM else 150
                        
                        # Высчитываем остаток
                        gb_left = max(0.0, round(gb_limit - gb_used, 1))
                        
                        # Рисуем красивую визуальную прогресс-полосу из 10 делений
                        used_segments = min(10, int(gb_used / (gb_limit / 10)))
                        bar = "🟩" * used_segments + "⬜" * (10 - used_segments)
                        
                        traffic_text = f"📊 Трафик: <b>{gb_used} ГБ</b> из <b>{gb_limit} ГБ</b>\n└ Осталось: <b>{gb_left} ГБ</b>\n└ Контур: <code>{bar}</code>\n"
            
            profile_text += traffic_text
            
            # Выводим ссылки серверов сети
            if not all_user_keys:
                profile_text += "<i>⌛ Нарезаем доступ на серверах...</i>\n"
            else:
                for key in all_user_keys:
                    if key.server:
                        srv = key.server
                        protocol = "http" if srv.api_url.startswith("http") and "myepicpanel" not in srv.api_url else "https"
                        from urllib.parse import urlparse
                        parsed_url = urlparse(srv.api_url)
                        clean_domain = parsed_url.hostname
                        dynamic_url = f"{protocol}://{clean_domain}:{srv.sub_port}/{srv.sub_path}/{key.sub_id}"
                    else:
                        dynamic_url = "Ошибка: Сервер удален"
                        
                    server_name = key.server.name if key.server else "Сервер"
                    profile_text += f"├ 🌍 <b>{server_name}:</b> <code>{dynamic_url}</code>\n"
                    
        profile_text += "\n💡 <i>Нажмите на код ссылки выше, чтобы скопировать её.</i>"
        
    try:
        await callback.message.edit_text(text=profile_text, reply_markup=get_profile_keyboard())
    except Exception:
        pass


@user_router.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: CallbackQuery):
    """Быстрый возврат на главный экран пользователя"""
    await callback.answer()
    await callback.message.edit_text(
        text=f"👋 Добро пожаловать в <b>{config.BRAND_NAME}</b>!\n\nИспользуйте меню ниже для управления вашим доступом:",
        reply_markup=get_main_menu_keyboard()
    )

# handlers/user.py — ШАГ 3 ИЗ 4

def get_periods_keyboard(plan_type: str) -> InlineKeyboardMarkup:
    """Выбор длительности подписки"""
    keyboard = [
        [
            InlineKeyboardButton(text="⏳ 1 Месяц", callback_data=f"buy_time_{plan_type}_30"),
            InlineKeyboardButton(text="⏳ 3 Месяца (-10%)", callback_data=f"buy_time_{plan_type}_90")
        ],
        [InlineKeyboardButton(text="⏳ 6 Месяцев (-20%)", callback_data=f"buy_time_{plan_type}_180")],
        [InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="menu_buy")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_price(plan_type: str, days: int) -> float:
    """Динамический расчет цен на основе конфигурации .env"""
    prices = {
        "base": {
            30: config.PRICE_BASE_1_MONTH, 
            90: config.PRICE_BASE_3_MONTHS, 
            180: config.PRICE_BASE_6_MONTHS
        },
        "premium": {
            30: config.PRICE_PREMIUM_1_MONTH, 
            90: config.PRICE_PREMIUM_3_MONTHS, 
            180: config.PRICE_PREMIUM_6_MONTHS
        }
    }
    return prices.get(plan_type, {}).get(days, 0.0)

@user_router.callback_query(F.data == "menu_buy")
async def cb_menu_buy(callback: CallbackQuery):
    """Экран выбора уровня доступа с поддержкой двух тарифов"""
    await callback.answer()
    cur = config.PAYMENT_CURRENCY
    
    text = (
        f"💎 <b>Покупка подписки {config.BRAND_NAME}</b>\n\n"
        f"🚀 <b>Тариф БАЗОВЫЙ (BASE):</b>\n"
        f"• Доступ к ультрабыстрым локациям\n"
        f"• Цена: от <code>{config.PRICE_BASE_1_MONTH}</code> {cur} / мес.\n\n"
        f"👑 <b>Тариф ПРЕМИУМ (PREMIUM):</b>\n"
        f"• Доступ ко ВСЕМ серверам сети + VIP-протоколы\n"
        f"• Цена: от <code>{config.PRICE_PREMIUM_1_MONTH}</code> {cur} / мес."
    )
    kb = [
        [InlineKeyboardButton(text="⚡ Купить БАЗОВЫЙ", callback_data="buy_plan_base")],
        [InlineKeyboardButton(text="🔥 Купить ПРЕМИУМ", callback_data="buy_plan_premium")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]
    ]
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@user_router.callback_query(F.data.startswith("buy_plan_"))
async def cb_buy_plan(callback: CallbackQuery):
    """Экран выбора периода подписки"""
    await callback.answer()
    plan_type = callback.data.split("_")[-1]
    
    text = "📅 <b>Выберите срок действия вашей подписки:</b>"
    await callback.message.edit_text(text=text, reply_markup=get_periods_keyboard(plan_type))

@user_router.callback_query(F.data.startswith("buy_time_"))
async def cb_generate_invoice(callback: CallbackQuery):
    """Генерация счета в CryptoBot для оплаты подписки"""
    await callback.answer()
    parts = callback.data.split("_")
    plan_type = parts[-2]
    days = int(parts[-1])
    
    price = get_price(plan_type, days)
    asset = config.PAYMENT_CURRENCY
    await callback.message.edit_text("⏳ <i>Формирую счет на оплату, пожалуйста, подождите...</i>")
    
    payload = f"{callback.from_user.id}:{plan_type}:{days}"
    description = f"Оплата {config.BRAND_NAME}: тариф {plan_type.upper()} на {days} дней"
    
    invoice = await cryptobot_client.create_invoice(
        amount=price, asset=asset, description=description, payload=payload
    )
    
    if invoice and invoice.get("bot_invoice_url"):
        kb = [
            [InlineKeyboardButton(text="💳 Оплатить счет", url=invoice["bot_invoice_url"])],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_invoice_{invoice['invoice_id']}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_buy")]
        ]
        text = (
            f"🧾 <b>Счет успешно выставлен!</b>\n\n"
            f"• <b>Тариф:</b> {plan_type.upper()}\n"
            f"• <b>Срок:</b> {days} дней\n"
            f"• <b>К оплате:</b> <code>{invoice['amount']}</code> {asset}\n\n"
            f"Оплатите счет в @CryptoBot и нажмите «Проверить оплату»."
        )
        await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await callback.message.edit_text(
            text="❌ Не удалось связаться с CryptoBot. Пожалуйста, попробуйте позже.",
            reply_markup=get_main_menu_keyboard()
        )

# handlers/user.py — ШАГ 4 ИЗ 4

@user_router.callback_query(F.data == "menu_partner_gift")
async def cb_menu_partner_gift(callback: CallbackQuery, db_session: AsyncSession):
    """Условия получения бесплатного месяца от партнеров — ПОЖИЗНЕННЫЙ БЛОК ПОВТОРОВ"""
    await callback.answer()
    
    stmt = select(User).where(User.telegram_id == callback.from_user.id)
    res = await db_session.execute(stmt)
    user = res.scalar_one()
    
    # ИСПРАВЛЕНО: Убрали кулдаун в 30 дней. Если дата запрашивания триала есть — акция закрыта навсегда
    if user.last_partner_trial:
        await callback.message.edit_text(
            text="❌ <b>Доступ уже запрашивался!</b>\n\nЭтот приветственный бонус доступен только 1 раз при регистрации.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")
            ]])
        )
        return

    stmt = select(PartnerChannel).where(PartnerChannel.is_required == False)
    res = await db_session.execute(stmt)
    channels = res.scalars().all()
    
    if not channels:
        await callback.message.edit_text(
            text="🎁 Извините, список партнеров временно пуст. Зайдите позже!",
            reply_markup=get_main_menu_keyboard()
        )
        return

    text = "🎁 <b>Месяц бесплатного VPN от партнеров!</b>\n\nПодпишитесь на каналы спонсоров:"
    kb = []
    for i, ch in enumerate(channels, 1):
        text += f"\n{i}. {ch.channel_name}"
        kb.append([InlineKeyboardButton(text=f"📢 Канал {i}", url=ch.invite_link)])
        
    kb.append([InlineKeyboardButton(text="✅ Проверить и получить месяц", callback_data="claim_partner_bonus")])
    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@user_router.callback_query(F.data == "claim_partner_bonus")
async def cb_claim_partner_bonus(callback: CallbackQuery, db_session: AsyncSession, bot: Bot):
    """Проверка подписок и запуск активации бонуса"""
    stmt = select(PartnerChannel).where(PartnerChannel.is_required == False)
    res = await db_session.execute(stmt)
    channels = res.scalars().all()
    
    for ch in channels:
        try:
            m = await bot.get_chat_member(chat_id=ch.channel_id, user_id=callback.from_user.id)
            if m.status in ["left", "kicked"]:
                await callback.answer("❌ Вы подписались не на все каналы из списка!", show_alert=True)
                return
        except Exception:
            await callback.answer("❌ Ошибка проверки каналов партнеров.", show_alert=True)
            return

    await callback.message.edit_text("⏳ <i>Проверка пройдена! Нарезаем подписки на серверах...</i>")
    await provision_multiserver_subscription(callback, db_session)

async def provision_multiserver_subscription(callback: CallbackQuery, db_session: AsyncSession):
    """Логика каскадного пуша клиента на все доступные панели 3x-ui"""
    now = datetime.datetime.utcnow()
    user_id = callback.from_user.id
    
    stmt = select(User).where(User.telegram_id == user_id).options(selectinload(User.required_channels))
    res = await db_session.execute(stmt)
    user = res.scalar_one()
    user.last_partner_trial = now
    user.has_active_partner_bonus = True

    channels_stmt = select(PartnerChannel).where(PartnerChannel.is_required == False)
    channels_res = await db_session.execute(channels_stmt)
    user.required_channels = channels_res.scalars().all()

    sub = Subscription(user_id=user_id, plan_type=SubscriptionType.BASE, expires_at=now + datetime.timedelta(days=30))
    db_session.add(sub)
    await db_session.flush()

    servers_res = await db_session.execute(select(Server).where(Server.is_active == True))
    servers = servers_res.scalars().all()
    
    email = f"usr_{user_id}_{uuid.uuid4().hex[:4]}"
    sub_id = uuid.uuid4().hex
    success_nodes_count = 0

    for srv in servers:
        if sub.plan_type == SubscriptionType.PREMIUM:
            ib_stmt = select(TariffInbound).where(
                TariffInbound.server_id == srv.id,
                TariffInbound.plan_type.in_([SubscriptionType.BASE, SubscriptionType.PREMIUM])
            )
        else:
            ib_stmt = select(TariffInbound).where(
                TariffInbound.server_id == srv.id,
                TariffInbound.plan_type == SubscriptionType.BASE
            )
            
        ib_res = await db_session.execute(ib_stmt)
        inbound_ids = [ib.inbound_id for ib in ib_res.scalars().all()]
        
        if not inbound_ids:
            continue

        xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
        success = await xui.add_client(email=email, sub_id=sub_id, inbound_ids=inbound_ids, expires_days=30)
        
        if success:
            # Нам больше не нужно собирать здесь subscribe_url!
            # Пишем в config_data заглушку, так как Личный кабинет теперь собирает всё сам
            key_record = VPNKey(
                subscription_id=sub.id, 
                server_id=srv.id, 
                client_email=email, 
                sub_id=sub_id, 
                config_data=sub_id # Просто дублируем sub_id
            )
            db_session.add(key_record)
            success_nodes_count += 1


    if success_nodes_count > 0:
        await db_session.commit()
        await callback.message.answer(
            text=f"🎉 <b>Успешно активировано!</b>\n\nВам начислен 1 месяц подписки. Ссылки на подключение ко всем серверам сети ({success_nodes_count} шт.) доступны в личном кабинете!"
        )
    else:
        await db_session.rollback()
        await callback.message.answer(
            text="❌ Произошла техническая ошибка на стороне ноды. Пожалуйста, обратитесь в поддержку."
        )

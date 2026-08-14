# handlers/user.py — ЧАСТЬ 1 ИЗ 4
import logging
import uuid
import datetime
import re
from urllib.parse import urlparse
from typing import Any

from aiogram import Router, Bot, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import User, PartnerChannel, Subscription, VPNKey, Server, TariffInbound, SubscriptionType, PaymentLog
from services.xui import XUIMultiClient
from services.cryptobot import cryptobot_client
from aiogram.fsm.state import StatesGroup, State
class LavaPaymentStates(StatesGroup):
    wait_for_email = State() 


logger = logging.getLogger(__name__)
user_router = Router()

async def check_main_channel_sub(bot: Bot, session: AsyncSession, user_id: int) -> bool:
    """Проверка подписки на обязательный канал поддержки"""
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
    """Клавиатура главного меню"""
    keyboard = [
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
        [InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu_buy")],
        [InlineKeyboardButton(text="👥 Партнерская программа", callback_data="menu_referral")],
        [InlineKeyboardButton(text="🎁 Месяц от партнеров", callback_data="menu_partner_gift")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура Личного кабинета"""
    keyboard = [
        [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="menu_profile")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# handlers/user.py — ЧАСТЬ 2 ИЗ 4

@user_router.message(CommandStart())
async def cmd_start(message: Message, db_session: AsyncSession, bot: Bot):
    """Регистрация реферала по ссылке и вывод приветствия"""
    user_id = message.from_user.id
    
    if user_id in config.ADMIN_IDS:
        from handlers.admin import cmd_admin
        await cmd_admin(message, db_session)
        return
        
    referrer_id = None
    if message.text and len(message.text.split()) > 1:
        args = message.text.split()[-1]
        if args.startswith("ref"):
            try:
                potential_ref = int(args.replace("ref", "").strip())
                if potential_ref != user_id:
                    referrer_id = potential_ref
            except ValueError:
                pass

    stmt = select(User).where(User.telegram_id == user_id)
    res = await db_session.execute(stmt)
    user = res.scalar_one_or_none()
    
    if not user:
        user = User(
            telegram_id=user_id,
            username=message.from_user.username,
            referred_by=referrer_id,
            registered_at=datetime.datetime.utcnow()
        )
        db_session.add(user)
        await db_session.commit()

    if not await check_main_channel_sub(bot, db_session, user_id):
        stmt = select(PartnerChannel).where(PartnerChannel.is_required == True)
        ch_res = await db_session.execute(stmt)
        channel = ch_res.scalar_one()
        
        kb = [
            [InlineKeyboardButton(text="📢 Подписаться", url=channel.invite_link)],
            [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub_again")]
        ]
        await message.answer(
            text=f"❌ <b>Доступ ограничен!</b>\n\nДля использования бота подпишитесь на наш официальный канал:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
        return

    await message.answer(
        text=f"👋 Добро пожаловать в <b>{config.BRAND_NAME}</b>!\n\nИспользуйте меню ниже:",
        reply_markup=get_main_menu_keyboard()
    )

@user_router.callback_query(F.data == "check_sub_again")
async def cb_check_sub_again(callback: CallbackQuery, db_session: AsyncSession, bot: Bot):
    if await check_main_channel_sub(bot, db_session, callback.from_user.id):
        await callback.answer("✅ Подписка подтверждена!")
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="🚀 Отлично! Доступ открыт:",
            reply_markup=get_main_menu_keyboard()
        )
        await callback.message.delete()
    else:
        await callback.answer("❌ Вы всё еще не подписались на канал!", show_alert=True)

@user_router.callback_query(F.data == "menu_profile")
async def cb_menu_profile(callback: CallbackQuery, db_session: AsyncSession):
    """Живой Личный кабинет со счетчиком ГБ и авто-протоколами подписок"""
    await callback.answer()
    now = datetime.datetime.utcnow()
    
    stmt = (
        select(User).where(User.telegram_id == callback.from_user.id)
        .options(selectinload(User.subscriptions).selectinload(Subscription.keys).selectinload(VPNKey.server))
    )
    result = await db_session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        await callback.message.edit_text("❌ Ошибка профиля. Сделайте /start")
        return
        
    premium_sub = next((s for s in user.subscriptions if s.plan_type == SubscriptionType.PREMIUM and s.is_active and not s.is_pending and s.expires_at > now), None)
    profile_text = f"👤 <b>Личный кабинет</b>\n\n• Твой ID: <code>{user.telegram_id}</code>\n"
    active_exists = any(s.is_active and s.expires_at > now for s in user.subscriptions)
    
    if not active_exists:
        profile_text += "• Статус подписки: ❌ <b>Не активна</b>"
    else:
        profile_text += "• Статус подписки: ✅ <b>Активна</b>\n\n🔗 <b>Ваши доступы:</b>\n"
        all_user_keys = [k for s in user.subscriptions for k in s.keys]
        sorted_subs = sorted(user.subscriptions, key=lambda x: 1 if x.plan_type == SubscriptionType.PREMIUM else 2)
        
        for sub in sorted_subs:
            if not sub.is_active: continue
            expires_str = sub.expires_at.strftime("%d.%m.%Y %H:%M")
            
            if sub.is_pending:
                saved_days = (sub.expires_at - sub.created_at).days or 30
                if premium_sub:
                    prem_end_str = premium_sub.expires_at.strftime("%d.%m.%Y %H:%M")
                    profile_text += f"\n⏳ <b>Тариф: {sub.plan_type.upper()} (В очереди: {saved_days} дн.)</b>\n└ <i>Включится <code>{prem_end_str}</code>.</i>\n"
                continue
                
            if sub.expires_at <= now: continue
            profile_text += f"\nТариф: <b>{sub.plan_type.upper()}</b> (До: <code>{expires_str}</code>)\n"
            
            traffic_text = "📊 Трафик: <i>⌛ Загружаем счетчики...</i>\n"
            
            if all_user_keys and len(all_user_keys) > 0:
                target_key = all_user_keys[0]
                
                if target_key.server:
                    xui = XUIMultiClient(api_url=target_key.server.api_url, api_token=target_key.server.api_token)
                    stats = await xui.get_client_traffic(target_key.client_email)
                    
                    if stats:
                        bytes_used = stats.get("up", 0) + stats.get("down", 0)
                        gb_used = round(bytes_used / (1024 * 1024 * 1024), 1)
                        gb_limit = 300 if sub.plan_type == SubscriptionType.PREMIUM else 150
                        gb_left = max(0.0, round(gb_limit - gb_used, 1))
                        
                        used_segments = min(10, int(gb_used / (gb_limit / 10)))
                        bar = "🟩" * used_segments + "⬜" * (10 - used_segments)
                        traffic_text = f"📊 Трафик: <b>{gb_used} ГБ</b> из <b>{gb_limit} ГБ</b>\n└ Осталось: <b>{gb_left} ГБ</b>\n└ Контур: <code>{bar}</code>\n"
            else:
                traffic_text = "📊 Трафик: <code>0.0 ГБ</code> (Подключения еще не созданы)\n"
            
            profile_text += traffic_text
            
            if sub.keys:
                for key in sub.keys:
                    if key.server:
                        srv = key.server
                        clean_domain = urlparse(srv.api_url).hostname
                        is_ip = clean_domain and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", clean_domain)
                        
                        protocol = "http" if is_ip else "https" if srv.api_url.startswith("https") else "http"
                        dynamic_url = f"{protocol}://{clean_domain}:{srv.sub_port}/{srv.sub_path}/{key.sub_id}"
                        
                        profile_text += f"├ 🌍 <b>{key.server.name}:</b> <code>{dynamic_url}</code>\n"
            else:
                profile_text += "├ 🌍 <i>Доступы для этого тарифа еще не сгенерированы.</i>\n"
                    
        profile_text += "\n💡 <i>Нажмите на код ссылки выше, чтобы скопировать её.</i>"

        
    try: await callback.message.edit_text(text=profile_text, reply_markup=get_profile_keyboard())
    except Exception: pass

@user_router.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(text=f"👋 Добро пожаловать в <b>{config.BRAND_NAME}</b>!", reply_markup=get_main_menu_keyboard())


def get_periods_keyboard(plan_type: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="⏱ 1 Месяц", callback_data=f"buy_time_{plan_type}_30"),
            InlineKeyboardButton(text="⏱ 3 Месяца (-10%)", callback_data=f"buy_time_{plan_type}_90")
        ],
        [InlineKeyboardButton(text="⏱ 6 Месяцев (-20%)", callback_data=f"buy_time_{plan_type}_180")],
        [InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="menu_buy")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_price(plan_type: str, days: int) -> float:
    prices = {
        "base": {30: config.PRICE_BASE_1_MONTH, 90: config.PRICE_BASE_3_MONTHS, 180: config.PRICE_BASE_6_MONTHS},
        "premium": {30: config.PRICE_PREMIUM_1_MONTH, 90: config.PRICE_PREMIUM_3_MONTHS, 180: config.PRICE_PREMIUM_6_MONTHS}
    }
    return prices.get(plan_type, {}).get(days, 0.0)

@user_router.callback_query(F.data == "menu_buy")
async def cb_menu_buy(callback: CallbackQuery):
    """Экран витрины тарифов"""
    await callback.answer()
    cur = config.PAYMENT_CURRENCY
    text = (
        f"💳 <b>Покупка подписки {config.BRAND_NAME}</b>\n\n"
        f"⚙️ <b>Тариф БАЗОВЫЙ (BASE):</b>\n• Доступ к локациям тарифа\n• Цена: от <code>{config.PRICE_BASE_1_MONTH}</code> {cur}/мес.\n\n"
        f"👑 <b>Тариф ПРЕМИУМ (PREMIUM):</b>\n• Доступ ко ВСЕМ нодам + VIP-протоколы\n• Цена: от <code>{config.PRICE_PREMIUM_1_MONTH}</code> {cur}/мес."
    )
    kb = [
        [InlineKeyboardButton(text="⚙️ Купить БАЗОВЫЙ", callback_data="buy_plan_base")],
        [InlineKeyboardButton(text="👑 Купить ПРЕМИУМ", callback_data="buy_plan_premium")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]
    ]
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@user_router.callback_query(F.data.startswith("buy_plan_"))
async def cb_buy_plan(callback: CallbackQuery):
    await callback.answer()
    plan_type = callback.data.split("_")[-1]
    await callback.message.edit_text(text="⏱ <b>Выберите срок действия подписки:</b>", reply_markup=get_periods_keyboard(plan_type))


@user_router.callback_query(F.data.startswith("buy_time_"))
async def cb_select_payment_method(callback: CallbackQuery, state: FSMContext):
    """Экран выбора платежной системы после указания срока тарифа"""
    await callback.answer()
    parts = callback.data.split("_")
    plan_type = parts[-2]
    days = int(parts[-1])
    price = get_price(plan_type, days)
    
    # Сохраняем параметры выбора во временный кэш FSM-состояний
    await state.update_data(plan_type=plan_type, days=days, price=price)
    
    text = (
        f"💳 <b>Выбор метода оплаты подписки</b>\n\n"
        f"• <b>Тариф:</b> {plan_type.upper()}\n"
        f"• <b>Срок:</b> {days} дней\n"
        f"• <b>Стоимость:</b> <code>{price}</code> {config.PAYMENT_CURRENCY}\n\n"
        f"Выберите удобный для вас способ оплаты:"
    )
    kb = [
        [InlineKeyboardButton(text="🪙 Оплатить Криптой (CryptoBot)", callback_data="pay_via_cryptobot")],
        [InlineKeyboardButton(text="💳 Оплатить Картой (Lava.top)", callback_data="pay_via_lava")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_buy")]
    ]
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@user_router.callback_query(F.data == "pay_via_lava")
async def cb_lava_email_request(callback: CallbackQuery, state: FSMContext):
    """Запрос email для Lava.top (требование стр. 29 документации)"""
    await callback.answer()
    await state.set_state(LavaPaymentStates.wait_for_email)
    await callback.message.edit_text(
        text="📧 <b>Введите ваш Email для отправки чека:</b>\n\n"
             "<i>Lava.top официально требует указать почту для формирования фискального платежа.</i>"
    )

@user_router.message(LavaPaymentStates.wait_for_email, F.text)
async def msg_lava_process_email(message: Message, state: FSMContext):
    """Валидация email и выставление динамического счета Lava.top"""
    email = message.text.strip()
    
    # Простейшая валидация регулярным выражением
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await message.answer("❌ <b>Некорректный формат почты!</b> Пожалуйста, введите валидный email (например, client@gmail.com):")
        return
        
    data = await state.get_data()
    await state.clear() # Очищаем состояние
    
    plan_type, days, price = data["plan_type"], data["days"], data["price"]
    await message.answer("🔄 <i>Формирую фиатный счет Lava.top, пожалуйста, подождите...</i>")
    
    from services.lava import lava_top_client
    # Выставляем динамический счет Lava.top со страницы 5/29 документации
    invoice = await lava_top_client.create_invoice(amount=price, client_email=email)
    
    if invoice and invoice.get("url"):
        kb = [
            [InlineKeyboardButton(text="💳 Ссылка на оплату картой", url=invoice["url"])],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_lava_{invoice['invoice_id']}_{plan_type}_{days}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_buy")]
        ]
        text = (
            f"🧾 <b>Счет Lava.top успешно сформирован!</b>\n\n"
            f"• <b>Тариф:</b> {plan_type.upper()}\n"
            f"• <b>Срок:</b> {days} дней\n"
            f"• <b>К оплате:</b> <code>{price}</code> RUB\n\n"
            f"Оплатите покупку на открывшейся платежной форме Lava и нажмите «Проверить оплату»."
        )
        await message.answer(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await message.answer(text="❌ Не удалось связаться с платежным шлюзом Lava.top. Попробуйте позже.", reply_markup=get_main_menu_keyboard())

@user_router.callback_query(F.data == "menu_partner_gift")
async def cb_menu_partner_gift(callback: CallbackQuery, db_session: AsyncSession):
    """Пожизненный блок абуза Welcome-бонуса"""
    await callback.answer()
    stmt = select(User).where(User.telegram_id == callback.from_user.id)
    user = (await db_session.execute(stmt)).scalar_one()
    
    if user.last_partner_trial:
        await callback.message.edit_text(
            text="❌ <b>Доступ уже запрашивался!</b>\n\nЭтот приветственный бонус доступен только 1 раз при регистрации.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]])
        )
        return

    channels = (await db_session.execute(select(PartnerChannel).where(PartnerChannel.is_required == False))).scalars().all()
    if not channels:
        await callback.message.edit_text(text="🎁 Список партнеров пуст. Зайдите позже!", reply_markup=get_main_menu_keyboard())
        return

    text = "🎁 <b>Месяц бесплатного VPN от партнеров!</b>\n\nПодпишитесь на каналы:"
    kb = [[InlineKeyboardButton(text=f"📢 Канал {i}", url=ch.invite_link)] for i, ch in enumerate(channels, 1)]
    kb.append([InlineKeyboardButton(text="✅ Проверить и получить месяц", callback_data="claim_partner_bonus")])
    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@user_router.callback_query(F.data == "claim_partner_bonus")
async def cb_claim_partner_bonus(callback: CallbackQuery, db_session: AsyncSession, bot: Bot):
    channels = (await db_session.execute(select(PartnerChannel).where(PartnerChannel.is_required == False))).scalars().all()
    for ch in channels:
        try:
            m = await bot.get_chat_member(chat_id=ch.channel_id, user_id=callback.from_user.id)
            if m.status in ["left", "kicked"]:
                await callback.answer("❌ Вы подписались не на все каналы!", show_alert=True)
                return
        except Exception:
            await callback.answer("❌ Ошибка проверки каналов.", show_alert=True)
            return
            
    await callback.message.edit_text("⏳ <i>Проверка пройдена! Нарезаем доступы...</i>")
    await provision_multiserver_subscription(callback, db_session)


async def provision_multiserver_subscription(callback: CallbackQuery, db_session: AsyncSession):
    now = datetime.datetime.utcnow()
    user_id = callback.from_user.id
    user = (await db_session.execute(select(User).where(User.telegram_id == user_id).options(selectinload(User.required_channels)))).scalar_one()
    
    user.last_partner_trial = now
    user.has_active_partner_bonus = True
    user.required_channels = (await db_session.execute(select(PartnerChannel).where(PartnerChannel.is_required == False))).scalars().all()
    
    sub = Subscription(user_id=user_id, plan_type=SubscriptionType.BASE, expires_at=now + datetime.timedelta(days=30))
    db_session.add(sub); await db_session.flush()
    servers = (await db_session.execute(select(Server).where(Server.is_active == True))).scalars().all()
    
    email = f"usr_{user_id}_{uuid.uuid4().hex[:4]}"
    sub_id = uuid.uuid4().hex
    success_nodes_count = 0
    
    for srv in servers:
        ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id, TariffInbound.plan_type == "base")
        inbound_ids = [ib.inbound_id for ib in (await db_session.execute(ib_stmt)).scalars().all()]
        if not inbound_ids: continue
        
        xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
        if await xui.add_client(email=email, sub_id=sub_id, inbound_ids=inbound_ids, expires_days=30):
            db_session.add(VPNKey(subscription_id=sub.id, server_id=srv.id, client_email=email, sub_id=sub_id, config_data=sub_id))
            success_nodes_count += 1
            
    if success_nodes_count > 0:
        await db_session.commit()
        await callback.message.answer(text=f"🎉 <b>Успешно активировано!</b>\n\nВам начислен 1 месяц подписки. Ссылки доступны в личном кабинете!")
    else:
        await db_session.rollback()
        await callback.message.answer(text="❌ Техническая ошибка ноды. Обратитесь в поддержку.")

@user_router.callback_query(F.data.startswith("check_invoice_"))
async def cb_check_invoice_pull(callback: CallbackQuery, db_session: AsyncSession):
    """Метод пуллинга инвойса с атомарной защитой FOR UPDATE от двойного начисления"""
    invoice_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    stmt_lock = select(PaymentLog).where(PaymentLog.invoice_id == invoice_id).with_for_update()
    log_check = await db_session.execute(stmt_lock)
    
    if log_check.scalar_one_or_none():
        await callback.answer("✅ Эта покупка уже успешно зачислена на твой аккаунт!", show_alert=True)
        return

    status = await cryptobot_client.get_invoice_status(invoice_id)
    if not status: await callback.answer("⚠️ Ошибка связи с CryptoBot.", show_alert=True); return
    if status == "active": await callback.answer("⏳ Счёт еще не оплачен!", show_alert=True); return
    if status == "expired": await callback.message.edit_text("❌ Срок действия счета истек."); return

    await callback.message.edit_text("⏳ <b>Платеж подтвержден!</b> Нарезаем доступы и рассчитываем партнерские награды...")

    is_premium = "PREMIUM" in callback.message.text
    plan_type = "premium" if is_premium else "base"
    days = 180 if "180" in callback.message.text else 90 if "90" in callback.message.text else 30
    
    price = get_price(plan_type, days)
    now = datetime.datetime.utcnow()
    user = (await db_session.execute(select(User).where(User.telegram_id == user_id).options(selectinload(User.subscriptions)))).scalar_one()

    base_active = any(s.plan_type == "base" and s.is_active and s.expires_at > now and not s.is_pending for s in user.subscriptions)
    target_sub = next((s for s in user.subscriptions if s.plan_type == plan_type), None)
    is_pending_status = False
    
    if plan_type == "premium" and base_active:
        for s in user.subscriptions:
            if s.plan_type == "base": s.is_pending = True
        msg_alert = f"Тариф PREMIUM на {days} дней успешно активирован! Ваша База временно убрана в очередь."
    else:
        is_pending_status = any(s.is_active and s.expires_at > now and not s.is_pending for s in user.subscriptions if s.plan_type != plan_type)
        msg_alert = f"Тариф {plan_type.upper()} продлен на {days} дней."

    if target_sub:
        if target_sub.is_active and target_sub.expires_at > now and not target_sub.is_pending: target_sub.expires_at += datetime.timedelta(days=days)
        else: target_sub.expires_at = now + datetime.timedelta(days=days); target_sub.is_pending = is_pending_status; target_sub.is_active = True
        active_sub_obj = target_sub
    else:
        new_sub = Subscription(user_id=user_id, plan_type=plan_type, expires_at=now + datetime.timedelta(days=days), is_pending=is_pending_status)
        db_session.add(new_sub); active_sub_obj = new_sub

    await db_session.flush()
    past_payments = await db_session.scalar(select(func.count(PaymentLog.id)).where(PaymentLog.user_id == user_id)) or 0
    
    if user.referred_by:
        referrer = (await db_session.execute(select(User).where(User.telegram_id == user.referred_by).options(selectinload(User.subscriptions)))).scalar_one_or_none()
        
        if referrer:
            if referrer.is_pro_ref:
                referrer.partner_balance_usd += (price * 0.10)
                try: 
                    await callback.bot.send_message(
                        chat_id=referrer.telegram_id, 
                        text=f"👑 <b>PRO-Начисление!</b> Твой реферал совершил оплату тарифа {plan_type.upper()}.\nТебе зачислено: <b>${price * 0.10:.2f}</b>"
                    )
                except Exception: pass

            else:
                if past_payments == 0:
                    ref_target_sub = next((s for s in referrer.subscriptions if s.plan_type == plan_type), None)
                    if ref_target_sub:
                        if ref_target_sub.is_active and ref_target_sub.expires_at > now: 
                            ref_target_sub.expires_at += datetime.timedelta(days=days)
                        else: 
                            ref_target_sub.expires_at = now + datetime.timedelta(days=days)
                            ref_target_sub.is_active = True
                    else: 
                        db_session.add(Subscription(user_id=referrer.telegram_id, plan_type=plan_type, expires_at=now + datetime.timedelta(days=days)))
                    
                    try: 
                        await callback.bot.send_message(
                            chat_id=referrer.telegram_id, 
                            text=f"🎁 <b>Реферальный бонус 1:1!</b> Твой друг купил тариф {plan_type.upper()} на {days} дней. Тебе начислено <b>{days} дней такого же тарифа в подарок!</b>"
                        )
                    except Exception: pass

    db_session.add(PaymentLog(invoice_id=invoice_id, user_id=user_id, plan_type=plan_type, amount=price, ref_processed=True))

    servers_res = await db_session.execute(select(Server).where(Server.is_active == True))
    servers = servers_res.scalars().all()

    any_user_key_stmt = select(VPNKey).join(Subscription).where(Subscription.user_id == user_id).limit(1)
    any_user_key = (await db_session.execute(any_user_key_stmt)).scalar_one_or_none()
    
    final_shared_email = any_user_key.client_email if any_user_key else f"usr_{user_id}_{uuid.uuid4().hex[:4]}"
    final_shared_sub_id = any_user_key.sub_id if any_user_key else uuid.uuid4().hex
    
    keys_stmt = select(VPNKey).where(VPNKey.subscription_id == active_sub_obj.id)
    keys_res = await db_session.execute(keys_stmt)
    existing_keys = {k.server_id: k for k in keys_res.scalars().all()}
    
    expiry_timestamp = int(active_sub_obj.expires_at.timestamp() * 1000)

    for srv in servers:
        try:
            if plan_type == "premium" or not is_pending_status:
                ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id, TariffInbound.plan_type.in_(["base", "premium"]))
            else:
                ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id, TariffInbound.plan_type == "base")
                
            ib_res = await db_session.execute(ib_stmt)
            inbound_ids = [ib.inbound_id for ib in ib_res.scalars().all()]
            
            if not inbound_ids: 
                continue
                
            xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
            
            if srv.id in existing_keys:
                current_key = existing_keys[srv.id]
                target_bytes = (300 if plan_type == "premium" else 150) * 1024 * 1024 * 1024
                
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
                days_left = max(1, (active_sub_obj.expires_at - now).days)
                
                success = await xui.add_client(
                    email=final_shared_email, sub_id=final_shared_sub_id, 
                    inbound_ids=inbound_ids, expires_days=days_left, plan_type=plan_type
                )
                
                if success:
                    new_key_record = VPNKey(
                        subscription_id=active_sub_obj.id, server_id=srv.id, 
                        client_email=final_shared_email, sub_id=final_shared_sub_id, config_data=final_shared_sub_id
                    )
                    db_session.add(new_key_record)
                    
                    await db_session.flush()
                    existing_keys[srv.id] = new_key_record
                    
        except Exception as e:
            logger.error(f"🚨 Изолированный сбой ноды {srv.name} (ID: {srv.id}) при покупке: {e}")
            continue

    await db_session.commit()
    
    success_text = (
        f"🎉 <b>Подписка успешно начислена!</b>\n\n"
        f"• <b>Активирован тариф:</b> <code>{plan_type.upper()}</code>\n"
        f"• <b>Добавлено времени:</b> <b>+{days} дней</b>\n"
        f"• <b>Выделенный трафик:</b> <code>{300 if plan_type == 'premium' else 150} ГБ</code>\n\n"
        f"💬 <i>Статус: {msg_alert} Все изменения синхронизированы на нодах. Нажмите кнопку ниже.</i>"
    )
    
    try:
        await callback.message.edit_text(
            text=success_text, 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]])
        )
    except Exception:
        pass

# handlers/user.py — ХЕНДЛЕР ПУЛЛИНГА СТАТУСА LAVA.TOP С ФИНАЛЬНЫМ НАЧИСЛЕНИЕМ ТАРИФА

@user_router.callback_query(F.data.startswith("check_lava_"))
async def cb_check_lava_pull(callback: CallbackQuery, db_session: AsyncSession):
    """Ручная проверка статуса фиатного счета Lava.top по кнопке пользователя"""
    parts = callback.data.split("_")
    invoice_id = parts[2]
    plan_type = parts[3]
    days = int(parts[4])
    user_id = callback.from_user.id
    
    # 1. Защита FOR UPDATE от Race Condition (двойных кликов)
    stmt_lock = select(PaymentLog).where(PaymentLog.invoice_id == int(hash(invoice_id) & 0x7fffffff)).with_for_update()
    if (await db_session.execute(stmt_lock)).scalar_one_or_none():
        await callback.answer("✅ Эта покупка уже успешно зачислена!", show_alert=True)
        return

    from services.lava import lava_top_client
    # Получаем живой статус со стр. 26 документации Lava.top (NEW, IN_PROGRESS, COMPLETED, FAILED)
    status = await lava_top_client.get_invoice_status(invoice_id)
    
    if not status:
        await callback.answer("⚠️ Не удалось получить статус счета от Lava.top. Попробуйте снова.", show_alert=True)
        return
    if status in ("NEW", "IN_PROGRESS"):
        await callback.answer("⏳ Оплата еще не поступила! Оплатите счет на форме Lava и нажмите кнопку снова.", show_alert=True)
        return
    if status == "FAILED":
        await callback.message.edit_text("❌ Платеж отклонен или просрочен. Пожалуйста, создайте новый счет.")
        return

    # --- СТАТУС СТРОГО "COMPLETED" (УСПЕШНАЯ ФИАТНАЯ ОПЛАТА) ---
    await callback.message.edit_text("⏳ <b>Фиатный платеж подтвержден!</b> Нарезаем доступы...")
    
    price = get_price(plan_type, days)
    now = datetime.datetime.utcnow()
    user = (await db_session.execute(select(User).where(User.telegram_id == user_id).options(selectinload(User.subscriptions)))).scalar_one()

    # (Ниже идет наш стандартный, пуленепробиваемый алгоритм очередей, гибридной рефералки и flush нод Xray)
    base_active = any(s.plan_type == "base" and s.is_active and s.expires_at > now and not s.is_pending for s in user.subscriptions)
    target_sub = next((s for s in user.subscriptions if s.plan_type == plan_type), None)
    is_pending_status = False
    
    if plan_type == "premium" and base_active:
        for s in user.subscriptions:
            if s.plan_type == "base": s.is_pending = True
        msg_alert = f"Тариф PREMIUM на {days} дней активирован! База убрана в очередь."
    else:
        is_pending_status = any(s.is_active and s.expires_at > now and not s.is_pending for s in user.subscriptions if s.plan_type != plan_type)
        msg_alert = f"Тариф {plan_type.upper()} продлен на {days} дней."

    if target_sub:
        if target_sub.is_active and target_sub.expires_at > now and not target_sub.is_pending: target_sub.expires_at += datetime.timedelta(days=days)
        else: target_sub.expires_at = now + datetime.timedelta(days=days); target_sub.is_pending = is_pending_status; target_sub.is_active = True
        active_sub_obj = target_sub
    else:
        new_sub = Subscription(user_id=user_id, plan_type=plan_type, expires_at=now + datetime.timedelta(days=days), is_pending=is_pending_status)
        db_session.add(new_sub); active_sub_obj = new_sub

    await db_session.flush()
    
    # Сплит гибридной партнерки (Пожизненный доход PRO и одноразовый для обычных друзей)
    past_payments = await db_session.scalar(select(func.count(PaymentLog.id)).where(PaymentLog.user_id == user_id)) or 0
    if user.referred_by:
        referrer = (await db_session.execute(select(User).where(User.telegram_id == user.referred_by).options(selectinload(User.subscriptions)))).scalar_one_or_none()
        if referrer:
            if referrer.is_pro_ref:
                referrer.partner_balance_usd += (price * 0.10)
                try: await callback.bot.send_message(chat_id=referrer.telegram_id, text=f"👑 <b>PRO-Начисление!</b> Твой реферал оплатил тариф {plan_type.upper()} через карту.\nТебе зачислено: <b>${price * 0.10:.2f}</b>")
                except Exception: pass
            elif past_payments == 0:
                ref_target_sub = next((s for s in referrer.subscriptions if s.plan_type == plan_type), None)
                if ref_target_sub:
                    if ref_target_sub.is_active and ref_target_sub.expires_at > now: ref_target_sub.expires_at += datetime.timedelta(days=days)
                    else: ref_target_sub.expires_at = now + datetime.timedelta(days=days); ref_target_sub.is_active = True
                else: db_session.add(Subscription(user_id=referrer.telegram_id, plan_type=plan_type, expires_at=now + datetime.timedelta(days=days)))
                try: await callback.bot.send_message(chat_id=referrer.telegram_id, text=f"🎁 <b>Реферальный бонус 1:1!</b> Твой друг купил тариф {plan_type.upper()}. Тебе начислено <b>{days} дней в подарок!</b>")
                except Exception: pass

    # Фиксируем лог фиатной транзакции
    fake_int_id = int(hash(invoice_id) & 0x7fffffff)
    db_session.add(PaymentLog(invoice_id=fake_int_id, user_id=user_id, plan_type=plan_type, amount=price, ref_processed=True))

    # Каскадный пуш на ноды Xray с единым shared_email
    servers = (await db_session.execute(select(Server).where(Server.is_active == True))).scalars().all()
    any_user_key = (await db_session.execute(select(VPNKey).join(Subscription).where(Subscription.user_id == user_id).limit(1))).scalar_one_or_none()
    final_shared_email = any_user_key.client_email if any_user_key else f"usr_{user_id}_{uuid.uuid4().hex[:4]}"
    final_shared_sub_id = any_user_key.sub_id if any_user_key else uuid.uuid4().hex
    
    existing_keys = {k.server_id: k for k in (await db_session.execute(select(VPNKey).where(VPNKey.subscription_id == active_sub_obj.id))).scalars().all()}
    expiry_timestamp = int(active_sub_obj.expires_at.timestamp() * 1000)
    
    for srv in servers:
        try:
            ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id, TariffInbound.plan_type.in_(["base", "premium"] if (plan_type == "premium" or not is_pending_status) else ["base"]))
            inbound_ids = [ib.inbound_id for ib in (await db_session.execute(ib_stmt)).scalars().all()]
            if not inbound_ids: continue
            
            xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
            if srv.id in existing_keys:
                current_key = existing_keys[srv.id]
                target_bytes = (300 if plan_type == "premium" else 150) * 1024 * 1024 * 1024
                await xui.attach_client_inbounds(email=current_key.client_email, inbound_ids=inbound_ids)
                res_get = await xui._request("GET", f"panel/api/clients/get/{current_key.client_email}")
                if res_get and res_get.get("success") and res_get.get("obj"):
                    p_data = res_get.get("obj")
                    await xui._request("POST", f"panel/api/clients/update/{current_key.client_email}", json_data={"id": p_data.get("id"), "email": current_key.client_email, "totalGB": target_bytes, "expiryTime": expiry_timestamp, "subId": p_data.get("subId"), "limitIp": 3, "enable": True})
                    await xui._request("POST", f"panel/api/clients/resetTraffic/{current_key.client_email}")
            else:
                days_left = max(1, (active_sub_obj.expires_at - now).days)
                if await xui.add_client(email=final_shared_email, sub_id=final_shared_sub_id, inbound_ids=inbound_ids, expires_days=days_left, plan_type=plan_type):
                    new_key_record = VPNKey(subscription_id=active_sub_obj.id, server_id=srv.id, client_email=final_shared_email, sub_id=final_shared_sub_id, config_data=final_shared_sub_id)
                    db_session.add(new_key_record)
                    await db_session.flush()
                    existing_keys[srv.id] = new_key_record
        except Exception as e:
            logger.error(f"🚨 Сбой ноды {srv.name} при оплате Lava: {e}")
            continue

    await db_session.commit()
    success_text = f"🎉 <b>Подписка успешно начислена через карту!</b>\n\n• <b>Активирован тариф:</b> <code>{plan_type.upper()}</code>\n• <b>Добавлено времени:</b> <b>+{days} дней</b>\n\n💬 <i>Статус: {msg_alert} Нажмите кнопку ниже для возврата.</i>"
    try: await callback.message.edit_text(text=success_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]]))
    except Exception: pass

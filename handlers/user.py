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

            
            for key in all_user_keys:
                if key.server:
                    srv = key.server
                    clean_domain = urlparse(srv.api_url).hostname
                    is_ip = clean_domain and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", clean_domain)
                    protocol = "http" if is_ip else "https" if srv.api_url.startswith("https") else "http"
                    dynamic_url = f"{protocol}://{clean_domain}:{srv.sub_port}/{srv.sub_path}/{key.sub_id}"
                    profile_text += f"├ 🌍 <b>{key.server.name}:</b> <code>{dynamic_url}</code>\n"
                    
        profile_text += "\n💡 <i>Нажмите на код ссылки выше, чтобы скопировать.</i>"
        
    try: await callback.message.edit_text(text=profile_text, reply_markup=get_profile_keyboard())
    except Exception: pass

@user_router.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(text=f"👋 Добро пожаловать в <b>{config.BRAND_NAME}</b>!", reply_markup=get_main_menu_keyboard())

# handlers/user.py — ЧАСТЬ 3 ИЗ 4

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
async def cb_generate_invoice(callback: CallbackQuery):
    """Выставление счета в CryptoBot"""
    await callback.answer()
    parts = callback.data.split("_")
    plan_type = parts[-2]
    days = int(parts[-1])
    
    price = get_price(plan_type, days)
    asset = config.PAYMENT_CURRENCY
    await callback.message.edit_text("🔄 <i>Формирую счет, пожалуйста, подождите...</i>")
    
    payload = f"{callback.from_user.id}:{plan_type}:{days}"
    description = f"Оплата {config.BRAND_NAME}: {plan_type.upper()} на {days} дней"
    
    invoice = await cryptobot_client.create_invoice(amount=price, asset=asset, description=description, payload=payload)
    
    if invoice and invoice.get("bot_invoice_url"):
        kb = [
            [InlineKeyboardButton(text="💳 Оплатить счет", url=invoice["bot_invoice_url"])],
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_invoice_{invoice['invoice_id']}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_buy")]
        ]
        text = (
            f"🧾 <b>Счет успешно выставлен!</b>\n\n• <b>Тариф:</b> {plan_type.upper()}\n• <b>Срок:</b> {days} дней\n• <b>К оплате:</b> <code>{invoice['amount']}</code> {asset}\n\n"
            f"Оплатите инвойс в @CryptoBot и нажмите «Проверить оплату»."
        )
        await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await callback.message.edit_text(text="❌ Ошибка связи со шлюзом. Попробуйте позже.", reply_markup=get_main_menu_keyboard())

# handlers/user.py — ЧАСТЬ 4.1 ИЗ 5

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

# handlers/user.py — ЧАСТЬ 4.2 ИЗ 5

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
        msg_alert = f"Тариф PREMIUM на {days} дней активирован! База временно убрана в очередь."
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
    
    if past_payments == 0 and user.referred_by:
        referrer = (await db_session.execute(select(User).where(User.telegram_id == user.referred_by).options(selectinload(User.subscriptions)))).scalar_one_or_none()
        if referrer:
            if referrer.is_pro_ref:
                referrer.partner_balance_usd += (price * 0.10)
                try: await callback.bot.send_message(chat_id=referrer.telegram_id, text=f"👑 <b>PRO-Начисление!</b> Твой реферал купил {plan_type.upper()}.\nТебе начислено: <b>${price * 0.10:.2f}</b>")
                except Exception: pass
            else:
                ref_target_sub = next((s for s in referrer.subscriptions if s.plan_type == plan_type), None)
                if ref_target_sub:
                    if ref_target_sub.is_active and ref_target_sub.expires_at > now: ref_target_sub.expires_at += datetime.timedelta(days=days)
                    else: ref_target_sub.expires_at = now + datetime.timedelta(days=days); ref_target_sub.is_active = True
                else: db_session.add(Subscription(user_id=referrer.telegram_id, plan_type=plan_type, expires_at=now + datetime.timedelta(days=days)))
                try: await callback.bot.send_message(chat_id=referrer.telegram_id, text=f"🎁 <b>Реферальный бонус 1:1!</b> Твой друг купил тариф {plan_type.upper()} на {days} дней. Тебе начислено <b>{days} дней такого же тарифа в подарок!</b>")
                except Exception: pass

    db_session.add(PaymentLog(invoice_id=invoice_id, user_id=user_id, plan_type=plan_type, amount=price, ref_processed=True))
    servers = (await db_session.execute(select(Server).where(Server.is_active == True))).scalars().all()
    existing_keys = {k.server_id: k for k in (await db_session.execute(select(VPNKey).join(Subscription).where(Subscription.user_id == user_id))).scalars().all()}
    expiry_timestamp = int(active_sub_obj.expires_at.timestamp() * 1000)
    
    for srv in servers:
        ib_stmt = select(TariffInbound).where(TariffInbound.server_id == srv.id, TariffInbound.plan_type.in_(["base", "premium"] if (plan_type == "premium" or not is_pending_status) else ["base"]))
        inbound_ids = [ib.inbound_id for ib in (await db_session.execute(ib_stmt)).scalars().all()]
        if not inbound_ids: continue
        xui = XUIMultiClient(api_url=srv.api_url, api_token=srv.api_token)
        
        if srv.id not in existing_keys:
            email = f"usr_{user_id}_{uuid.uuid4().hex[:4]}"
            sub_id = uuid.uuid4().hex
            if await xui.add_client(email=email, sub_id=sub_id, inbound_ids=inbound_ids, expires_days=days, plan_type=plan_type):
                db_session.add(VPNKey(subscription_id=active_sub_obj.id, server_id=srv.id, client_email=email, sub_id=sub_id, config_data=sub_id))
        else:
            current_key = existing_keys[srv.id]
            target_bytes = (300 if plan_type == "premium" else 150) * 1024 * 1024 * 1024
            await xui.attach_client_inbounds(email=current_key.client_email, inbound_ids=inbound_ids)
            res_get = await xui._request("GET", f"panel/api/clients/get/{current_key.client_email}")
            if res_get and res_get.get("success") and res_get.get("obj"):
                p_data = res_get.get("obj")
                await xui._request("POST", f"panel/api/clients/update/{current_key.client_email}", json_data={"id": p_data.get("id"), "email": current_key.client_email, "totalGB": target_bytes, "expiryTime": expiry_timestamp, "subId": p_data.get("subId"), "limitIp": 3, "enable": True})
                await xui._request("POST", f"panel/api/clients/resetTraffic/{current_key.client_email}")

    await db_session.commit()
    success_text = f"🎉 <b>Подписка успешно начислена!</b>\n\n• <b>Активирован тариф:</b> <code>{plan_type.upper()}</code>\n• <b>Добавлено времени:</b> <b>+{days} дней</b>\n• <b>Трафик:</b> <code>{300 if plan_type == 'premium' else 150} ГБ</code>\n\n💬 <i>Статус: {msg_alert} Все изменения сохранены. Нажмите кнопку ниже для перехода в меню.</i>"
    try: await callback.message.edit_text(text=success_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]]))
    except Exception: pass


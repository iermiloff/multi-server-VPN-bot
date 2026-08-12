# handlers/user.py — ЧАСТЬ 1
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

from bot.config import config
from bot.database.models import User, PartnerChannel, Subscription, VPNKey, Server, TariffInbound, SubscriptionType
from bot.services.xui import XUIMultiClient

logger = logging.getLogger(__name__)
user_router = Router()

async def check_main_channel_sub(bot: Bot, session: AsyncSession, user_id: int) -> bool:
    """Проверяет обязательную подписку на главный канал (is_required=True)"""
    stmt = select(PartnerChannel).where(PartnerChannel.is_required == True)
    res = await session.execute(stmt)
    main_channel = res.scalar_one_or_none()
    
    if not main_channel:
        return True # Если админ еще не настроил главный канал, пускаем всех
        
    try:
        member = await bot.get_chat_member(chat_id=main_channel.channel_id, user_id=user_id)
        if member.status not in ["left", "kicked"]:
            return True
    except Exception as e:
        logger.error(f"Ошибка проверки главной подписки: {e}")
        return True
        
    return False

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Генерация кнопок главного меню"""
    keyboard = [
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
        [InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu_buy")],
        [InlineKeyboardButton(text="🎁 Месяц от партнеров", callback_data="menu_partner_gift")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@user_router.message(CommandStart())
async def cmd_start(message: Message, db_session: AsyncSession, bot: Bot):
    """Регистрация пользователя и приветствие"""
    # 1. Проверяем или создаем пользователя в БД
    stmt = select(User).where(User.telegram_id == message.from_user.id)
    res = await db_session.execute(stmt)
    user = res.scalar_one_or_none()
    
    if not user:
        user = User(telegram_id=message.from_user.id, username=message.from_user.username)
        db_session.add(user)
        await db_session.commit()

    # 2. Проверяем подписку на основной канал
    if not await check_main_channel_sub(bot, db_session, message.from_user.id):
        # Находим инвайт-ссылку главного канала
        stmt = select(PartnerChannel).where(PartnerChannel.is_required == True)
        ch_res = await db_session.execute(stmt)
        channel = ch_res.scalar_one()
        
        kb = [[InlineKeyboardButton(text="📢 Подписаться", url=channel.invite_link)],
              [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub_again")]]
        
        await message.answer(
            text="❌ <b>Доступ временно ограничен!</b>\n\nДля использования нашего VPN подпишитесь на официальный канал техподдержки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
        return

    await message.answer(
        text=f"👋 Добро пожаловать в <b>{config.BRAND_NAME}</b>!\n\nИспользуйте меню ниже для управления вашим доступом:",
        reply_markup=get_main_menu_keyboard()
    )

@user_router.callback_query(F.data == "check_sub_again")
async def cb_check_sub_again(callback: CallbackQuery, db_session: AsyncSession, bot: Bot):
    """Повторный триггер проверки при клике на кнопку"""
    if await check_main_channel_sub(bot, db_session, callback.from_user.id):
        await callback.answer("✅ Подписка подтверждена!")
        await callback.message.delete()
        # Вызываем стартовое приветствие
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        res = await db_session.execute(stmt)
        user = res.scalar_one()
        await callback.message.answer(
            text="🎉 Отлично! Доступ открыт. Выберите нужный раздел:",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await callback.answer("❌ Вы всё еще не подписались на канал техподдержки!", show_alert=True)

# handlers/user.py — ЧАСТЬ 2 (ПОЛНАЯ)
from bot.keyboards.user import get_main_menu_keyboard

def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Кнопки внутри личного кабинета"""
    keyboard = [
        [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="menu_profile")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@user_router.callback_query(F.data == "menu_profile")
async def cb_menu_profile(callback: CallbackQuery, db_session: AsyncSession):
    """Вывод личного кабинета со ссылками на все активные ноды"""
    await callback.answer()
    now = datetime.datetime.utcnow()
    
    # Загружаем юзера, его подписки, ключи и данные серверов за один запрос
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
        
    # Фильтруем только реально действующие по времени подписки
    active_subs = [s for s in user.subscriptions if s.is_active and s.expires_at > now]
    
    profile_text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"• Твой Telegram ID: <code>{user.telegram_id}</code>\n"
    )
    
    if not active_subs:
        profile_text += (
            "• Статус подписки: ❌ <b>Не активна</b>\n\n"
            "У вас пока нет активных подключений. Купите доступ или "
            "активируйте бонус от партнеров в главном меню."
        )
    else:
        profile_text += "• Статус подписки: ✅ <b>Активна</b>\n\n"
        profile_text += "🔗 <b>Ваши персональные ссылки подписки:</b>\n"
        
        # Перебираем подписки и выводим ссылки из панелей 3x-ui по каждой ноде
        for sub in active_subs:
            expires_str = sub.expires_at.strftime("%d.%m.%Y %H:%M")
            profile_text += f"\nТариф: <b>{sub.plan_type.upper()}</b> (До: <code>{expires_str}</code>)\n"
            
            if not sub.keys:
                profile_text += "<i>⌛ Нарезаем доступ на серверах, обновите профиль через минуту...</i>\n"
            else:
                for key in sub.keys:
                    # Извлекаем название сервера из связи, если она активна
                    server_name = key.server.name if key.server else "Сервер"
                    profile_text += f"├ 🌍 <b>{server_name}:</b> <code>{key.config_data}</code>\n"
                    
        profile_text += (
            "\n💡 <i>Нажмите на код ссылки выше, чтобы мгновенно скопировать её. "
            "Затем импортируйте её в ваше приложение (v2rayNG, FoXray, Streisand) "
            "как 'Subscription' или 'Удаленный ресурс'.</i>"
        )
        
    try:
        await callback.message.edit_text(text=profile_text, reply_markup=get_profile_keyboard())
    except Exception:
        # Если текст сообщения не изменился, просто гасим часики в TG
        pass

@user_router.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: CallbackQuery):
    """Быстрый возврат на главный экран"""
    await callback.answer()
    await callback.message.edit_text(
        text=f"👋 Добро пожаловать в <b>{config.BRAND_NAME}</b>!\n\nИспользуйте меню ниже для управления вашим доступом:",
        reply_markup=get_main_menu_keyboard()
    )

# handlers/user.py — ЧАСТЬ 3 (ПОЛОВИНА 3.1)

def get_periods_keyboard(plan_type: str) -> InlineKeyboardMarkup:
    """Выбор длительности подписки (скидки заложены визуально)"""
    keyboard = [
        [
            InlineKeyboardButton(text="⏳ 1 Месяц", callback_data=f"buy_time_{plan_type}_30"),
            InlineKeyboardButton(text="⏳ 3 Месяца (-10%)", callback_data=f"buy_time_{plan_type}_90")
        ],
        [InlineKeyboardButton(text="⏳ 6 Месяцев (-20%)", callback_data=f"buy_time_{plan_type}_180")],
        [InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="menu_buy")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@user_router.callback_query(F.data == "menu_buy")
async def cb_menu_buy(callback: CallbackQuery):
    """Экран выбора уровня доступа"""
    await callback.answer()
    cur = config.PAYMENT_CURRENCY
    
    text = (
        f"💎 <b>Покупка подписки {config.BRAND_NAME}</b>\n\n"
        f"Выберите желаемый тарифный план:\n\n"
        f"🚀 <b>БАЗОВЫЙ (BASE):</b>\n"
        f"• Доступ к ультрабыстрым протоколам обхода блокировок\n"
        f"• Автоматическая синхронизация со всеми нодами сети\n"
        f"• Цена: от <code>{config.PRICE_BASE_1_MONTH}</code> {cur} / мес.\n"
    )
    
    kb = [
        [InlineKeyboardButton(text="🔥 Выбрать тариф BASE", callback_data="buy_plan_base")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main")]
    ]
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@user_router.callback_query(F.data.startswith("buy_plan_"))
async def cb_buy_plan(callback: CallbackQuery):
    """Экран выбора периода подписки для выбранного тарифа"""
    await callback.answer()
    plan_type = callback.data.split("_")[2] # base
    
    text = (
        f"📅 <b>Выберите срок действия подписки:</b>\n\n"
        f"Чем длиннее период вы выбираете, тем выгоднее итоговая стоимость месяца!"
    )
    await callback.message.edit_text(text=text, reply_markup=get_periods_keyboard(plan_type))

# handlers/user.py — ЧАСТЬ 3 (ПОЛОВИНА 3.2)
from bot.services.cryptobot import cryptobot_client # Импорт вашего клиента Crypto Pay

# Словарь для локального расчета стоимости на основе конфига
def get_price(plan_type: str, days: int) -> float:
    prices = {
        "base": {30: config.PRICE_BASE_1_MONTH, 
                 90: config.PRICE_BASE_3_MONTHS, 
                 180: config.PRICE_BASE_6_MONTHS}
    }
    return prices.get(plan_type, {}).get(days, 0.0)

@user_router.callback_query(F.data.startswith("buy_time_"))
async def cb_generate_invoice(callback: CallbackQuery):
    """Генерация счета в CryptoBot для оплаты подписки"""
    await callback.answer()
    parts = callback.data.split("_")
    plan_type = parts[2]
    days = int(parts[3])
    
    price = get_price(plan_type, days)
    asset = config.PAYMENT_CURRENCY
    
    await callback.message.edit_text("⏳ <i>Формирую счет на оплату, пожалуйста, подождите...</i>")
    
    # Payload для распознавания платежа при вебхуке/проверке
    payload = f"{callback.from_user.id}:{plan_type}:{days}"
    description = f"Оплата {config.BRAND_NAME}: тариф {plan_type.upper()} на {days} дней"
    
    # Создаем инвойс через API Crypto Pay
    invoice = await cryptobot_client.create_invoice(
        amount=price,
        asset=asset,
        description=description,
        payload=payload
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
            f"Нажмите кнопку ниже, чтобы перейти в @CryptoBot и совершить платеж. "
            f"После транзакции нажмите «Проверить оплату»."
        )
        await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await callback.message.edit_text(
            text="❌ Не удалось связаться с CryptoBot. Пожалуйста, попробуйте позже.",
            reply_markup=get_main_menu_keyboard()
        )

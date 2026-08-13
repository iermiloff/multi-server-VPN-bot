import logging
import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database.models import User, Subscription, SubscriptionType
from handlers.user import get_main_menu_keyboard # Подтягиваем вашу клавиатуру назад

logger = logging.getLogger(__name__)
referral_router = Router()

# Группа FSM состояний для ввода кошелька
class UserRefStates(StatesGroup):
    wait_for_wallet = State()

@referral_router.callback_query(F.data == "menu_referral")
async def cb_menu_referral(callback: CallbackQuery, db_session: AsyncSession):
    """Отрисовка реферального меню в зависимости от статуса (User или PRO)"""
    await callback.answer()
    user_id = callback.from_user.id
    
    stmt = select(User).where(User.telegram_id == user_id)
    res = await db_session.execute(stmt)
    user = res.scalar_one()
    
    # Считаем количество рефералов
    ref_count = await db_session.scalar(
        select(func.count(User.telegram_id)).where(User.referred_by == user_id)
    ) or 0
    
    bot_username = (await callback.bot.get_me()).username
    ref_link = f"https://t.me{bot_username}?start=ref{user_id}"
    
    kb = []
    
    # --- ВАРИАНТ А: У ЮЗЕРА PRO-СТАТУС (модель CPA 10%) ---
    if user.is_pro_ref:
        wallet_str = user.crypto_wallet if user.crypto_wallet else "❌ Не привязан"
        text = (
            f"👑 <b>PRO-Реферальный Кабинет</b>\n\n"
            f"Вы являетесь официальным информационным партнером {config.BRAND_NAME}!\n\n"
            f"📊 <b>Твоя статистика заработка:</b>\n"
            f"├ Приглашено рефералов: <code>{ref_count} чел.</code>\n"
            f"├ Доступный баланс к выплате: <b>${user.partner_balance_usd or 0.0:.2f}</b>\n"
            f"└ TON/GRAM кошелек: <code>{wallet_str}</code>\n\n"
            f"📢 <b>Твоя персональная CPA-ссылка:</b>\n<code>{ref_link}</code>\n\n"
            f"💡 <i>Вам начисляется <b>10%</b> от каждой покупки ваших рефералов живыми деньгами! "
            f"Выплаты производятся 1-го числа каждого месяца на ваш кошелек.</i>"
        )
        kb.append([InlineKeyboardButton(text="💳 Изменить / Привязать кошелек", callback_data="ref_set_wallet")])
        
    # --- ВАРИАНТ Б: ОБЫЧНАЯ РЕФЕРАЛКА (модель 1:1 по дням) ---
    else:
        text = (
            f"👥 <b>Реферальная программа Overlord VPN</b>\n\n"
            f"Приглашай своих друзей и пользуйся премиальным VPN абсолютно бесплатно!\n\n"
            f"📊 <b>Твои успехи:</b>\n"
            f"└ Приглашено друзей: <code>{ref_count} чел.</code>\n"
            f"🔗 <b>Твоя пригласительная ссылка:</b>\n<code>{ref_link}</code>\n\n"
            f"🎁 <b>Как это работает:</b>\n"
            f"Отправь ссылку другу. Когда твой друг совершит <b>первую покупку любого тарифа</b>, "
            f"вам обоим начислится <b>точно такое же количество дней</b> в подарок! "
            f"Привел друга на год Премиума — получил год Премиума бесплатно!"
        )
        
    kb.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_to_main")])
    await callback.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# handlers/referral.py — ПОЛНЫЙ МОДУЛЬ РЕФЕРАЛЬНОЙ СИСТЕМЫ (ЧАСТЬ 2)

@referral_router.callback_query(F.data == "ref_set_wallet")
async def cb_ref_set_wallet(callback: CallbackQuery, state: FSMContext):
    """Запуск FSM процесса привязки кошелька"""
    await callback.answer()
    await state.set_state(UserRefStates.wait_for_wallet)
    await callback.message.edit_text(
        text="💎 Введите адрес вашего **TON (GRAM) кошелька** для ежемесячных выплат:\n\n*Принимаются адреса Tonkeeper, MyTonWallet, CryptoBot и др.*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_referral")]])
    )

@referral_router.message(UserRefStates.wait_for_wallet)
async def msg_ref_save_wallet(message: Message, state: FSMContext, db_session: AsyncSession):
    """Сохранение кошелька партнера в СУБД"""
    if message.text.startswith("/"):
        await state.clear()
        await message.answer("❌ Ввод отменен.")
        return

    wallet_address = message.text.strip()
    
    if len(wallet_address) < 20:
        await message.answer("❌ Кажется, это некорректный адрес кошелька. Попробуйте еще раз:")
        return
        
    stmt = select(User).where(User.telegram_id == message.from_user.id)
    res = await db_session.execute(stmt)
    user = res.scalar_one()
    
    user.crypto_wallet = wallet_address
    await db_session.commit()
    await state.clear()
    
    # Перерисовываем меню
    fake_callback = CallbackQuery(
        id="0", from_user=message.from_user, chat_instance="0",
        message=message, data="menu_referral"
    )
    await cb_menu_referral(fake_callback, db_session)

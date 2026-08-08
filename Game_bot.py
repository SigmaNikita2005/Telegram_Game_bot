import asyncio
import logging
import json
import os
import random
import re
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject

# Включаем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8615120486:AAERbI3-3pM-I2f20V63hKa0ypZN6o0UAsM"
OWNER_ID = 8260588511  # Твой ID владельца
LOG_FILE = "bot_log.txt"
DB_FILE = "users_db.json"  # Одна база данных для всех игроков

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальная переменная для игры "Угадай слово"
guess_game = {"word": None, "chat_id": None, "author_id": None}

# Универсальная кнопка возврата в меню
cancel_kb = types.ReplyKeyboardMarkup(
    keyboard=[[types.KeyboardButton(text="В главное меню 🔙")]],
    resize_keyboard=True
)

# --- СОСТОЯНИЯ ДЛЯ FSM ---
class AdminGiveState(StatesGroup):
    currency = State()
    amount = State()
    target_user = State()

class ExchangeState(StatesGroup):
    currency = State()
    amount = State()

class LoanState(StatesGroup):
    currency = State()
    amount = State()

class RepayState(StatesGroup):
    amount = State()

class AdminForgiveState(StatesGroup):
    target_user = State()

class AdminVipState(StatesGroup):
    target = State()

class CubiciState(StatesGroup):
    currency = State()
    amount = State()
    number = State()

class GameBetState(StatesGroup):
    game = State()
    currency = State()
    amount = State()

class MinesState(StatesGroup):
    currency = State()
    amount = State()
    mines_count = State()

class RocketState(StatesGroup):
    currency = State()
    amount = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

class RouletteState(StatesGroup):
    currency = State()
    amount = State()
    bullet = State()

class RobState(StatesGroup):
    target_id = State()
    currency = State()
    amount = State()

# НОВЫЕ СОСТОЯНИЯ: ДОНАТ И ПРЕДЛОЖКА
class DonatState(StatesGroup):
    amount = State()

class PredlojkaState(StatesGroup):
    idea = State()

class PredlojkaReplyState(StatesGroup):
    text = State()
    target_id = State()

class PredlojkaUserReplyState(StatesGroup):
    text = State()
    target_id = State()

# --- БАЗА ДАННЫХ (JSON) ---
def load_db():
    if not os.path.exists(DB_FILE):
        return {"users": {}, "usernames": {}, "settings": {"rules": None, "channels": []}, "chats": {}}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        db = json.load(f)
        if "settings" not in db:
            db["settings"] = {"rules": None, "channels": []}
        if "channels" not in db["settings"]:
            db["settings"]["channels"] = []
        if "chats" not in db:
            db["chats"] = {}
        return db

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def init_user(db, user_id, username=None):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "coins": 100,
            "gold": 0,
            "diamonds": 0,
            "last_farm": None,
            "loan_amount": 0,
            "loan_currency": None,
            "loan_deadline": None,
            "spouse": None,
            "vip_until": None,
            "referrals": 0
        }
    else:
        if "loan_amount" not in db["users"][uid]:
            db["users"][uid].update({"loan_amount": 0, "loan_currency": None, "loan_deadline": None, "spouse": None})
        if "vip_until" not in db["users"][uid]: db["users"][uid]["vip_until"] = None
        if "referrals" not in db["users"][uid]: db["users"][uid]["referrals"] = 0
            
    if username:
        db["usernames"][username.lower().replace("@", "")] = uid

def is_vip(user_data):
    if not user_data.get("vip_until"):
        return False
    if user_data["vip_until"] == "forever":
        return True
    vip_date = datetime.fromisoformat(user_data["vip_until"])
    if datetime.now() < vip_date:
        return True
    return False

async def check_loans_loop():
    while True:
        await asyncio.sleep(3600)
        db = load_db()
        now = datetime.now()
        changed = False
        for uid, data in db["users"].items():
            if data.get("loan_deadline") and data.get("loan_amount", 0) > 0:
                deadline = datetime.fromisoformat(data["loan_deadline"])
                if now > deadline:
                    data["coins"] = 0
                    data["gold"] = 0
                    data["diamonds"] = 0
                    data["loan_amount"] = 0
                    data["loan_currency"] = None
                    data["loan_deadline"] = None
                    changed = True
                    try:
                        await bot.send_message(int(uid), "❌ Вы не погасили кредит вовремя. Ваш баланс полностью обнулен.")
                    except: pass
        if changed:
            save_db(db)

# =====================================================================
# --- MIDDLEWARE ДЛЯ ОБЯЗАТЕЛЬНОЙ ПОДПИСКИ (FORCE SUB) ---
# =====================================================================
class CheckSubMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
            
        if user.id == OWNER_ID:
            return await handler(event, data)
            
        is_command = getattr(event, "text", "") and event.text.startswith("/")
        is_callback = isinstance(event, types.CallbackQuery) and not event.data.startswith("check_sub")
        
        if not (is_command or is_callback):
            return await handler(event, data)
            
        db = load_db()
        channels = db["settings"].get("channels", [])
        if not channels:
            return await handler(event, data)
            
        not_subbed = False
        for ch in channels:
            try:
                member = await bot.get_chat_member(chat_id=ch, user_id=user.id)
                if member.status in ['left', 'kicked', 'banned']:
                    not_subbed = True
                    break
            except Exception:
                pass 
                
        if not_subbed:
            kb = []
            for i, ch in enumerate(channels):
                url_ch = ch.replace("@", "")
                kb.append([types.InlineKeyboardButton(text=f"Канал {i+1}", url=f"https://t.me/{url_ch}")])
            kb.append([types.InlineKeyboardButton(text="Проверить", callback_data="check_sub_status")])
            
            markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
            text = "вы не подписались на канал нужно подписаться"
            
            if isinstance(event, types.Message):
                await event.answer(text, reply_markup=markup)
            elif isinstance(event, types.CallbackQuery):
                await event.message.answer(text, reply_markup=markup)
                await event.answer()
            return 
            
        return await handler(event, data)

dp.message.middleware(CheckSubMiddleware())
dp.callback_query.middleware(CheckSubMiddleware())

@dp.callback_query(F.data == "check_sub_status")
async def cb_check_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    db = load_db()
    channels = db["settings"].get("channels", [])
    not_subbed = False
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ['left', 'kicked', 'banned']:
                not_subbed = True
                break
        except Exception:
            pass
            
    if not_subbed:
        await callback.answer("проверьте ещё раз и все и каналы какие там", show_alert=True)
    else:
        await callback.message.edit_text("вы подписались на каналы вы можете пользоваться ботом и сделай так что бы команды выдавало")

# =====================================================================
# --- ФУНКЦИИ ВВОДА (ОБМЕН, АДМИНКА И КРЕДИТЫ И ГЛАВНОЕ МЕНЮ) ---
# =====================================================================

@dp.message(F.text == "В главное меню 🔙")
async def cancel_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.reply("Вы вернулись в главное меню.", reply_markup=types.ReplyKeyboardRemove())

@dp.callback_query(F.data.startswith("main_menu_"))
async def cb_main_menu(callback: types.CallbackQuery, state: FSMContext):
    uid = int(callback.data.split("_")[2])
    if callback.from_user.id != uid:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
    await state.clear()
    await callback.message.edit_text("Вы вернулись в главное меню.")

@dp.message(ExchangeState.currency, ~F.text.startswith('/'))
async def exchange_currency(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    choice = message.text.lower().strip()
    if choice not in ["алмазы", "золото"]:
        await message.reply("❌ Неверный выбор. Введите `алмазы` или `золото`:", reply_markup=cancel_kb)
        return
    await state.update_data(target=choice)
    await state.set_state(ExchangeState.amount)
    await message.reply(f"Введите количество **{choice}**, которое хотите получить:", reply_markup=cancel_kb)

@dp.message(ExchangeState.amount, ~F.text.startswith('/'))
async def exchange_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите корректное число больше нуля:", reply_markup=cancel_kb)

    data = await state.get_data()
    target = data["target"]
    rate = 20 if target == "алмазы" else 30
    required_coins = amount * rate

    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    if db["users"][uid]["coins"] < required_coins:
        await message.reply(f"❌ Недостаточно коинов! Нужно {required_coins} коинов.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    await state.update_data(amount=amount, cost=required_coins)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Подтвердить обмен", callback_data=f"confirm_ex_{message.from_user.id}")],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{message.from_user.id}")]
    ])
    await message.reply(f"📊 Вы хотите получить {amount} {target} за {required_coins} коинов. Подтвердите:", reply_markup=kb)

@dp.callback_query(F.data.startswith("confirm_ex_"))
async def confirm_exchange(callback: types.CallbackQuery, state: FSMContext):
    uid_check = int(callback.data.split("_")[2])
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)

    data = await state.get_data()
    if not data:
        await callback.answer("Сессия истекла.", show_alert=True)
        return

    target = data["target"]
    amount = data["amount"]
    cost = data["cost"]
    
    db = load_db()
    uid = str(callback.from_user.id)
    
    if db["users"][uid]["coins"] < cost:
        await callback.answer("Ошибка: недостаточно коинов!", show_alert=True)
        await state.clear()
        return

    db["users"][uid]["coins"] -= cost
    if target == "алмазы": db["users"][uid]["diamonds"] += amount
    else: db["users"][uid]["gold"] += amount
        
    save_db(db)
    await callback.message.edit_text(f"💳 Списано {cost} коинов. Получено {amount} {target}! 🎉")
    await state.clear()


@dp.message(Command("give"))
async def cmd_give(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.set_state(AdminGiveState.currency)
    await message.reply("Какую валюту выдать? (`алмазы`, `золото`, `коины`):", reply_markup=cancel_kb)

@dp.message(AdminGiveState.currency, ~F.text.startswith('/'))
async def admin_choose_curr(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if message.from_user.id != OWNER_ID: return
    choice = message.text.lower().strip()
    if choice in ["алмазы", "алмаз", "diamonds"]: curr = "diamonds"; name = "алмазов"
    elif choice in ["золото", "золот", "gold"]: curr = "gold"; name = "золота"
    elif choice in ["коины", "коин", "coins"]: curr = "coins"; name = "коинов"
    else:
        await message.reply("❌ Выберите корректную валюту (`алмазы`, `золото`, `коины`):", reply_markup=cancel_kb)
        return
    await state.update_data(currency=curr, currency_name=name)
    await state.set_state(AdminGiveState.amount)
    await message.reply(f"Введите количество валюты ({choice}):", reply_markup=cancel_kb)

@dp.message(AdminGiveState.amount, ~F.text.startswith('/'))
async def admin_enter_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if message.from_user.id != OWNER_ID: return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите валидное число больше нуля:", reply_markup=cancel_kb)
    
    await state.update_data(amount=amount)
    await state.set_state(AdminGiveState.target_user)
    await message.reply("Введите юзернейм (например, `@nickname`) или Telegram ID игрока:", reply_markup=cancel_kb)

@dp.message(AdminGiveState.target_user, ~F.text.startswith('/'))
async def admin_process_give(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if message.from_user.id != OWNER_ID: return
    target_input = message.text.strip()
    data = await state.get_data()
    curr = data["currency"]
    curr_name = data["currency_name"]
    amount = data["amount"]
    
    db = load_db()
    target_id = None

    if target_input.isdigit(): target_id = target_input
    else:
        clean_username = target_input.replace("@", "").lower()
        target_id = db["usernames"].get(clean_username)

    if not target_id:
        await message.reply("❌ Пользователь не найден в базе данных.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    init_user(db, target_id)
    db["users"][str(target_id)][curr] += amount
    save_db(db)

    try:
        await bot.send_message(chat_id=int(target_id), text=f"Вам выдал администратор {curr_name} {amount}")
    except: pass

    await message.reply(f"Выдали {amount} {curr_name} игроку {target_input}", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


@dp.message(LoanState.currency, ~F.text.startswith('/'))
async def loan_currency(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    choice = message.text.lower().strip()
    if choice in ["алмазы", "алмаз"]: curr = "diamonds"; name = "алмазов"
    elif choice in ["золото", "золот"]: curr = "gold"; name = "золота"
    elif choice in ["коины", "коин"]: curr = "coins"; name = "коинов"
    else:
        await message.reply("❌ Выберите корректную валюту (алмазы, золото, коины):", reply_markup=cancel_kb)
        return
    await state.update_data(currency=curr, currency_name=name)
    await state.set_state(LoanState.amount)
    await message.reply("Какое количество хотите взять (введите число):", reply_markup=cancel_kb)

@dp.message(LoanState.amount, ~F.text.startswith('/'))
async def loan_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите корректное число больше нуля:", reply_markup=cancel_kb)
        
    data = await state.get_data()
    curr = data["currency"]
    curr_name = data["currency_name"]
    
    db = load_db()
    uid = str(message.from_user.id)
    
    db["users"][uid]["loan_amount"] = amount
    db["users"][uid]["loan_currency"] = curr_name
    db["users"][uid]["loan_deadline"] = (datetime.now() + timedelta(days=7)).isoformat()
    db["users"][uid][curr] += amount
    save_db(db)
    
    await message.reply(f"🏦 Вы взяли в кредит {amount} {curr_name}. Отдать нужно через неделю!", reply_markup=types.ReplyKeyboardRemove())
    try: await bot.send_message(OWNER_ID, f"Игрок {message.from_user.id} взял кредит: {amount} {curr_name}")
    except: pass
    await state.clear()

@dp.message(RepayState.amount, ~F.text.startswith('/'))
async def process_repay(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите корректную сумму больше нуля:", reply_markup=cancel_kb)
        
    uid = str(message.from_user.id)
    db = load_db()
    
    debt = db["users"][uid].get("loan_amount", 0)
    curr_name = db["users"][uid].get("loan_currency", "коинов")
    
    if "алмаз" in curr_name: curr = "diamonds"
    elif "золот" in curr_name: curr = "gold"
    else: curr = "coins"
    
    if amount > debt: amount = debt
    if db["users"][uid][curr] < amount:
        await message.reply("❌ У вас недостаточно средств для погашения этой суммы.", reply_markup=types.ReplyKeyboardRemove())
        return
        
    db["users"][uid][curr] -= amount
    db["users"][uid]["loan_amount"] -= amount
    
    if db["users"][uid]["loan_amount"] <= 0:
        db["users"][uid]["loan_amount"] = 0
        db["users"][uid]["loan_deadline"] = None
        await message.reply("🎉 Вы успешно погасили свой долг!", reply_markup=types.ReplyKeyboardRemove())
    else:
        await message.reply(f"✅ Долг частично погашен. Осталось отдать: {db['users'][uid]['loan_amount']} {curr_name}.", reply_markup=types.ReplyKeyboardRemove())
        
    save_db(db)
    await state.clear()

@dp.message(AdminForgiveState.target_user, ~F.text.startswith('/'))
async def process_admin_forgive(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if message.from_user.id != OWNER_ID: return
    target_input = message.text.strip()
    db = load_db()
    target_id = target_input if target_input.isdigit() else db["usernames"].get(target_input.replace("@", "").lower())

    if not target_id or str(target_id) not in db["users"]:
        await message.reply("❌ Пользователь не найден.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    uid = str(target_id)
    db["users"][uid]["loan_amount"] = 0
    db["users"][uid]["loan_deadline"] = None
    save_db(db)

    await message.reply(f"✅ Вы простили долг игроку {target_input}.", reply_markup=types.ReplyKeyboardRemove())
    try: await bot.send_message(int(target_id), "Администратор простил вам долг! Вы свободны.")
    except: pass
    await state.clear()


# =====================================================================
# --- ВСЕ КОМАНДЫ ---
# =====================================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    db = load_db()
    
    args = message.text.split()
    referrer_id = args[1] if len(args) > 1 else None
    uid = str(message.from_user.id)
    
    if uid not in db["users"] and referrer_id and referrer_id != uid:
        if referrer_id in db["users"]:
            db["users"][referrer_id]["referrals"] = db["users"][referrer_id].get("referrals", 0) + 1
            db["users"][referrer_id]["coins"] += 50
            try: await bot.send_message(int(referrer_id), "🎉 По вашей ссылке зарегистрировался игрок! +50 коинов.")
            except: pass

    init_user(db, message.from_user.id, message.from_user.username)
    save_db(db)
    
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={uid}"
    
    await message.reply(
        "🎮 **Привет! Добро пожаловать в игровой бот!**\n\n"
        "📜 **Команды:**\n"
        "🔹 `/profil` — Твой баланс и статус\n"
        "🔹 `/farm` — Собрать бонус коинов\n"
        "🔹 `/exchange` — Обменять коины\n"
        "🔹 `/credit` и `/dolg` — Банк\n"
        "🔹 `/vip` — Купить VIP\n"
        "🔹 Игры: `/cubici`, `/casino`, `/darts`, `/football`, `/basketball`, `/mines`, `/rocket`, `/roulette`\n"
        "🔹 Чат: `/rules`, `/mut`, `/unmute`, `/ban`, `/unban`, `/rob`\n"
        "🔹 Прочее: `/upgrade`, `/donat`, `/predlojka`\n\n"
        "👑 Админ: `/adminpanel`\n\n"
        f"🔗 Реф. ссылка:\n`{ref_link}`", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(Command("exchange"))
async def cmd_exchange(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(ExchangeState.currency)
    await message.reply("Какую валюту хотите получить? Напишите `алмазы` или `золото`:", reply_markup=cancel_kb)

@dp.message(Command("credit"))
async def cmd_credit(message: types.Message, state: FSMContext):
    await state.clear()
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    if db["users"][uid].get("loan_amount", 0) > 0:
        await message.reply("❌ У вас уже есть активный кредит. Погасите его через /dolg.")
        return
        
    await state.set_state(LoanState.currency)
    await message.reply("В какой валюте хотите взять кредит? Напишите `алмазы`, `золото` или `коины`:", reply_markup=cancel_kb)

@dp.message(Command("dolg"))
async def cmd_dolg(message: types.Message, state: FSMContext):
    await state.clear()
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    debt = db["users"][uid].get("loan_amount", 0)
    curr = db["users"][uid].get("loan_currency", "коинов")
    
    if debt <= 0:
        await message.reply("✅ У вас нет активных долгов!")
        return
        
    await state.set_state(RepayState.amount)
    await message.reply(f"🏦 Ваш текущий долг: {debt} {curr}.\nВведите сумму, которую хотите погасить сейчас:", reply_markup=cancel_kb)


# --- НОВЫЕ ФУНКЦИИ (АДМИН ПАНЕЛЬ, РАССЫЛКА, КАНАЛЫ) ---

@dp.message(Command("adminpanel"))
async def cmd_adminpanel(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.reply("❌ У вас нет доступа к админ-панели.")
        return
    text = (
        "👑 **Админ-Панель:**\n\n"
        "🛠 `/give` — Выдать валюту игроку\n"
        "🛠 `/forgive` — Простить долг\n"
        "🛠 `/datbvip` — Выдать VIP\n"
        "🛠 `/rassulka` — Массовая рассылка\n"
        "🛠 `+канал @юзернейм` — Добавить обязательную подписку\n"
        "🛠 `+правила [текст]` — Установить правила чата\n"
        "🛠 `+приветствие [текст]` — Установить приветствие для этого чата\n"
    )
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("rassulka"))
async def cmd_rassulka(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.set_state(BroadcastState.waiting_for_message)
    await message.reply("введите рассылку что вы хотите")

@dp.message(BroadcastState.waiting_for_message)
async def process_rassulka(message: types.Message, state: FSMContext):
    db = load_db()
    users = db["users"].keys()
    for uid in users:
        try:
            await message.send_copy(chat_id=int(uid))
            await asyncio.sleep(0.05)  # Избегаем лимитов Telegram
        except Exception:
            pass  # Если бот заблокирован, игнорируем ошибку и шлем дальше
    await message.reply("успешно 100%")
    await state.clear()

@dp.message(F.text.lower().startswith("+канал "))
async def cmd_add_channel(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    channel = message.text.split(" ", 1)[1].strip()
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=bot.id)
        if member.status in ['administrator', 'creator']:
            db = load_db()
            if channel not in db["settings"]["channels"]:
                db["settings"]["channels"].append(channel)
                save_db(db)
            await message.reply("вы назначили бота админом! ")
        else:
            await message.reply("вы не назначили админа ботом")
    except Exception:
        await message.reply("вы не назначили админа ботом")

@dp.message(Command("rules"))
async def cmd_rules(message: types.Message):
    db = load_db()
    chat_id_str = str(message.chat.id)
    rules = db.get("chats", {}).get(chat_id_str, {}).get("rules")
    if not rules:
        await message.reply("Нету правил.")
    else:
        await message.reply(f"📜 **Правила:**\n\n{rules}")

@dp.message(F.text.lower().startswith("+правила "))
async def set_rules(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    new_rules = message.text.split(" ", 1)[1]
    db = load_db()
    chat_id_str = str(message.chat.id)
    if "chats" not in db:
        db["chats"] = {}
    if chat_id_str not in db["chats"]:
        db["chats"][chat_id_str] = {}
    db["chats"][chat_id_str]["rules"] = new_rules
    save_db(db)
    await message.reply("Правила обновились вы можете написать команду /rules что бы их посмотреть")

@dp.message(F.text.lower().startswith("+приветствие "))
async def set_welcome(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    new_welcome = message.text.split(" ", 1)[1]
    db = load_db()
    chat_id_str = str(message.chat.id)
    if "chats" not in db:
        db["chats"] = {}
    if chat_id_str not in db["chats"]:
        db["chats"][chat_id_str] = {}
    db["chats"][chat_id_str]["welcome"] = new_welcome
    save_db(db)
    await message.reply("Вы добавили приветствие для этого чата, теперь когда люди будут заходить и будет приветствие!")

@dp.message(F.new_chat_members)
async def on_user_join(message: types.Message):
    db = load_db()
    chat_id_str = str(message.chat.id)
    welcome_text = db.get("chats", {}).get(chat_id_str, {}).get("welcome")
    if welcome_text:
        for new_member in message.new_chat_members:
            if new_member.id != bot.id:
                await message.reply(f"{welcome_text}\nПривет, {new_member.first_name}!")

# --- СИСТЕМА МОДЕРАЦИИ (МУТЫ, БАНЫ) ---

def parse_time(time_str):
    match = re.match(r"(\d+)([mhd])", time_str)
    if not match: return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == 'm': return timedelta(minutes=amount)
    if unit == 'h': return timedelta(hours=amount)
    if unit == 'd': return timedelta(days=amount)
    return None

@dp.message(Command("mut"))
async def cmd_mut(message: types.Message):
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение пользователя, которого нужно замутить.")
        return
        
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.reply("❌ Пример: /mut 1h причина (1h = 1 час, 1d = 1 день, 1m = 1 минута)")
        return

    time_delta = parse_time(args[1])
    if not time_delta:
        await message.reply("❌ Неверный формат времени. Используйте: 1m, 1h, 1d")
        return

    reason = args[2] if len(args) > 2 else "Без причины"
    until_date = datetime.now() + time_delta

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=message.reply_to_message.from_user.id,
            permissions=types.ChatPermissions(can_send_messages=False),
            until_date=until_date
        )
        await message.reply(f"🔇 Пользователь замучен на {args[1]}.\nПричина: {reason}")
    except Exception as e:
        await message.reply("❌ Ошибка. Убедитесь, что бот является администратором в чате и имеет права на ограничение пользователей.")

@dp.message(Command("unmute"))
async def cmd_unmute(message: types.Message):
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение пользователя, которого нужно размутить.")
        return
    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=message.reply_to_message.from_user.id,
            permissions=types.ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await message.reply("Вас администратор размутил, вы можете спокойно общаться")
    except:
        await message.reply("❌ Не удалось размутить. Проверьте права бота.")

@dp.message(Command("ban"))
async def cmd_ban(message: types.Message):
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение пользователя, которого нужно забанить.")
        return
    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=message.reply_to_message.from_user.id)
        await message.reply("🔨 Пользователь успешно забанен.")
    except Exception as e:
        await message.reply("❌ Не удалось забанить. Убедитесь, что бот является администратором и имеет соответствующие права.")

@dp.message(Command("unban"))
async def cmd_unban(message: types.Message):
    if not message.reply_to_message:
        await message.reply("❌ Ответьте на сообщение пользователя, которого нужно разбанить.")
        return
    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=message.reply_to_message.from_user.id, only_if_banned=True)
        await message.reply("✅ Пользователь успешно разбанен, он снова может вступить в чат.")
    except Exception as e:
        await message.reply("❌ Не удалось разбанить. Проверьте права бота.")


# =====================================================================
# --- ИЗМЕНЕННЫЙ ПРОФИЛЬ, ПРОКАЧКА И ROB ---
# =====================================================================

@dp.message(Command("profil", "balance", "баланс"))
@dp.message(F.text.lower().in_({"/profil", "профиль", "/balance", "/баланс", "баланс"}))
async def cmd_profile(message: types.Message, state: FSMContext):
    await state.clear()
    db = load_db()
    init_user(db, message.from_user.id, message.from_user.username)
    u = db["users"][str(message.from_user.id)]
    
    text = f"👤 **Профиль игрока:**\n"
    if is_vip(u): text = f"💎 " + text
        
    text += f"📝 Юз: @{message.from_user.username or 'отсутствует'}\n"
    text += f"🆔 Айди: `{message.from_user.id}`\n"
    
    if message.from_user.id == OWNER_ID:
        text += f"👑 **Статус:** Владелец и Админ\n"
        text += f"🛠 **Админ-команды:** `/adminpanel`, `/give`, `/forgive`, `/datbvip`\n"
        
    text += f"---------------------------\n"
    text += f"💰 Коины: {u['coins']}\n"
    text += f"👑 Золото: {u['gold']}\n"
    text += f"💎 Алмазы: {u['diamonds']}\n"
    text += f"👥 Рефералы: {u.get('referrals', 0)}\n"
    
    if u.get("loan_amount", 0) > 0:
        text += f"🏦 **Долг:** {u['loan_amount']} {u['loan_currency']}\n"
    if u.get("spouse"):
        text += f"💍 **В браке с ID:** `{u['spouse']}`\n"
        
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("upgrade", "prokachka", "прокачка"))
async def cmd_upgrade(message: types.Message):
    await message.reply("Эта система в разработке")

@dp.message(Command("rob", ignore_case=True))
async def cmd_rob(message: types.Message, state: FSMContext):
    if not message.reply_to_message:
        return await message.reply("❌ Ответьте на сообщение игрока, которого хотите ограбить!")
        
    target_id = message.reply_to_message.from_user.id
    if target_id == message.from_user.id:
        return await message.reply("❌ Вы не можете ограбить сами себя!")
        
    await state.update_data(target_id=target_id)
    await state.set_state(RobState.currency)
    await message.reply("Выберете какую валюту у игрока хотите украсть:\nАлмазы, коины, золото", reply_markup=cancel_kb)

@dp.message(RobState.currency)
async def rob_currency(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    choice = message.text.lower().strip()
    
    if choice in ["алмазы", "алмаз", "diamonds"]: curr = "diamonds"; name = "Алмазов"
    elif choice in ["золото", "золот", "gold"]: curr = "gold"; name = "Золота"
    elif choice in ["коины", "коин", "coins"]: curr = "coins"; name = "Коинов"
    else:
        return await message.reply("❌ Неверная валюта. Выберите: Алмазы, коины, золото", reply_markup=cancel_kb)
        
    await state.update_data(currency=curr, curr_name=name)
    await state.set_state(RobState.amount)
    await message.reply(f"Сколько хотите Украсть {name}?\nВведите сумму:", reply_markup=cancel_kb)

@dp.message(RobState.amount)
async def rob_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите сумму больше нуля.", reply_markup=cancel_kb)
        
    data = await state.get_data()
    curr = data["currency"]
    curr_name = data["curr_name"]
    target_id = str(data["target_id"])
    
    db = load_db()
    uid = str(message.from_user.id)
    
    init_user(db, message.from_user.id, message.from_user.username)
    init_user(db, int(target_id))
    
    target_balance = db["users"][target_id][curr]
    
    if target_balance < amount:
        await message.reply(f"❌ У игрока нету {curr_name.lower()} в таком количестве.", reply_markup=types.ReplyKeyboardRemove())
    else:
        db["users"][target_id][curr] -= amount
        db["users"][uid][curr] += amount
        save_db(db)
        
        await message.reply(f"✅ Вы забрали {amount} {curr_name}.", reply_markup=types.ReplyKeyboardRemove())
        
        try:
            await bot.send_message(int(target_id), f"⚠️ Внимание! У вас забрали {curr_name.lower()} в размере {amount}.")
        except Exception:
            pass
            
    await state.clear()


@dp.message(Command("vip"))
async def cmd_vip(message: types.Message):
    if message.chat.type != "private":
        bot_info = await bot.get_me()
        await message.reply(f"❌ Покупка VIP доступна **только в личных сообщениях** с ботом!\nПерейдите сюда: @{bot_info.username} и напишите `/vip`.")
        return

    prices = [types.LabeledPrice(label="VIP на месяц", amount=1)]
    try:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title="VIP Статус 💎",
            description="Больше коинов с фарма, х2 призы в играх.",
            payload="vip_1_month",
            provider_token="",
            currency="XTR",
            prices=prices
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка платежа. Убедитесь, что ваш Telegram поддерживает оплату Stars.\nКод ошибки: {e}")

@dp.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# ИЗМЕНЕННЫЙ ОБРАБОТЧИК ОПЛАТЫ (теперь ловит и вип, и донат)
@dp.message(F.successful_payment)
async def on_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    if payload == "vip_1_month":
        db["users"][uid]["vip_until"] = (datetime.now() + timedelta(days=30)).isoformat()
        save_db(db)
        await message.reply("🎉 Поздравляем! Вы успешно приобрели VIP на 1 месяц! 💎")
    
    elif payload == "donat_stars":
        stars_amount = message.successful_payment.total_amount
        save_db(db)
        await message.reply("🌟 Спасибо за поддержку бота! Твой донат делает нас лучше!")
        try:
            await bot.send_message(
                OWNER_ID, 
                f"💸 **Новый донат!**\n👤 Игрок: @{message.from_user.username or 'без юзернейма'} (ID: `{message.from_user.id}`)\n🌟 Поддержал бота на **{stars_amount} XTR** (звёзд)!",
                parse_mode="Markdown"
            )
        except:
            pass


@dp.message(Command("datbvip"))
async def cmd_datbvip(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID: return
    await state.set_state(AdminVipState.target)
    await message.reply("Введите юз или айди кому хотите выдать VIP:", reply_markup=cancel_kb)

@dp.message(AdminVipState.target)
async def process_vip_target(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    target_input = message.text.strip()
    db = load_db()
    target_id = target_input if target_input.isdigit() else db["usernames"].get(target_input.replace("@", "").lower())

    if not target_id or str(target_id) not in db["users"]:
        await message.reply("❌ Пользователь не найден!", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="На 1 месяц", callback_data=f"givevip_{target_id}_1")],
        [types.InlineKeyboardButton(text="Навсегда", callback_data=f"givevip_{target_id}_forever")],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{message.from_user.id}")]
    ])
    await message.reply("На сколько выдать?", reply_markup=kb)
    await state.clear()

@dp.message(F.text.lower().in_({"сбор", "farm", "/farm"}))
async def cmd_farm(message: types.Message, state: FSMContext):
    await state.clear()
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    user_data = db["users"][uid]
    now = datetime.now()
    
    if user_data["last_farm"]:
        last_farm_time = datetime.fromisoformat(user_data["last_farm"])
        if now - last_farm_time < timedelta(hours=1):
            remaining = timedelta(hours=1) - (now - last_farm_time)
            minutes = int(remaining.seconds // 60)
            await message.reply(f"⏳ Вы уже собирали ресурсы! Подождите ещё {minutes} мин.")
            return

    reward = 30 if is_vip(user_data) else 8
    user_data["coins"] += reward
    user_data["last_farm"] = now.isoformat()
    save_db(db)
    await message.reply(f"🚜 Вы успешно провели сбор! Получено: `+{reward} коинов`💰")


# =====================================================================
# --- ИГРЫ (КУБИКИ, МИНЫ, РАКЕТКА, РУЛЕТКА И ОСТАЛЬНЫЕ) ---
# =====================================================================

@dp.message(F.text == "Продолжить русскую рулетку")
@dp.message(Command("roulette", "рулетка"))
async def cmd_roulette(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Алмазы 💎", callback_data=f"rr_curr_diamonds_{uid}"),
            types.InlineKeyboardButton(text="Золото 👑", callback_data=f"rr_curr_gold_{uid}"),
            types.InlineKeyboardButton(text="Коины 💰", callback_data=f"rr_curr_coins_{uid}")
        ],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{uid}")]
    ])
    await message.reply("🔫 **Русская рулетка**\nВыберите валюту для ставки:", reply_markup=kb)

@dp.callback_query(F.data.startswith("rr_curr_"))
async def cb_rr_curr(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    curr = parts[2]
    uid_check = int(parts[3])
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
        
    await state.update_data(currency=curr)
    await state.set_state(RouletteState.amount)
    await callback.message.delete()
    await callback.message.answer("Введите сумму ставки:", reply_markup=cancel_kb)

@dp.message(RouletteState.amount)
async def process_rr_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите корректное число больше нуля.", reply_markup=cancel_kb)
        
    data = await state.get_data()
    curr = data["currency"]
    
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    if db["users"][uid][curr] < amount:
        await message.reply("❌ У вас недостаточно средств для такой ставки.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    await state.update_data(amount=amount)
    await state.set_state(RouletteState.bullet)
    
    kb = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="1"), types.KeyboardButton(text="2"), types.KeyboardButton(text="3")],
        [types.KeyboardButton(text="4"), types.KeyboardButton(text="5"), types.KeyboardButton(text="6")],
        [types.KeyboardButton(text="В главное меню 🔙")]
    ], resize_keyboard=True)
    await message.reply("Какой вы патрон выберете??\n1 = 1 патрон\n2 = 2 патрона\n3 = 3 патрона\n4 = 4 патрона\n5 = 5 патрона\n6 = 6 патрона", reply_markup=kb)

@dp.message(RouletteState.bullet)
async def process_rr_bullet(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    bullet = int(message.text)
    if bullet < 1 or bullet > 6:
        return await message.reply("❌ Выберите патрон от 1 до 6.")
        
    data = await state.get_data()
    curr = data["currency"]
    amount = data["amount"]
    
    db = load_db()
    uid = str(message.from_user.id)
    
    if db["users"][uid][curr] < amount:
        await message.reply("❌ Недостаточно средств.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    db["users"][uid][curr] -= amount
    
    death_bullet = random.randint(1, 6)
    win = (bullet != death_bullet)
    
    if win:
        prize = amount * 2
        db["users"][uid][curr] += prize
        res_text = f"🎉 Вы выиграли в русской рулетке (сумма умножена в 2х)!"
    else:
        res_text = f"💀 Вы проиграли в русской рулетке в следующий раз повезет!"
        
    save_db(db)
    
    u = db["users"][uid]
    user = message.from_user
    name_str = user.first_name
    username_str = f"@{user.username}" if user.username else "Отсутствует"
    
    info = (
        f"👤 Имя: {name_str} | Юз: {username_str} | ID: {user.id}\n"
        f"💰 Баланс: Коины {u['coins']} | 💎 Алмазы {u['diamonds']} | 👑 Золото {u['gold']}"
    )
    
    final_text = f"{res_text}\n\n{info}"
    
    kb = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="Продолжить русскую рулетку")],
        [types.KeyboardButton(text="В главное меню 🔙")]
    ], resize_keyboard=True)
    
    await message.reply(final_text, reply_markup=kb)
    await state.clear()


@dp.message(Command("cubici"))
async def cmd_cubici(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Алмазы 💎", callback_data=f"cubici_diamonds_{uid}"),
            types.InlineKeyboardButton(text="Золото 👑", callback_data=f"cubici_gold_{uid}"),
            types.InlineKeyboardButton(text="Коины 💰", callback_data=f"cubici_coins_{uid}")
        ],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{uid}")]
    ])
    await message.reply("Какую валюту хотите поставить?", reply_markup=kb)

@dp.callback_query(F.data.startswith("cubici_"))
async def cb_cubici_curr(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    curr = parts[1]
    uid_check = int(parts[2])
    
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
        
    await state.update_data(currency=curr)
    await state.set_state(CubiciState.amount)
    curr_names = {"diamonds": "алмазов", "gold": "золота", "coins": "коинов"}
    await callback.message.delete()
    await callback.message.answer(f"Сколько {curr_names[curr]} хотите поставить?", reply_markup=cancel_kb)

@dp.message(CubiciState.amount)
async def process_cubici_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите корректное число больше нуля.", reply_markup=cancel_kb)
        
    data = await state.get_data()
    curr = data["currency"]
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    if db["users"][uid][curr] < amount:
        await message.reply("❌ У вас недостаточно средств.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    await state.update_data(amount=amount)
    await state.set_state(CubiciState.number)
    
    kb = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="1"), types.KeyboardButton(text="2"), types.KeyboardButton(text="3")],
        [types.KeyboardButton(text="4"), types.KeyboardButton(text="5"), types.KeyboardButton(text="6")],
        [types.KeyboardButton(text="В главное меню 🔙")]
    ], resize_keyboard=True, one_time_keyboard=True)
    await message.reply("Какое число выбираете (от 1 до 6)?", reply_markup=kb)

@dp.message(CubiciState.number)
async def process_cubici_number(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    num = int(message.text)
    if num < 1 or num > 6:
        return await message.reply("❌ Выберите число от 1 до 6.")
        
    data = await state.get_data()
    curr = data["currency"]
    amount = data["amount"]
    
    db = load_db()
    uid = str(message.from_user.id)
    vip = is_vip(db["users"][uid])
    
    if db["users"][uid][curr] < amount:
        await message.reply("❌ Недостаточно средств.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    db["users"][uid][curr] -= amount
    save_db(db)
    
    dice_msg = await message.answer_dice(emoji="🎲", reply_markup=types.ReplyKeyboardRemove())
    await asyncio.sleep(4)
    val = dice_msg.dice.value
    
    win_multiplier = 2 if vip else 1.5
    win_amount = int(amount * win_multiplier)
    
    if val == num:
        db = load_db()
        db["users"][uid][curr] += amount + win_amount
        save_db(db)
        await message.reply(f"🎲 Вы выиграли! Выпало {val}.\nПолучено: +{win_amount}")
    else:
        await message.reply(f"🎲 Увы вы проиграли. Выпало число {val}.")
    await state.clear()

@dp.message(Command("casino", "darts", "football", "basketball"))
async def cmd_bet_games(message: types.Message, state: FSMContext):
    await state.clear()
    game_name = message.text.split()[0].replace("/", "").split("@")[0].lower()
    
    await state.update_data(game=game_name)
    await state.set_state(GameBetState.currency)
    
    game_titles = {"casino": "Казино 🎰", "darts": "Дартс 🎯", "football": "Футбол ⚽", "basketball": "Баскетбол 🏀"}
    uid = message.from_user.id
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Алмазы 💎", callback_data=f"gbet_diamonds_{uid}"),
            types.InlineKeyboardButton(text="Золото 👑", callback_data=f"gbet_gold_{uid}"),
            types.InlineKeyboardButton(text="Коины 💰", callback_data=f"gbet_coins_{uid}")
        ],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{uid}")]
    ])
    await message.reply(f"🎮 Игра: **{game_titles[game_name]}**\nКакую валюту хотите поставить?", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("gbet_"))
async def cb_gbet_curr(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    curr = parts[1]
    uid_check = int(parts[2])
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
        
    await state.update_data(currency=curr)
    await state.set_state(GameBetState.amount)
    curr_names = {"diamonds": "алмазов", "gold": "золота", "coins": "коинов"}
    await callback.message.delete()
    await callback.message.answer(f"Сколько **{curr_names[curr]}** хотите поставить?", parse_mode="Markdown", reply_markup=cancel_kb)

@dp.message(GameBetState.amount)
async def process_gbet_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    bet = int(message.text)
    if bet <= 0:
        return await message.reply("❌ Введите корректное число больше нуля.", reply_markup=cancel_kb)
        
    data = await state.get_data()
    curr = data["currency"]
    game_name = data["game"]
    
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    if db["users"][uid][curr] < bet:
        await message.reply("❌ У вас недостаточно средств для такой ставки.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    db["users"][uid][curr] -= bet
    save_db(db)
    vip = is_vip(db["users"][uid])
    
    await message.answer("Ставка принята, кидаем кости...", reply_markup=types.ReplyKeyboardRemove())
    
    if game_name == "casino":
        dice_msg = await message.answer_dice(emoji="🎰")
        await asyncio.sleep(4)
        val = dice_msg.dice.value - 1
        slots = ["🍫", "🍇", "🍋", "7️⃣"]
        s1, s2, s3 = slots[val % 4], slots[(val // 4) % 4], slots[val // 16]
        res_str = f"{s1} | {s2} | {s3}"
        
        if s1 == s2 == s3:
            win = bet * (6 if vip else 3)
            db["users"][uid][curr] += win
            out = f"🎰 **КАЗИНО** 🎰\n\n[ {res_str} ]\n🔥 **ДЖЕКПОТ!** Вы забрали: +{win}!"
        elif s1 == s2 or s2 == s3 or s1 == s3:
            win = int(bet * (3 if vip else 1.5))
            db["users"][uid][curr] += win
            out = f"🎰 **КАЗИНО** 🎰\n\n[ {res_str} ]\n🎉 **Победа!** Приз: +{win}!"
        else:
            out = f"🎰 **КАЗИНО** 🎰\n\n[ {res_str} ]\n🛑 **Вы проиграли.**"

    elif game_name == "darts":
        dice_msg = await message.answer_dice(emoji="🎯")
        await asyncio.sleep(4)
        val = dice_msg.dice.value
        
        if val == 6:
            win = bet * (6 if vip else 3)
            db["users"][uid][curr] += win
            out = f"🎯 **ДАРТС** 🎯\n\n🎯 В яблочко! Ваш приз: +{win}!"
        elif val >= 4:
            win = int(bet * (3 if vip else 1.5))
            db["users"][uid][curr] += win
            out = f"🎯 **ДАРТС** 🎯\n\n🎉 Хороший бросок: +{win}!"
        else:
            out = f"🎯 **ДАРТС** 🎯\n\n❌ Вы проиграли."

    elif game_name == "football":
        dice_msg = await message.answer_dice(emoji="⚽")
        await asyncio.sleep(4)
        val = dice_msg.dice.value
        
        if val >= 3:
            win = bet * (4 if vip else 2)
            db["users"][uid][curr] += win
            out = f"⚽ **ФУТБОЛ** ⚽\n\n⚡ ГОЛ! Награда: +{win}!"
        else:
            out = f"⚽ **ФУТБОЛ** ⚽\n\n❌ Вы проиграли. Мимо ворот..."

    elif game_name == "basketball":
        dice_msg = await message.answer_dice(emoji="🏀")
        await asyncio.sleep(4)
        val = dice_msg.dice.value
        
        if val >= 4:
            win = bet * (4 if vip else 2)
            db["users"][uid][curr] += win
            out = f"🏀 **БАСКЕТБОЛ** 🏀\n\n🏀 Точный бросок! Вы выиграли: +{win}!"
        else:
            out = f"🏀 **БАСКЕТБОЛ** 🏀\n\n❌ Вы проиграли. Промах! Мяч не попал в кольцо."

    save_db(db)
    await message.reply(out)
    await state.clear()


@dp.message(Command("mines"))
async def cmd_mines(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Алмазы 💎", callback_data=f"mines_curr_diamonds_{uid}"),
            types.InlineKeyboardButton(text="Золото 👑", callback_data=f"mines_curr_gold_{uid}"),
            types.InlineKeyboardButton(text="Коины 💰", callback_data=f"mines_curr_coins_{uid}")
        ],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{uid}")]
    ])
    await message.reply("💣 **Мины**\nВыберете какую валюту хотите потратить в мины:", reply_markup=kb)

@dp.callback_query(F.data.startswith("mines_curr_"))
async def cb_mines_curr(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    curr = parts[2]
    uid_check = int(parts[3])
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
        
    await state.update_data(currency=curr)
    await state.set_state(MinesState.amount)
    names = {"diamonds": "алмазов", "gold": "золота", "coins": "коинов"}
    await callback.message.delete()
    await callback.message.answer(f"Введите количество **{names[curr]}**, которое хотите поставить:", reply_markup=cancel_kb)

@dp.message(MinesState.amount)
async def process_mines_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите корректное число больше нуля.", reply_markup=cancel_kb)
        
    data = await state.get_data()
    curr = data["currency"]
    
    db = load_db()
    uid = str(message.from_user.id)
    init_user(db, message.from_user.id, message.from_user.username)
    
    if db["users"][uid][curr] < amount:
        await message.reply("❌ Недостаточно средств.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    await state.update_data(amount=amount)
    await state.set_state(MinesState.mines_count)
    await message.reply("Введите количество мин (от 1 до 8):", reply_markup=cancel_kb)

@dp.message(MinesState.mines_count)
async def process_mines_count(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    mines_count = int(message.text)
    if mines_count < 1 or mines_count > 8:
        return await message.reply("❌ Введите число от 1 до 8.")
        
    data = await state.get_data()
    curr = data["currency"]
    amount = data["amount"]
    
    db = load_db()
    uid = str(message.from_user.id)
    
    if db["users"][uid][curr] < amount:
        await message.reply("❌ Недостаточно средств.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    db["users"][uid][curr] -= amount
    save_db(db)
    
    grid = [True]*mines_count + [False]*(9-mines_count)
    random.shuffle(grid)
    
    await state.update_data(grid=grid, safe_clicks=0, mines_count=mines_count, clicked=[False]*9)
    
    msg = await message.reply("Начинаем игру...", reply_markup=types.ReplyKeyboardRemove())
    
    kb = get_mines_keyboard([False]*9, grid, message.from_user.id, playing=True)
    await msg.answer(f"💣 **Мины**\nСтавка: {amount}\nМин на поле: {mines_count}\nМножитель: 1.0x\n\nВыбирайте ячейку!", reply_markup=kb)

def get_mines_keyboard(clicked, grid, user_id, playing=True, safe_clicks=0, mines_count=0):
    kb = []
    for i in range(0, 9, 3):
        row = []
        for j in range(3):
            idx = i + j
            if not playing:
                if grid[idx]:
                    row.append(types.InlineKeyboardButton(text="💣", callback_data="ignore_cb"))
                else:
                    row.append(types.InlineKeyboardButton(text="💎" if clicked[idx] else "⬜", callback_data="ignore_cb"))
            else:
                if clicked[idx]:
                    row.append(types.InlineKeyboardButton(text="✅", callback_data="ignore_cb"))
                else:
                    row.append(types.InlineKeyboardButton(text="❓", callback_data=f"mine_click_{idx}_{user_id}"))
        kb.append(row)
    
    if playing and safe_clicks > 0:
        multiplier = round(1 + (safe_clicks * (mines_count * 0.2)), 2)
        kb.append([types.InlineKeyboardButton(text=f"💰 Забрать выигрыш ({multiplier}x)", callback_data=f"mine_take_{user_id}")])
        
    return types.InlineKeyboardMarkup(inline_keyboard=kb)

@dp.callback_query(F.data.startswith("mine_click_"))
async def cb_mine_click(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    idx = int(parts[2])
    uid_check = int(parts[3])
    
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
        
    data = await state.get_data()
    if not data.get("grid"): 
        return await callback.answer("Игра окончена.", show_alert=True)
        
    grid = data["grid"]
    clicked = data["clicked"]
    safe_clicks = data["safe_clicks"]
    mines_count = data["mines_count"]
    user_id = callback.from_user.id
    
    if grid[idx]:
        kb = get_mines_keyboard(clicked, grid, user_id, playing=False)
        await state.clear()
        await callback.message.edit_text("💥 БАМ! Вы попали на мину.\nВы проиграли в минах.", reply_markup=kb)
    else:
        clicked[idx] = True
        safe_clicks += 1
        await state.update_data(clicked=clicked, safe_clicks=safe_clicks)
        
        if safe_clicks == (9 - mines_count):
            await cb_mine_take(callback, state, force_take=True)
        else:
            multiplier = round(1 + (safe_clicks * (mines_count * 0.2)), 2)
            kb = get_mines_keyboard(clicked, grid, user_id, playing=True, safe_clicks=safe_clicks, mines_count=mines_count)
            await callback.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("mine_take_"))
async def cb_mine_take(callback: types.CallbackQuery, state: FSMContext, force_take=False):
    if not force_take:
        uid_check = int(callback.data.split("_")[2])
        if callback.from_user.id != uid_check:
            return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
            
    data = await state.get_data()
    if not data.get("grid"): return
    
    safe_clicks = data["safe_clicks"]
    mines_count = data["mines_count"]
    amount = data["amount"]
    curr = data["currency"]
    grid = data["grid"]
    clicked = data["clicked"]
    user_id = callback.from_user.id
    
    multiplier = round(1 + (safe_clicks * (mines_count * 0.2)), 2)
    win_amount = int(amount * multiplier)
    
    db = load_db()
    uid = str(user_id)
    db["users"][uid][curr] += win_amount
    save_db(db)
    
    kb = get_mines_keyboard(clicked, grid, user_id, playing=False)
    names = {"diamonds": "алмазов", "gold": "золота", "coins": "коинов"}
    
    await callback.message.edit_text(f"🎉 Вы выиграли! Вы можете внизу забрать {win_amount} {names[curr]}.\nЧтобы продолжить, введите команду заново.", reply_markup=kb)
    await state.clear()


@dp.message(Command("rocket"))
async def cmd_rocket(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Алмазы 💎", callback_data=f"rocket_curr_diamonds_{uid}"),
            types.InlineKeyboardButton(text="Золото 👑", callback_data=f"rocket_curr_gold_{uid}"),
            types.InlineKeyboardButton(text="Коины 💰", callback_data=f"rocket_curr_coins_{uid}")
        ],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{uid}")]
    ])
    await message.reply("🚀 **Игра Ракетка (Краш)**\nВыберете какую валюту хотите потратить:", reply_markup=kb)

@dp.callback_query(F.data.startswith("rocket_curr_"))
async def cb_rocket_curr(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    curr = parts[2]
    uid_check = int(parts[3])
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
        
    await state.update_data(currency=curr)
    await state.set_state(RocketState.amount)
    names = {"diamonds": "алмазов", "gold": "золота", "coins": "коинов"}
    await callback.message.delete()
    await callback.message.answer(f"Введите количество **{names[curr]}**, которое хотите поставить:", reply_markup=cancel_kb)

@dp.message(RocketState.amount)
async def process_rocket_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit(): return
    amount = int(message.text)
    if amount <= 0:
        return await message.reply("❌ Введите корректное число больше нуля.", reply_markup=cancel_kb)
        
    data = await state.get_data()
    curr = data["currency"]
    
    msg = await message.reply("Запускаем двигатели 🚀...", reply_markup=types.ReplyKeyboardRemove())
    await asyncio.sleep(2)
    await play_rocket(msg, message.from_user.id, curr, amount, state, is_callback=True)

@dp.callback_query(F.data.startswith("rocket_play_"))
async def cb_rocket_play_again(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    curr = parts[2]
    amount_str = parts[3]
    uid_check = int(parts[4])
    
    if callback.from_user.id != uid_check:
        return await callback.answer("Не хуллигань не твоя ставка дождись своей ставки", show_alert=True)
        
    amount = int(amount_str)
    try:
        await callback.message.edit_text("Запускаем двигатели 🚀...")
    except:
        pass
    await asyncio.sleep(2)
    await play_rocket(callback.message, callback.from_user.id, curr, amount, state, is_callback=True)
    try:
        await callback.answer()
    except:
        pass

async def play_rocket(message_or_cb, user_id, curr, amount, state, is_callback=False):
    db = load_db()
    uid = str(user_id)
    init_user(db, user_id)
    
    game_id = random.randint(10000, 99999)
    
    if db["users"][uid][curr] < amount:
        text = f"❌ У вас недостаточно средств для игры.\n\n🎲 Матч #{game_id}"
        if is_callback:
            try: await message_or_cb.edit_text(text)
            except: await message_or_cb.answer(text)
        else:
            await message_or_cb.reply(text)
        await state.clear()
        return
        
    db["users"][uid][curr] -= amount
    
    win = random.choice([True, False])
    names = {"diamonds": "алмазов", "gold": "золота", "coins": "коинов"}
    
    if win:
        prize = amount * 2
        db["users"][uid][curr] += prize
        text = f"🚀 Ракетка полетела!\n🎉 Вы выиграли в ракетке {prize} {names[curr]}! Можете снова играть внизу или выйти.\n\n🎲 Матч #{game_id}"
    else:
        text = f"💥 Ракетка взорвалась (Слив)...\n❌ Вы проиграли в ракетке. В следующий раз повезет!\n\n🎲 Матч #{game_id}"
        
    save_db(db)
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Снова продолжить 🚀", callback_data=f"rocket_play_{curr}_{amount}_{user_id}")],
        [types.InlineKeyboardButton(text="В главное меню 🔙", callback_data=f"main_menu_{user_id}")]
    ])
    
    if is_callback:
        try:
            await message_or_cb.edit_text(text, reply_markup=kb)
        except Exception as e:
            try:
                await message_or_cb.answer(text, reply_markup=kb)
            except: pass
    else:
        await message_or_cb.reply(text, reply_markup=kb)
    await state.clear()

@dp.callback_query(F.data == "ignore_cb")
async def cb_ignore_general(callback: types.CallbackQuery):
    await callback.answer()


# =====================================================================
# --- НОВЫЕ СИСТЕМЫ: ДОНАТ И ПРЕДЛОЖКА ---
# =====================================================================

@dp.message(Command("donat", ignore_case=True))
async def cmd_donat(message: types.Message, state: FSMContext):
    await state.set_state(DonatState.amount)
    await message.reply("🌟 **Сколько хотите дать звёзд!?!?**\n(Пример: 1, 10, 50)", reply_markup=cancel_kb)

@dp.message(DonatState.amount)
async def process_donat_amount(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    if not message.text.isdigit():
        return await message.reply("❌ Пожалуйста, введите число (например, 1).", reply_markup=cancel_kb)
    
    amount = int(message.text)
    if amount < 1:
        return await message.reply("❌ Минимум 1 звезда.", reply_markup=cancel_kb)
    
    prices = [types.LabeledPrice(label="Донат боту", amount=amount)]
    
    try:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title="Поддержка бота 🌟",
            description=f"Донат в размере {amount} звёзд.",
            payload="donat_stars",
            provider_token="",  # Пустой токен для XTR (звёзд)
            currency="XTR",
            prices=prices
        )
        await state.clear()
        await message.reply("👇 Внизу появилась кнопка для оплаты!", reply_markup=types.ReplyKeyboardRemove())
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
        await state.clear()

@dp.message(Command("predlojka", ignore_case=True))
async def cmd_predlojka(message: types.Message, state: FSMContext):
    await state.set_state(PredlojkaState.idea)
    await message.reply("💡 Напишите идею свою какую вы хотите:", reply_markup=cancel_kb)

@dp.message(PredlojkaState.idea)
async def process_predlojka_idea(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    idea_text = message.text
    user_id = message.from_user.id
    username = message.from_user.username or "Отсутствует"
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Ответить 💬", callback_data=f"pred_reply_{user_id}"),
            types.InlineKeyboardButton(text="Проигнорировать ❌", callback_data=f"pred_ignore_{user_id}")
        ]
    ])
    
    try:
        await bot.send_message(
            OWNER_ID,
            f"💡 **Новая идея/предложение!**\n👤 От: @{username} (ID: `{user_id}`)\n\n📝 Идея:\n{idea_text}",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await message.reply("✅ Ваша идея успешно отправлена создателю бота!", reply_markup=types.ReplyKeyboardRemove())
    except Exception:
        await message.reply("❌ Ошибка отправки. Возможно, создатель заблокировал бота.", reply_markup=types.ReplyKeyboardRemove())
        
    await state.clear()

@dp.callback_query(F.data.startswith("pred_ignore_"))
async def cb_pred_ignore(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    try:
        await bot.send_message(user_id, "Ваш ответ проигнорировали")
        await callback.message.edit_text(callback.message.text + "\n\n❌ **Проигнорировано**")
    except:
        pass
    await callback.answer("Успешно проигнорировано")

@dp.callback_query(F.data.startswith("pred_reply_"))
async def cb_pred_reply(callback: types.CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split("_")[2])
    await state.update_data(target_id=user_id)
    await state.set_state(PredlojkaReplyState.text)
    await callback.message.answer("✍️ Напишите ответ пользователю:", reply_markup=cancel_kb)
    await callback.answer()

@dp.message(PredlojkaReplyState.text)
async def process_predlojka_reply(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    data = await state.get_data()
    target_id = data.get("target_id")
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Ответить 💬", callback_data=f"user_pred_reply_{message.from_user.id}")]
    ])
    
    try:
        await bot.send_message(
            target_id,
            f"👨‍💻 **Вам пришел ответ от Создателя бота:**\n\n{message.text}",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await message.reply("✅ Ответ успешно отправлен пользователю!", reply_markup=types.ReplyKeyboardRemove())
    except Exception:
        await message.reply("❌ Ошибка! Пользователь заблокировал бота.", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()

@dp.callback_query(F.data.startswith("user_pred_reply_"))
async def cb_user_pred_reply(callback: types.CallbackQuery, state: FSMContext):
    owner_id = int(callback.data.split("_")[3])
    await state.update_data(target_id=owner_id)
    await state.set_state(PredlojkaUserReplyState.text)
    await callback.message.answer("✍️ Напишите ваш ответ создателю:", reply_markup=cancel_kb)
    await callback.answer()

@dp.message(PredlojkaUserReplyState.text)
async def process_predlojka_user_reply(message: types.Message, state: FSMContext):
    if message.text == "В главное меню 🔙": return
    data = await state.get_data()
    target_id = data.get("target_id")
    user_id = message.from_user.id
    username = message.from_user.username or "Отсутствует"
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="Ответить 💬", callback_data=f"pred_reply_{user_id}"),
            types.InlineKeyboardButton(text="Проигнорировать ❌", callback_data=f"pred_ignore_{user_id}")
        ]
    ])
    
    try:
        await bot.send_message(
            target_id,
            f"📨 **Ответ от пользователя!**\n👤 От: @{username} (ID: `{user_id}`)\n\n📝 Текст:\n{message.text}",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await message.reply("✅ Ваш ответ успешно отправлен создателю!", reply_markup=types.ReplyKeyboardRemove())
    except Exception:
        await message.reply("❌ Ошибка отправки.", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


@dp.message(Command("zagadat"))
async def cmd_zagadat(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Пример: `/zagadat 6767` или `/zagadat бурмалда`")
        return
        
    word = args[1].lower().strip()
    guess_game["word"] = word
    guess_game["chat_id"] = message.chat.id
    guess_game["author_id"] = message.from_user.id
    
    hint = f"из цифр {len(word)} штук" if word.isdigit() else "буквы написанные"
    await message.reply(f"🧩 Подсказка: это {hint}. Игроки должны угадать!")

@dp.message(StateFilter(None), ~F.text.startswith('/'))
async def guess_or_catch_all(message: types.Message):
    if message.text == "В главное меню 🔙": return
    if guess_game.get("word") and message.chat.id == guess_game["chat_id"]:
        if message.from_user.id == guess_game["author_id"]: return
            
        text = message.text.lower().strip()
        if text == guess_game["word"]:
            uid = str(message.from_user.id)
            db = load_db()
            init_user(db, message.from_user.id, message.from_user.username)
            
            reward = 30 if is_vip(db["users"][uid]) else 8
            db["users"][uid]["coins"] += reward
            save_db(db)
            
            await message.reply(f"🎉 Вы выиграли! Это было: {guess_game['word']}. +{reward} коинов.")
            guess_game["word"] = None
            guess_game["chat_id"] = None

# --- ТОЧКА ВХОДА ---
async def main():
    logger.info(f"👑 Владелец: {OWNER_ID}")
    asyncio.create_task(check_loans_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n--- Бот запущен {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    print("🚀 Бот успешно запущен и готов к играм!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

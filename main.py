# bot.py
import os
import json
import re
from datetime import datetime, timedelta, timezone
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters
)
import google.generativeai as genai
from dotenv import load_dotenv
import asyncio
# Завантажуємо змінні з .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not BOT_TOKEN:
    raise ValueError("Не вдалося завантажити BOT_TOKEN з .env файлу")
# Налаштування Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
else:
     model = None # Без ключа ИИ не будет работать, но бот запустится
# Файли для зберігання даних
DATA_FILE = "bot_data.json"
CONVERSATIONS_FILE = "conversations.json"
# === ФУНКЦІЇ ДЛЯ РОБОТИ З ФАЙЛАМИ ===
def load_json(filename):
    """Завантажує JSON з файлу"""
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Помилка завантаження {filename}: {e}")
            return {}
    return {}
def save_json(filename, data):
    """Зберігає дані в JSON-файл"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"⚠️ Помилка збереження {filename}: {e}")
# === ФУНКЦІЇ ДЛЯ РОБОТИ З КОНТЕКСТОМ ===
async def get_user_name(user):
    """Отримує ім'я користувача"""
    return user.first_name or user.username or f"Користувач {user.id}"
async def get_conversation_context(user_id):
    """Отримує контекст розмови користувача"""
    conversations = load_json(CONVERSATIONS_FILE)
    user_conv = conversations.get(str(user_id), {})
    return user_conv.get("history", [])
async def save_conversation_step(user_id, user_message, bot_response, user_name):
    """Зберігає крок розмови"""
    conversations = load_json(CONVERSATIONS_FILE)
    if str(user_id) not in conversations:
        conversations[str(user_id)] = {
            "name": user_name,
            "history": []
        }
    conv = conversations[str(user_id)]
    conv["name"] = user_name
    conv["history"].extend([user_message, bot_response])
    # Зберігаємо тільки останні 10 повідомлень
    if len(conv["history"]) > 10:
        conv["history"] = conv["history"][-10:]
    save_json(CONVERSATIONS_FILE, conversations)
# === ФУНКЦІЇ ДЛЯ РОБОТИ З ДАНИМИ БОТА ===
def load_persistent_data():
    """Завантажує дані бота з файлу"""
    return load_json(DATA_FILE)
def save_persistent_data(data):
    """Зберігає дані бота у файл"""
    save_json(DATA_FILE, data)
async def post_init(application):
    """Ініціалізація бота при старті"""
    persistent_data = load_persistent_data()
    application.bot_data.update(persistent_data)
    print("Дані завантажено з файлу")
def save_bot_data(context: ContextTypes.DEFAULT_TYPE):
    """Зберігає поточні дані бота"""
    save_persistent_data(context.bot_data)
# === ФУНКЦІЇ ДЛЯ ПЕРЕВІРКИ ПРАВ АДМІНІСТРАТОРА ===
async def is_user_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """Перевіряє, чи є користувач адміністратором"""
    try:
        user = await context.bot.get_chat_member(chat_id, user_id)
        return user.status in ['administrator', 'creator']
    except:
        return False
# === ФУНКЦІЇ ДЛЯ РОБОТИ З ЧАСОМ ===
def parse_duration(duration_str: str) -> timedelta:
    """Перетворює строку типу 5h у timedelta"""
    match = re.match(r"(\d+)([mhw])", duration_str)
    if not match:
        raise ValueError("Неправильний формат часу. Використовуйте: 10m, 2h, 1w")
    value, unit = int(match.group(1)), match.group(2)
    if unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'w':
        return timedelta(weeks=value)
    else:
        raise ValueError("Неправильний формат часу.")
# === ФУНКЦІЇ ДЛЯ АВТОМАТИЧНОГО РОЗМУТУ ===
async def auto_unmute_callback(context: ContextTypes.DEFAULT_TYPE):
    """Функція, викликається по закінченню часу мута."""
    job = context.job
    chat_id = job.data['chat_id']
    user_id = job.data['user_id']
    username = job.data['username']
    bot = context.bot
    try:
        # Розмут користувача
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=False
            )
        )
        # Видаляємо зі списку замучених
        muted_data = context.bot_data.get("muted_users", {}).get(str(chat_id), {})
        if str(user_id) in muted_data:
            del muted_data[str(user_id)]
            save_persistent_data(context.bot_data)
        # Відправляємо повідомлення в чат
        unmute_msg = f"⏰ Таймер мута @{username} завершено. Кляп знято автоматично."
        await bot.send_message(chat_id=chat_id, text=unmute_msg)
        print(f"✅ Автоматично розмучено користувача {username} (ID: {user_id}) в чаті {chat_id}")
    except Exception as e:
        print(f"⚠️ Помилка при автоматичному розмуті {username} (ID: {user_id}) в чаті {chat_id}: {e}")
# === ВСПОМОГАТЕЛЬНІ ФУНКЦІЇ ===
async def safe_delete_message(chat_id: int, message_id: int, bot):
    """Безпечне видалення повідомлення з обробкою помилок."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        print(f"🗑️ Повідомлення {message_id} видалено з чату {chat_id}")
    except Exception as e:
        print(f"⚠️ Не вдалося видалити повідомлення {message_id} з чату {chat_id}: {e}")
async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int):
    """Планує видалення повідомлення через певний час."""
    context.job_queue.run_once(
        callback=lambda ctx: asyncio.create_task(safe_delete_message(chat_id, message_id, ctx.bot)),
        when=delay,
        data={'chat_id': chat_id, 'message_id': message_id}
    )
# === КОМАНДИ БОТА ===
# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    if update.effective_chat.type != "private":
        # Запланувати видалення команди через 10 секунд
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        return
    # Перевірка чи є користувач адміном хоча б в одній групі
    user_id = update.effective_user.id
    groups = context.bot_data.get("groups", {})
    is_admin_anywhere = False
    for group_id in groups:
        if await is_user_admin(context, int(group_id) if isinstance(group_id, str) else group_id, user_id):
            is_admin_anywhere = True
            break
    if not is_admin_anywhere:
        msg = await update.message.reply_text("Я впихну кляп тобі, якщо продовжиш тикати.")
        await schedule_message_deletion(context, update.effective_chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        return
    keyboard = [
        [InlineKeyboardButton("Мути 🔇", callback_data="show_groups")],
        [InlineKeyboardButton("Gemini Персона 🤖", callback_data="gemini_personality")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text("Привіт! Я бот для управління мутами.", reply_markup=reply_markup)
    await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
    # msg (повідомлення бота) не видаляється
# Команда /date - створення анкети
async def date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /date - створення анкети користувача"""
    user = update.effective_user
    chat = update.effective_chat
    if not context.args:
        msg = await update.message.reply_text("""Використовуй: /date Ім'я, вік, цілі, інтереси
Приклад: /date Сергій, 25 років. Тут по фану!""")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    profile_text = " ".join(context.args)
    # Зберігаємо анкету
    profiles = context.bot_data.setdefault("profiles", {})
    profiles[str(user.id)] = {
        "username": user.username,
        "first_name": user.first_name,
        "profile": profile_text,
        "created_at": datetime.now().isoformat()
    }
    # Зберігаємо на диск
    save_bot_data(context)
    # msg = await update.message.reply_text(f"@{user.username or user.first_name} Радий знайомству! Інформацію зберіг. Отримати інформацію інших користувачів через /who @username або дай відповідь на повідомлення цієї людини.")
    # await schedule_message_deletion(context, chat.id, msg.message_id, 10)
    # await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # return
    await update.message.reply_text(f"@{user.username or user.first_name} Радий знайомству! Інформацію зберіг. Отримати інформацію інших користувачів через /who @username або дай відповідь на повідомлення цієї людини.")
    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # msg (повідомлення бота) не видаляється
# Команда /who - перегляд анкети
async def who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /who - перегляд анкети іншого користувача"""
    user = update.effective_user
    chat = update.effective_chat
    target_user = None
    # Якщо є відповідь на повідомлення
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    # Якщо вказано @username
    elif context.args:
        username = context.args[0].lstrip('@')
        # Шукаємо користувача в profiles
        profiles = context.bot_data.get("profiles", {})
        for user_id, profile_data in profiles.items():
            if profile_data.get("username") == username:
                # Створюємо фейковий об'єкт користувача
                from telegram import User
                target_user = User(id=int(user_id), first_name=profile_data.get("first_name", ""), username=username, is_bot=False)
                break
        else:
            msg = await update.message.reply_text("Користувача не знайдено або у нього немає анкети.")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
    else:
        msg = await update.message.reply_text("Використовуй: /who @username або дай відповідь на повідомлення користувача.")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    if not target_user:
        msg = await update.message.reply_text("Не вдалося отримати інформацію про користувача.")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    # Отримуємо анкету
    profiles = context.bot_data.get("profiles", {})
    user_profile = profiles.get(str(target_user.id))
    if not user_profile:
        msg = await update.message.reply_text("У цього користувача немає анкети.")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    profile_text = user_profile.get("profile", "Немає інформації")
    username = target_user.username or target_user.first_name
    response = f"👤 @{username}\n{profile_text}"
    # msg = await update.message.reply_text(response)
    # await schedule_message_deletion(context, chat.id, msg.message_id, 10)
    # await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # return
    await update.message.reply_text(response)
    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # msg (повідомлення бота) не видаляється
# Команда /muty
async def muty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /muty - показ списку мутів"""
    user_id = update.effective_user.id
    chat = update.effective_chat
    if not chat:
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    # В приватному чаті - показуємо групи
    if chat.type == "private":
        # Перевірка чи є користувач адміном хоча б в одній групі
        groups = context.bot_data.get("groups", {})
        user_groups = []
        for group_id_str, group_data in groups.items():
            group_id = int(group_id_str) if isinstance(group_id_str, str) else group_id_str
            if await is_user_admin(context, group_id, user_id):
                user_groups.append((group_id, group_data["title"]))
        if not user_groups:
            msg = await update.message.reply_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
        buttons = []
        for group_id, title in user_groups:
            buttons.append([InlineKeyboardButton(title, callback_data=f"group_mutes_{group_id}")])
        if not buttons:
            msg = await update.message.reply_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
        reply_markup = InlineKeyboardMarkup(buttons)
        msg = await update.message.reply_text("Оберіть групу:", reply_markup=reply_markup)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
    # В групі - показуємо мутів цієї групи (тільки для адмінів)
    else:
        # Перевірка прав адміна
        if not await is_user_admin(context, chat.id, user_id):
            msg = await update.message.reply_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
        # Додаємо групу до списку, якщо її там немає
        groups = context.bot_data.setdefault("groups", {})
        if str(chat.id) not in groups:
            groups[str(chat.id)] = {"title": chat.title or f"Група {chat.id}"}
            # Зберігаємо на диск
            save_bot_data(context)
        muted_users = []
        try:
            muted_list = context.bot_data.get("muted_users", {}).get(str(chat.id), {})
            for user_id_str, data in muted_list.items():
                user_id_int = int(user_id_str) if isinstance(user_id_str, str) else user_id_str
                muted_users.append((user_id_int, data['username']))
        except Exception as e:
            msg = await update.message.reply_text(f"Помилка: {e}")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
        if not muted_users:
            msg = await update.message.reply_text("Немає замучених користувачів.")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
        buttons = []
        for user_id, username in muted_users:
            buttons.append([InlineKeyboardButton(f"@{username}", callback_data=f"unmute_confirm_{user_id}_{chat.id}")])
        reply_markup = InlineKeyboardMarkup(buttons)
        msg = await update.message.reply_text("Список кляпів:", reply_markup=reply_markup)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
# Команда /mute
async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /mute - замутити користувача"""
    if not update.message or not update.message.from_user:
        return
    admin_user = update.message.from_user
    chat = update.effective_chat
    if not chat:
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    # Перевірка прав адміна
    if not await is_user_admin(context, chat.id, admin_user.id):
        msg = await update.message.reply_text("Я впихну кляп тобі, якщо продовжиш тикати.")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    user_to_mute = None
    reason = ""
    duration_str = ""
    # Спроба знайти користувача через відповідь
    if update.message.reply_to_message:
        try:
            user_to_mute = await context.bot.get_chat_member(chat.id, update.message.reply_to_message.from_user.id)
            if user_to_mute is None:
                msg = await update.message.reply_text("Користувача не знайдено.")
                await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                return
            args = context.args
            if args:
                duration_str = args[0]
                reason = " ".join(args[1:]) if len(args) > 1 else ""
            else:
                msg = await update.message.reply_text("Вкажіть тривалість муту (наприклад: 5h).")
                await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                return
        except Exception as e:
            msg = await update.message.reply_text(f"Помилка: {e}")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
    else:
        if not context.args:
            msg = await update.message.reply_text("Вкажіть користувача або відповідайте на повідомлення.")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
        first_arg = context.args[0]
        if first_arg.startswith('@') or first_arg.isdigit():
            try:
                user_to_mute = await context.bot.get_chat_member(chat.id, first_arg.lstrip('@'))
                if user_to_mute is None:
                    msg = await update.message.reply_text("Користувача не знайдено або він не у чаті.")
                    await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                    return
                remaining_args = context.args[1:]
                if not remaining_args:
                    msg = await update.message.reply_text("Вкажіть тривалість муту (наприклад: 5h).")
                    await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                    return
                duration_str = remaining_args[0]
                reason = " ".join(remaining_args[1:]) if len(remaining_args) > 1 else ""
            except Exception as e:
                msg = await update.message.reply_text(f"Помилка: {e}")
                await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                return
        else:
            if len(context.args) < 2:
                msg = await update.message.reply_text("Перший аргумент має бути @username або ID.")
                await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                return
            for i in range(1, len(context.args)):
                arg = context.args[i]
                if arg.startswith('@') or arg.isdigit():
                    try:
                        user_to_mute = await context.bot.get_chat_member(chat.id, arg.lstrip('@'))
                        if user_to_mute is None:
                            msg = await update.message.reply_text("Користувача не знайдено або він не у чаті.")
                            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                            return
                        duration_str = context.args[0]
                        reason_args = []
                        for j in range(i + 1, len(context.args)):
                            reason_args.append(context.args[j])
                        reason = " ".join(reason_args)
                        break
                    except Exception:
                        continue
            else:
                msg = await update.message.reply_text("Не вдалося знайти користувача.")
                await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                return
    try:
        duration = parse_duration(duration_str)
    except ValueError as e:
        msg = await update.message.reply_text(str(e))
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    until_time = datetime.now(timezone.utc) + duration
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_to_mute.user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_time
        )
        # Зберігаємо в bot_data
        muted_data = context.bot_data.setdefault("muted_users", {}).setdefault(str(chat.id), {})
        muted_data[str(user_to_mute.user.id)] = {
            "username": user_to_mute.user.username or user_to_mute.user.first_name,
            "until": until_time.isoformat()
        }
        # Зберігаємо на диск
        save_bot_data(context)
        # Додаємо групу до списку, якщо її там немає
        groups = context.bot_data.setdefault("groups", {})
        if str(chat.id) not in groups:
            groups[str(chat.id)] = {"title": chat.title or f"Група {chat.id}"}
            # Зберігаємо на диск
            save_bot_data(context)
        # --- Планування автоматичного розмуту ---
        try:
            job_data = {
                'chat_id': chat.id,
                'user_id': user_to_mute.user.id,
                'username': user_to_mute.user.username or user_to_mute.user.first_name
            }
            context.job_queue.run_once(
                callback=auto_unmute_callback,
                when=until_time,
                data=job_data,
                name=f"unmute_{chat.id}_{user_to_mute.user.id}"
            )
            print(f"⏰ Заплановано автоматичний розмут для {job_data['username']} в {until_time}")
        except Exception as e:
            print(f"⚠️ Помилка при плануванні авто-розмуту для {user_to_mute.user.username or user_to_mute.user.first_name}: {e}")
        gif_url = "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExYzNiaXo0YTZod2J0NmUzOXJ5Ymtid3ZpMGcxMjUxMTZxY2dybjJmOSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/snCdBOKXIgIf2perjF/giphy.gif"
        mute_message = f"@{user_to_mute.user.username or user_to_mute.user.first_name}, кляп встановлено @{admin_user.username or admin_user.first_name}! Не балуй, хлопчику!"
        msg = await update.message.reply_animation(animation=gif_url, caption=mute_message)
        # Сповіщаємо адмінів в боті
        try:
            # Отримуємо всіх адмінів
            admins = await context.bot.get_chat_administrators(chat.id)
            # Формуємо повідомлення
            mute_msg = f"🔇 @{admin_user.username or admin_user.first_name} замутив @{user_to_mute.user.username or user_to_mute.user.first_name}"
            if reason:
                mute_msg += f"\n📝 Причина: {reason}"
            # Додаємо посилання на повідомлення (якщо є)
            if update.message.reply_to_message:
                # Для супергруп ID починається з -100
                chat_id_for_link = str(chat.id)[4:] if str(chat.id).startswith('-100') else chat.id
                msg_link = f"https://t.me/c/{chat_id_for_link}/{update.message.reply_to_message.message_id}"
                mute_msg += f"\n🔗 Повідомлення: {msg_link}"
            # Відправляємо адмінам у приват
            for admin in admins:
                if admin.user.is_bot:
                    continue
                try:
                    await context.bot.send_message(chat_id=admin.user.id, text=mute_msg)
                except:
                    pass  # Якщо не можемо відправити — ігноруємо
        except Exception as e:
            print(f"Помилка при сповіщенні адмінів: {e}")
        # Авто-видалення повідомлень
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
    except Exception as e:
        msg = await update.message.reply_text(f"Помилка: {e}")
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
# Команда /unmute
async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /unmute - розмутити користувача"""
    if not update.message or not update.message.from_user:
        return
    admin_user = update.message.from_user
    chat = update.effective_chat
    if not chat:
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    # Перевірка прав адміна
    if not await is_user_admin(context, chat.id, admin_user.id):
        msg = await update.message.reply_text("Я впихну кляп тобі, якщо продовжиш тикати.")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    user_to_unmute = None
    if context.args:
        username_or_id = context.args[0].lstrip('@')
        try:
            user_to_unmute = await context.bot.get_chat_member(chat.id, username_or_id)
            if user_to_unmute is None:
                msg = await update.message.reply_text("Користувача не знайдено.")
                await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                return
        except Exception as e:
            msg = await update.message.reply_text(f"Помилка: {e}")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
    elif update.message.reply_to_message:
        try:
            user_to_unmute = await context.bot.get_chat_member(chat.id, update.message.reply_to_message.from_user.id)
            if user_to_unmute is None:
                msg = await update.message.reply_text("Користувача не знайдено.")
                await schedule_message_deletion(context, chat.id, msg.message_id, 10)
                await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
                return
        except Exception as e:
            msg = await update.message.reply_text(f"Помилка: {e}")
            await schedule_message_deletion(context, chat.id, msg.message_id, 10)
            await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
            return
    else:
        msg = await update.message.reply_text("Вкажіть користувача або відповідайте на повідомлення.")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_to_unmute.user.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=False
            )
        )
        # Видаляємо зі списку
        muted_data = context.bot_data.get("muted_users", {}).get(str(chat.id), {})
        if str(user_to_unmute.user.id) in muted_data:
            del muted_data[str(user_to_unmute.user.id)]
            # Зберігаємо на диск
            save_bot_data(context)
        unmute_message = f"@{user_to_unmute.user.username or user_to_unmute.user.first_name}, кляп видалено @{admin_user.username or admin_user.first_name}, не змушуй робити це ще раз!"
        msg = await update.message.reply_text(unmute_message)
        # Авто-видалення повідомлень
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
    except Exception as e:
        msg = await update.message.reply_text(f"Помилка: {e}")
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
# Команда /sky - чат з AI
async def sky(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /sky - чат з штучним інтелектом"""
    if not GEMINI_API_KEY:
        msg = await update.message.reply_text("Gemini API не налаштовано.")
        await schedule_message_deletion(context, update.effective_chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        return
    user = update.effective_user
    message_text = " ".join(context.args) if context.args else ""
    # Якщо немає тексту - привітання
    if not message_text:
        greeting = (
            "Привіт=) Я SkyNet! Доглядач за чатом TOP BOYS CHAT. "
            "Мене можна питати про все (завдяки Gemini). "
            "Людина, не порушуй правила чату і поважай адміністрацію, "
            "або я вирахую і знайду тебе (жартую, напевно). "
            "- Чим можу допомогти? Про що поговоримо?"
        )
        msg = await update.message.reply_text(greeting)
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
        return
    try:
        # Отримуємо персоналізацію
        personality = context.bot_data.get("gemini_personality", "")
        # Отримуємо історію розмови
        history = await get_conversation_context(user.id)
        # Формуємо запит з персоналізацією та історією
        full_prompt = f"{personality}\n"
        if history:
            full_prompt += "Попередня розмова:\n" + "\n".join(history) + "\n"
        full_prompt += f"Користувач ({await get_user_name(user)}): {message_text}\nАсистент:"
        # Отримуємо відповідь від Gemini
        response = model.generate_content(full_prompt)
        reply_text = response.text.strip()
        # Зберігаємо крок розмови
        await save_conversation_step(
            user_id=user.id,
            user_message=message_text,
            bot_response=reply_text,
            user_name=await get_user_name(user)
        )
        msg = await update.message.reply_text(reply_text)
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
    except Exception as e:
        error_msg = f"Помилка при зверненні до AI: {str(e)}"
        msg = await update.message.reply_text(error_msg)
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        print(error_msg)
        # msg (повідомлення бота) не видаляється
# Команда /my_pepper - показує розмір вашої линейки
async def my_pepper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /my_pepper - показує розмір вашої линейки."""
    user = update.effective_user
    chat = update.effective_chat
    if not chat or chat.type not in ['group', 'supergroup']:
        msg = await update.message.reply_text("🥺 Солоденький, ця команда працює тільки в групах!")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    reputations = context.bot_data.get("reputations", {})
    user_rep_key = f"{chat.id}_{user.id}"
    current_length = reputations.get(user_rep_key, 0)
    user_name = user.username or user.first_name
    msg = await update.message.reply_text(f"@{user_name}, ваша линейка {current_length} сантиметрів! 🫡")
    # Запланувати видалення повідомлення з розміром линейки через 10 секунд
    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # msg (повідомлення бота) не видаляється
# Команда /pepper - показує топ 3 линейки
async def pepper_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /pepper - показывает топ 3 пользователей по линейкам в чате."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ['group', 'supergroup']:
        msg = await update.message.reply_text("🥺 Солоденький, ця команда працює тільки в групах!")
        await schedule_message_deletion(context, chat.id, msg.message_id, 10)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return

    reputations = context.bot_data.get("reputations", {})
    # Фильтруем репутации только для текущего чата
    chat_reps = {
        key.split('_')[1]: length  # Виправлено: використовуємо 'key' замість 'user_id'
        for key, length in reputations.items()
        if key.startswith(f"{chat.id}_") and isinstance(length, (int, float))
    }
    if not chat_reps:
        msg = await update.message.reply_text("У цьому чаті ще немає линеек 😢")
        # Запланувати видалення через 5 хвилин
        await schedule_message_deletion(context, chat.id, msg.message_id, 300)
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return

    # Сортируем по убыванию длины линейки і берем тільки топ-3
    sorted_reps = sorted(chat_reps.items(), key=lambda item: item[1], reverse=True)[:3] # Тільки топ-3
    # Формируем текст рейтинга
    leaderboard_lines = ["🏆 Топ 3 Линейки цього чату:"]
    for i, (user_id_str, length) in enumerate(sorted_reps):
        # Пытаемся получить имя пользователя из чата
        try:
            member = await context.bot.get_chat_member(chat.id, int(user_id_str))
            user_name = member.user.username or member.user.first_name
            display_name = f"@{user_name}" if member.user.username else user_name
        except:
            # Если не удалось получить, отображаем ID
            display_name = f"Користувач {user_id_str}"
        # Добавляем эмодзи для первых мест
        if i == 0:
            place = "🥇"
        elif i == 1:
            place = "🥈"
        elif i == 2:
            place = "🥉"
        else:
            place = f"{i+1}."
        leaderboard_lines.append(f"{place} {display_name}: {length} см")

    leaderboard_text = "\n".join(leaderboard_lines) # Виправлено форматування

    # Отправляем сообщение
    msg = await update.message.reply_text(leaderboard_text)
    # Запланувати видалення повідомлення з рейтингом через 5 хвилин (300 секунд)
    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # msg (повідомлення бота) не видаляється

# --- Система репутації (Линейка) ---
async def handle_plus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обробляє повідомлення з "+" у відповіді. Підвищує репутацію (линейка) отримувача.
    """
    # Отримуємо чат, щоб перевірити тип
    chat = update.effective_chat
    if not chat or chat.type not in ['group', 'supergroup']:
        # Не обробляємо в приватних чатах
        return
    # Користувач, який надіслав "+"
    giver = update.effective_user
    # Повідомлення, на яке відповіли (тобто отримувач "+")
    replied_to_message = update.message.reply_to_message
    receiver = replied_to_message.from_user
    # Не можна давати собі "+" 
    if giver.id == receiver.id:
        return
    # --- Логіка репутації ---
    # 1. Отримати поточний розмір линейки отримувача
    reputations = context.bot_data.setdefault("reputations", {})
    user_rep_key = f"{chat.id}_{receiver.id}"
    current_length = reputations.get(user_rep_key, 0)
    # 2. Збільшити на 1
    new_length = current_length + 1
    # 3. Зберегти нове значення
    reputations[user_rep_key] = new_length
    save_bot_data(context) # Зберігаємо зміни
    # 4. Створити повідомлення
    giver_name = giver.username or giver.first_name
    receiver_name = receiver.username or receiver.first_name
    response_text = (
        f"@{giver_name} весело посміхаючись збільшив твою линейку на 1 сантиметр 😋 @{receiver_name}, "
        f"продовжуй пупсик себе добре поводити і відрощуй свою линейку.\n"
        f"Ваша линейка {new_length} сантиметрів!"
    )
    # 5. Відправити повідомлення у відповідь
    msg = await update.message.reply_text(response_text)
    # 6. Запланувати видалення повідомлення про линейку через 10 секунд
    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # msg (повідомлення бота) не видаляється
async def handle_minus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обробляє повідомлення з "-" у відповіді. Знижує репутацію (линейка) отримувача.
    """
    # Отримуємо чат, щоб перевірити тип
    chat = update.effective_chat
    if not chat or chat.type not in ['group', 'supergroup']:
        # Не обробляємо в приватних чатах
        return
    # Користувач, який надіслав "-"
    giver = update.effective_user
    # Повідомлення, на яке відповіли (тобто отримувач "-")
    replied_to_message = update.message.reply_to_message
    receiver = replied_to_message.from_user
    # Не можна давати собі "-" 
    if giver.id == receiver.id:
        return
    # --- Логіка репутації ---
    # 1. Отримати поточний розмір линейки отримувача
    reputations = context.bot_data.setdefault("reputations", {})
    user_rep_key = f"{chat.id}_{receiver.id}"
    current_length = reputations.get(user_rep_key, 0)
    # 2. Зменшити на 1 (але не нижче 0)
    new_length = max(current_length - 1, 0)
    # 3. Зберегти нове значення
    reputations[user_rep_key] = new_length
    save_bot_data(context) # Зберігаємо зміни
    # 4. Створити повідомлення
    giver_name = giver.username or giver.first_name
    receiver_name = receiver.username or receiver.first_name
    response_text = (
        f"@{giver_name} засмучено зменшив твою линейку на 1 сантиметр 😞 @{receiver_name}, "
        f"надіюсь, це тільки тимчасово.\n"
        f"Ваша линейка {new_length} сантиметрів!"
    )
    # 5. Відправити повідомлення у відповідь
    msg = await update.message.reply_text(response_text)
    # 6. Запланувати видалення повідомлення про линейку через 10 секунд
    await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
    # msg (повідомлення бота) не видаляється
# Обробник відповідей на повідомлення бота або згадок
IGNORED_COMMANDS = {"/mute", "/muty", "/ban", "/alert", "/report", "/date", "/who"}
# --- ИСПРАВЛЕННАЯ ФУНКЦИЯ handle_reply_or_mention ---
async def handle_reply_or_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает прямые упоминания, ответы на сообщения бота и ответы на сообщения участников с упоминанием."""
    # print(f"DEBUG: handle_reply_or_mention triggered for message ID {update.message.message_id if update.message else 'N/A'}")
    if not update.message or not update.message.from_user:
        # print("DEBUG: No message or user, returning.")
        return
    message_text = update.message.text
    if not message_text:
        # print("DEBUG: No message text, returning.")
        return
    user = update.message.from_user
    chat = update.effective_chat
    bot_username = context.bot.username
    # --- Логика определения необходимости обработки ---
    should_process = False
    replied_to_text = ""
    context_for_gemini = ""
    is_reply_scenario = False # Флаг, чтобы знать, нужно ли добавлять контекст ответа
    # 1. Проверка на прямое упоминание (не ответ)
    is_direct_mention = (
        f"@{bot_username}" in message_text and
        not update.message.reply_to_message
    )
    # print(f"DEBUG: is_direct_mention = {is_direct_mention}")
    # 2. Проверка на ответ на сообщение БОТА (с упоминанием или без)
    is_reply_to_bot = False
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        replied_to_user = update.message.reply_to_message.from_user
        # Проверяем, является ли автор сообщения, на которое отвечают, нашим ботом
        if replied_to_user.is_bot and replied_to_user.id == context.bot.id:
            is_reply_to_bot = True
            is_reply_scenario = True
            # Получаем текст сообщения, на которое ответили
            replied_to_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
            # print(f"DEBUG: is_reply_to_bot = True, replied_to_text = '{replied_to_text}'")
    # print(f"DEBUG: is_reply_to_bot = {is_reply_to_bot}")
    # 3. Проверка на ответ на сообщение УЧАСТНИКА с упоминанием бота
    is_reply_to_user_with_mention = False
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        replied_to_user = update.message.reply_to_message.from_user
        # Проверяем, что это НЕ бот и есть упоминание бота в тексте
        if not replied_to_user.is_bot and f"@{bot_username}" in message_text:
            is_reply_to_user_with_mention = True
            is_reply_scenario = True
            # Получаем текст сообщения, на которое ответили
            replied_to_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
            replied_user_name = await get_user_name(replied_to_user)
            # print(f"DEBUG: is_reply_to_user_with_mention = True, replied_to_text = '{replied_to_text}', user = {replied_user_name}")
    # print(f"DEBUG: is_reply_to_user_with_mention = {is_reply_to_user_with_mention}")
    # Определяем, нужно ли обрабатывать это сообщение
    if is_direct_mention or is_reply_to_bot or is_reply_to_user_with_mention:
        should_process = True
        # print("DEBUG: should_process = True")
    else:
        # print("DEBUG: should_process = False")
        pass # Игнорируем сообщение, не соответствующее критериям
    # --- Если сообщение нужно обработать ---
    if should_process:
        # print("DEBUG: Processing the message...")
        if not GEMINI_API_KEY:
            error_msg = "Gemini API не налаштовано."
            print(error_msg)
            error_reply = await update.message.reply_text(error_msg)
            await schedule_message_deletion(context, chat.id, error_reply.message_id, 10)
            # Не удаляем сообщение пользователя, которое он написал боту
            return
        try:
            # Очищаем текст запроса от упоминания бота (если оно было)
            clean_query_text = message_text.replace(f"@{bot_username}", "").strip()
            # print(f"DEBUG: Cleaned query text: '{clean_query_text}'")
            # Формируем запрос для Gemini
            personality = context.bot_data.get("gemini_personality", "")
            history = await get_conversation_context(user.id)
            # Создаем контекст для ИИ
            context_for_gemini = f"{personality}\n"
            if history:
                context_for_gemini += "Попередня розмова:\n" + "\n".join(history) + "\n"
            # Если это сценарий ответа (на бота или на участника), добавляем контекст
            if is_reply_scenario and replied_to_text:
                if is_reply_to_bot:
                     context_for_gemini += f"[Відповідь на повідомлення бота: {replied_to_text}]\n"
                elif is_reply_to_user_with_mention:
                     context_for_gemini += f"[Відповідь на повідомлення від {replied_user_name}: {replied_to_text}]\n"
            # Добавляем запрос пользователя
            user_name = await get_user_name(user)
            context_for_gemini += f"Користувач ({user_name}): {clean_query_text}\nАсистент:"
            # print(f"DEBUG: Final prompt to Gemini:\n{context_for_gemini}\n---END---")
            # Получаем ответ от Gemini
            response = model.generate_content(context_for_gemini)
            reply_text = response.text.strip()
            # print(f"DEBUG: Gemini response: '{reply_text}'")
            # Сохраняем шаг разговора
            # Для ответов на участников с упоминанием сохраняем контекст
            user_message_to_save = clean_query_text
            if is_reply_to_user_with_mention and replied_to_text:
                 user_message_to_save = f"[Про повідомлення '{replied_to_text}' від {replied_user_name}] {clean_query_text}"
            elif is_reply_to_bot and replied_to_text:
                 user_message_to_save = f"[Відповідь на '{replied_to_text}'] {clean_query_text}"
            await save_conversation_step(
                user_id=user.id,
                user_message=user_message_to_save,
                bot_response=reply_text,
                user_name=user_name
            )
            # Отправляем ответ
            # Сообщение бота НЕ удаляется
            await update.message.reply_text(reply_text)
            # Сообщение пользователя НЕ удаляется, так как оно адресовано боту или является продолжением диалога
        except Exception as e:
            error_msg = f"Помилка при зверненні до AI: {str(e)}"
            print(error_msg) # Логируем ошибку
            # Отправляем сообщение об ошибке
            error_reply = await update.message.reply_text(error_msg)
            await schedule_message_deletion(context, chat.id, error_reply.message_id, 10)
            # Сообщение пользователя НЕ удаляется
        # ВАЖНО: Возвращаемся, чтобы не продолжать обработку другими хендлерами
        return
    # --- Если сообщение не нужно обрабатывать ---
    # Проверим, не является ли оно командой из списка игнорируемых для удаления
    first_word_cmd = message_text.split(maxsplit=1)[0].split('@')[0].lower()
    if first_word_cmd in IGNORED_COMMANDS:
        await schedule_message_deletion(context, chat.id, update.message.message_id, 10)
        return
    # Если не соответствует ни одному критерию, просто игнорируем.
    # print("DEBUG: Message did not match any processing criteria, ignoring.")
    return # Явный return для ясности
# Обробник кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    # Кнопка "Мути" в /start
    if query.data == "show_groups":
        # Перевірка чи є користувач адміном хоча б в одній групі
        groups = context.bot_data.get("groups", {})
        user_groups = []
        for group_id_str, group_data in groups.items():
            group_id = int(group_id_str) if isinstance(group_id_str, str) else group_id_str
            if await is_user_admin(context, group_id, user_id):
                user_groups.append((group_id, group_data["title"]))
        if not user_groups:
            await query.edit_message_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            return
        buttons = []
        for group_id, title in user_groups:
            buttons.append([InlineKeyboardButton(title, callback_data=f"group_mutes_{group_id}")])
        if not buttons:
            await query.edit_message_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            return
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text("Оберіть групу:", reply_markup=reply_markup)
        # query.message (повідомлення бота) не видаляється
    # Кнопка "Gemini Персона"
    elif query.data == "gemini_personality":
        personality = context.bot_data.get("gemini_personality", "")
        await query.edit_message_text(
            f"Поточна персона Gemini:\n{personality}\nВведіть новий опис персони:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main")]])
        )
        # Зберігаємо стан, що очікуємо введення персони
        context.user_data["waiting_for_personality"] = True
        # query.message (повідомлення бота) не видаляється
    # Назад до головного меню
    elif query.data == "back_to_main":
        keyboard = [
            [InlineKeyboardButton("Мути 🔇", callback_data="show_groups")],
            [InlineKeyboardButton("Gemini Персона 🤖", callback_data="gemini_personality")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Привіт! Я бот для управління мутами.", reply_markup=reply_markup)
        # query.message (повідомлення бота) не видаляється
    # Обрано групу для перегляду мутів
    elif query.data.startswith("group_mutes_"):
        chat_id = int(query.data.split("_")[-1])
        # Перевірка чи є користувач адміном цієї групи
        if not await is_user_admin(context, chat_id, user_id):
            await query.edit_message_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            return
        muted_users = []
        try:
            muted_list = context.bot_data.get("muted_users", {}).get(str(chat_id), {})
            for user_id_str, data in muted_list.items():
                user_id_int = int(user_id_str) if isinstance(user_id_str, str) else user_id_str
                muted_users.append((user_id_int, data['username']))
        except Exception as e:
            await query.edit_message_text(f"Помилка: {e}")
            return
        if not muted_users:
            text = "Немає замучених користувачів."
            await query.edit_message_text(text)
            return
        buttons = []
        for user_id, username in muted_users:
            buttons.append([InlineKeyboardButton(f"@{username}", callback_data=f"unmute_confirm_{user_id}_{chat_id}")])
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text("Список кляпів:", reply_markup=reply_markup)
        # query.message (повідомлення бота) не видаляється
    # Підтвердження розмуту
    elif query.data.startswith("unmute_confirm_"):
        parts = query.data.split("_")
        user_id_to_unmute = int(parts[2])
        chat_id = int(parts[3])
        # Перевірка чи є користувач адміном цієї групи
        if not await is_user_admin(context, chat_id, query.from_user.id):
            await query.edit_message_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            return
        try:
            user = await context.bot.get_chat_member(chat_id, user_id_to_unmute)
            username = user.user.username or user.user.first_name
            confirm_button = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Так, зняти кляп", callback_data=f"confirm_unmute_{user_id_to_unmute}_{chat_id}")],
                [InlineKeyboardButton("❌ Скасувати", callback_data=f"group_mutes_{chat_id}")]])
            await query.edit_message_text(
                f"Зняти кляп з @{username}?",
                reply_markup=confirm_button
            )
            # query.message (повідомлення бота) не видаляється
        except Exception as e:
            await query.edit_message_text(f"Помилка: {e}")
    # Підтверджено розмут
    elif query.data.startswith("confirm_unmute_"):
        parts = query.data.split("_")
        user_id_to_unmute = int(parts[2])
        chat_id = int(parts[3])
        # Перевірка чи є користувач адміном цієї групи
        if not await is_user_admin(context, chat_id, query.from_user.id):
            await query.edit_message_text("Я впихну кляп тобі, якщо продовжиш тикати.")
            return
        try:
            user = await context.bot.get_chat_member(chat_id, user_id_to_unmute)
            username = user.user.username or user.user.first_name
            admin_username = query.from_user.username or query.from_user.first_name
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id_to_unmute,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=True,
                    can_pin_messages=False
                )
            )
            # Видаляємо зі списку
            muted_data = context.bot_data.get("muted_users", {}).get(str(chat_id), {})
            if str(user_id_to_unmute) in muted_data:
                del muted_data[str(user_id_to_unmute)]
                # Зберігаємо на диск
                save_bot_data(context)
            # Відправляємо повідомлення в групу
            unmute_msg = f"@{username}, кляп видалено @{admin_username}, не змушуй робити це ще раз!"
            await context.bot.send_message(chat_id=chat_id, text=unmute_msg)
            await query.edit_message_text(f"Кляп знятий з @{username}!")
            # query.message (повідомлення бота) не видаляється
        except Exception as e:
            await query.edit_message_text(f"Помилка: {e}")
# Обробник текстових повідомлень (для введення персони Gemini)
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє текстові повідомлення"""
    # Якщо очікуємо введення персони
    if context.user_data.get("waiting_for_personality"):
        personality = update.message.text
        context.bot_data["gemini_personality"] = personality
        save_bot_data(context)
        context.user_data["waiting_for_personality"] = False
        msg = await update.message.reply_text("Персона оновлена!")
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        # msg (повідомлення бота) не видаляється
        # Показуємо головне меню
        keyboard = [
            [InlineKeyboardButton("Мути 🔇", callback_data="show_groups")],
            [InlineKeyboardButton("Gemini Персона 🤖", callback_data="gemini_personality")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        menu_msg = await update.message.reply_text("Привіт! Я бот для управління мутами.", reply_markup=reply_markup)
        await schedule_message_deletion(context, update.effective_chat.id, update.message.message_id, 10)
        # menu_msg (повідомлення бота) не видаляється
# Відстеження груп через будь-які повідомлення
async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Відстежує групи, де бот є"""
    chat = update.effective_chat
    if chat and chat.type in ['group', 'supergroup']:
        groups = context.bot_data.setdefault("groups", {})
        groups[str(chat.id)] = {"title": chat.title or f"Група {chat.id}"}
        # Зберігаємо при зміні
        save_bot_data(context)
# --- ИЗМЕНЕНИЯ В main() ---
def main():
    """Головна функція бота"""
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    # Команди для знакомств
    app.add_handler(CommandHandler("date", date))
    app.add_handler(CommandHandler("who", who))
    # Команди для мут-системи
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("muty", muty))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    # Команда для AI
    app.add_handler(CommandHandler("sky", sky))
    # Команди для репутації
    app.add_handler(CommandHandler("my_pepper", my_pepper))
    app.add_handler(CommandHandler("pepper", pepper_leaderboard))
    # Обработчики для системы репутации (+/-) - они должны идти после, 
    # так как они обрабатывают специфичные сообщения
    app.add_handler(MessageHandler(filters.REPLY & filters.Regex(r'^\+$'), handle_plus), group=1) 
    app.add_handler(MessageHandler(filters.REPLY & filters.Regex(r'^-$'), handle_minus), group=1) 
    # Обработчики
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler), group=2) # group=2, если text_handler должен обрабатывать то, что не поймал handle_reply_or_mention
    # === ВАЖНО: Порядок добавления обработчиков ===
    # handle_reply_or_mention должен быть первым или почти первым, 
    # чтобы иметь возможность обработать сообщение до других обработчиков.
    # Используем group=0 (по умолчанию самый высокий приоритет) для этого обработчика.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_or_mention), group=0) 
    # track_chats - отслеживание чатов, должно идти позже
    app.add_handler(MessageHandler(filters.ALL, track_chats), group=3) 
    print("🟢 Бот запущений!")
    app.run_polling()
if __name__ == '__main__':
    main()
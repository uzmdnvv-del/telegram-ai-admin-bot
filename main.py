from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackContext, CommandHandler, MessageHandler, filters

# Load environment variables
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
APP_URL = os.getenv("APP_URL")

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            username TEXT,
            user_message TEXT,
            ai_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
        )
        """
    )
    conn.commit()
    conn.close()


def register_user_if_needed(telegram_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str]) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            "SELECT telegram_id FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        if cursor.fetchone() is None:
            conn.execute(
                "INSERT INTO users (telegram_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                (telegram_id, username, first_name, last_name),
            )
            conn.commit()
            print(f"New user registered: {telegram_id} | @{username} | {first_name} {last_name}")
    finally:
        conn.close()


def get_all_users() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT telegram_id, username, first_name, last_name, created_at FROM users ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_user_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count


def get_user_messages(telegram_id: int) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, user_message, ai_response, created_at FROM messages WHERE telegram_id = ? ORDER BY id DESC LIMIT 10",
        (telegram_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


async def send_admin_notification(user_data: dict) -> None:
    if not ADMIN_TELEGRAM_ID:
        return
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        await app.initialize()
        await app.bot.send_message(
            chat_id=ADMIN_TELEGRAM_ID,
            text=(
                "🆕 Yangi foydalanuvchi qo‘shildi\n\n"
                f"Ism: {user_data.get('first_name') or 'Noma’lum'}\n"
                f"Familiya: {user_data.get('last_name') or 'Noma’lum'}\n"
                f"Username: @{user_data.get('username') or 'yo‘q'}\n"
                f"Telegram ID: {user_data.get('telegram_id')}"
            ),
        )
        await app.shutdown()
    except Exception as e:
        print(f"Failed to notify admin: {e}")


async def send_admin_summary() -> None:
    if not ADMIN_TELEGRAM_ID:
        return
    users = get_all_users()
    text = "👨‍💼 Admin panel\n\n"
    text += f"Umumiy foydalanuvchilar: {len(users)}\n\n"
    if users:
        for u in users[:10]:
            text += (
                f"- {u['first_name'] or 'Noma’lum'} {u['last_name'] or ''} | "
                f"@{u['username'] or 'yo‘q'} | ID: {u['telegram_id']}\n"
            )
    else:
        text += "Hozircha foydalanuvchi yo‘q."
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        await app.initialize()
        await app.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=text)
        await app.shutdown()
    except Exception as e:
        print(f"Failed to send admin summary: {e}")


async def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user is None:
        return

    telegram_id = user.id
    username = user.username
    first_name = user.first_name
    last_name = user.last_name

    register_user_if_needed(telegram_id, username, first_name, last_name)

    user_data = {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
    }

    await send_admin_notification(user_data)

    welcome = (
        "Salom! 👋\n\n"
        "Men sizga Telegram orqali savollarga javob beraman.\n"
        "Xohlagan savolingizni yozing.\n\n"
        "Ayniqsa, men Gemini AI yordamida javob beraman."
    )
    await update.message.reply_text(welcome)


async def handle_message(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user is None or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    register_user_if_needed(user.id, user.username, user.first_name, user.last_name)

    ai_reply = await generate_ai_reply(text)

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (telegram_id, username, user_message, ai_response) VALUES (?, ?, ?, ?)",
        (user.id, user.username, text, ai_reply),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(ai_reply, parse_mode=ParseMode.MARKDOWN)


async def admin_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user is None:
        return

    if user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("Bu buyruq faqat admin uchun! ❌")
        return

    users = get_all_users()
    count = len(users)
    text = f"👨‍💼 Admin panel\n\nFoydalanuvchilar soni: {count}\n\n"
    if users:
        for u in users:
            text += (
                f"• {u['first_name'] or 'Noma’lum'} {u['last_name'] or ''} | "
                f"@{u['username'] or 'yo‘q'} | ID: {u['telegram_id']}\n"
            )
    else:
        text += "Hozircha foydalanuvchi yo‘q."

    await update.message.reply_text(text)


async def generate_ai_reply(prompt: str) -> str:
    if not GEMINI_API_KEY:
        return "Botga Gemini API kaliti ulanmagan. Iltimos .env faylga GEMINI_API_KEY qo‘shing."

    # Placeholder simple AI response with no external dependency to keep the project runnable.
    # Replace this with real Gemini API integration when ready.
    response = (
        f"Sizning savolingiz: {prompt}\n\n"
        "Bu test javob. AI integratsiyasi uchun Gemini API kalitini to‘g‘ri sozlang va "
        "generate_ai_reply() funksiyasini real API chaqiruvi bilan almashtiring."
    )
    return response


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi. .env faylga BOT_TOKEN yozing.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot ishga tushdi...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

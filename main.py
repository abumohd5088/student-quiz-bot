#!/usr/bin/env python3
import os
import sys
import logging
import asyncio
import signal
from datetime import date
from typing import Dict, List, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from groq import Groq
import json
import random
import sqlite3

# ─── CONFIG ───────────────────────────────────────────────────────
TG_BOT_TOKEN    = os.environ.get("TG_BOT_TOKEN", "")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL      = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
DATABASE_PATH   = "bot_database.db"

BOT_NAME        = "Student Quiz Bot"
XP_PER_LEVEL    = 100

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── DATABASE ─────────────────────────────────────────────────────
class Database:
    def __init__(self, path: str):
        self.path = path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, query: str, params=()):
        with self._connect() as conn:
            conn.execute(query, params)
            conn.commit()

    def fetch_one(self, query: str, params=()) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params=()) -> List[Dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def _init_db(self):
        queries = [
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                streak INTEGER DEFAULT 0,
                last_active TEXT,
                class_level INTEGER DEFAULT 10
            )""",
            """CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                content TEXT
            )""",
        ]
        for q in queries:
            self.execute(q)

    def get_or_create_user(self, user_id: int, username: str = None, first_name: str = None) -> Dict:
        user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not user:
            self.execute(
                "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username, first_name)
            )
            user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return user or {}

    def add_xp(self, user_id: int, amount: int) -> Dict:
        user = self.fetch_one("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
        if not user:
            return {"xp": amount, "level": 1}

        xp = user["xp"] + amount
        level = user["level"]

        while xp >= level * XP_PER_LEVEL:
            xp -= level * XP_PER_LEVEL
            level += 1

        self.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, user_id))
        return {"xp": xp, "level": level}

    def update_streak(self, user_id: int) -> int:
        user = self.fetch_one("SELECT streak, last_active FROM users WHERE user_id = ?", (user_id,))
        if not user:
            streak = 1
        else:
            last = user["last_active"]
            if not last:
                streak = 1
            else:
                days = (date.today() - date.fromisoformat(last)).days
                if days == 1:
                    streak = user["streak"] + 1
                elif days > 1:
                    streak = 1
                else:
                    streak = user["streak"]

        today = date.today().isoformat()
        self.execute("UPDATE users SET streak = ?, last_active = ? WHERE user_id = ?", (streak, today, user_id))
        return streak

    def save_note(self, user_id: int, title: str, content: str) -> int:
        self.execute("INSERT INTO notes (user_id, title, content) VALUES (?, ?, ?)", (user_id, title, content))
        row = self.fetch_one("SELECT last_insert_rowid() as id")
        return row["id"] if row else -1

# ─── AI ───────────────────────────────────────────────────────────
class AI:
    def __init__(self):
        self.client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
        self.fallback = [
            {
                "question": "What is 8 × 7?",
                "options": ["48", "54", "56", "64"],
                "correct_index": 2,
                "explanation": "8 × 7 = 56"
            },
            {
                "question": "Capital of Japan?",
                "options": ["Seoul", "Tokyo", "Beijing", "Bangkok"],
                "correct_index": 1,
                "explanation": "Tokyo is the capital."
            },
        ]

    def get_quiz(self, subject: str = "general") -> Dict:
        if not self.client:
            return random.choice(self.fallback)

        try:
            prompt = f"""Generate one {subject} quiz question for high-school level.
Return **only** JSON:
{{
  "question": "...",
  "options": ["A", "B", "C", "D"],
  "correct_index": 0-3,
  "explanation": "..."
}}"""
            resp = self.client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300
            )
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1].strip()
            return json.loads(text)
        except Exception:
            return random.choice(self.fallback)


# ─── BOT ──────────────────────────────────────────────────────────
class QuizBot:
    def __init__(self):
        self.db = Database(DATABASE_PATH)
        self.ai = AI()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.get_or_create_user(user.id, user.username, user.first_name)
        await update.message.reply_text(
            f"Welcome *{user.first_name}* to {BOT_NAME} 🎒\nUse /help to see commands.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "*Commands:*\n"
            "/start — welcome\n"
            "/quiz — random quiz\n"
            "/dailyquiz — streak quiz\n"
            "/study <topic> — explanation\n"
            "/profile — your stats\n"
            "/savenote <title> <text> — save note\n"
            "/notes — list notes"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = self.ai.get_quiz()
        await update.message.reply_poll(
            question=q["question"],
            options=q["options"],
            type="quiz",
            correct_option_id=q["correct_index"],
            explanation=q["explanation"],
            is_anonymous=False
        )
        self.db.add_xp(update.effective_user.id, 10)

    async def dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = random.choice(self.ai.fallback)
        streak = self.db.update_streak(update.effective_user.id)
        await update.message.reply_poll(
            question=f"Daily Quiz\n{q['question']}",
            options=q["options"],
            type="quiz",
            correct_option_id=q["correct_index"],
            explanation=q["explanation"],
            is_anonymous=False
        )
        await update.message.reply_text(f"🔥 Streak: *{streak}* days", parse_mode=ParseMode.MARKDOWN_V2)

    async def study(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /study topic")
            return
        topic = " ".join(context.args)
        await update.message.reply_text(f"Explaining *{topic}* … (placeholder)", parse_mode=ParseMode.MARKDOWN_V2)
        self.db.add_xp(update.effective_user.id, 5)

    async def profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.fetch_one("SELECT * FROM users WHERE user_id = ?", (update.effective_user.id,))
        if not user:
            await update.message.reply_text("No profile yet.")
            return
        text = f"*Profile*\nLevel: {user['level']}\nXP: {user['xp']}\nStreak: {user['streak']}"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def savenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /savenote title content...")
            return
        title = context.args[0]
        content = " ".join(context.args[1:])
        nid = self.db.save_note(update.effective_user.id, title, content)
        await update.message.reply_text(f"Note saved (#{nid})")

    async def notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        notes = self.db.fetch_all("SELECT title FROM notes WHERE user_id = ? LIMIT 5", (update.effective_user.id,))
        if not notes:
            await update.message.reply_text("No notes yet.")
            return
        text = "*Your notes:*\n" + "\n".join(f"• {n['title']}" for n in notes)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def main():
    if not TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN missing")
        sys.exit(1)

    application = (
        Application.builder()
        .token(TG_BOT_TOKEN)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .build()
    )

    bot = QuizBot()

    # Register commands
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("quiz", bot.quiz))
    application.add_handler(CommandHandler("dailyquiz", bot.dailyquiz))
    application.add_handler(CommandHandler("study", bot.study))
    application.add_handler(CommandHandler("profile", bot.profile))
    application.add_handler(CommandHandler("savenote", bot.savenote))
    application.add_handler(CommandHandler("notes", bot.notes))

    # Graceful shutdown
    loop = asyncio.get_running_loop()

    async def shutdown():
        logger.info("Shutting down...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

    def handle_sig(signum, frame):
        logger.info(f"Received signal {signum}")
        loop.create_task(shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_sig, sig, None)

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,
            poll_interval=0.5,
            timeout=20
        )
        logger.info("Bot is running...")
        await asyncio.Event().wait()
    except Exception as e:
        logger.exception("Fatal error", exc_info=e)
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())

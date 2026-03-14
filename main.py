#!/usr/bin/env python3
import os
import sys
import logging
import sqlite3
import random
from datetime import datetime, date

from telegram import Update, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# Configuration from environment variables
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    print("ERROR: TG_BOT_TOKEN environment variable is not set!")
    sys.exit(1)

DATABASE_PATH = "bot_database.db"
XP_PER_LEVEL = 100

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_database()
    
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    def execute_safe(self, query, params=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            conn.commit()
            return cursor
        except Exception as e:
            conn.rollback()            logger.error(f"DB Error: {e}")
            raise
        finally:
            conn.close()

    def fetch_one(self, query, params=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def fetch_all(self, query, params=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    def _init_database(self):
        queries = [
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                streak INTEGER DEFAULT 0,
                last_active DATE,
                joined_date DATE DEFAULT (date('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS quizzes (
                quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                question TEXT,
                options TEXT,
                correct_index INTEGER,
                explanation TEXT,
                is_correct BOOLEAN DEFAULT 0,                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        ]
        
        for query in queries:
            self.execute_safe(query)
        logger.info("Database initialized successfully")
    
    def get_or_create_user(self, user_id, username=None, first_name=None):
        user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not user:
            self.execute_safe(
                "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username, first_name)
            )
            user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return user if user else {}
    
    def add_xp(self, user_id, xp_amount):
        user = self.fetch_one("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
        if not user:
            return {"xp": xp_amount, "level": 1}
        
        new_xp = user["xp"] + xp_amount
        new_level = user["level"]
        
        while new_xp >= (new_level * XP_PER_LEVEL):
            new_level += 1
        
        self.execute_safe(
            "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
            (new_xp, new_level, user_id)
        )
        return {"xp": new_xp, "level": new_level}
    
    def update_streak(self, user_id):
        user = self.fetch_one("SELECT streak, last_active FROM users WHERE user_id = ?", (user_id,))
        if not user:
            self.execute_safe(
                "UPDATE users SET streak = 1, last_active = ? WHERE user_id = ?",
                (date.today().isoformat(), user_id)
            )
            return {"streak": 1}
        
        current_streak = user["streak"]
        if user["last_active"]:
            last_date = datetime.strptime(user["last_active"], "%Y-%m-%d").date()
            days_diff = (date.today() - last_date).days
            if days_diff == 1:
                current_streak += 1            elif days_diff > 1:
                current_streak = 1
        else:
            current_streak = 1
        
        self.execute_safe(
            "UPDATE users SET streak = ?, last_active = ? WHERE user_id = ?",
            (current_streak, date.today().isoformat(), user_id)
        )
        return {"streak": current_streak}
    
    def get_user_stats(self, user_id):
        return self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    
    def get_leaderboard(self, limit=10):
        return self.fetch_all(
            "SELECT user_id, username, first_name, xp, level FROM users ORDER BY xp DESC LIMIT ?",
            (limit,)
        )


class QuizBot:
    def __init__(self):
        self.db = DatabaseManager(DATABASE_PATH)
        self.app = None
        logger.info("QuizBot initializing...")
    
    async def initialize(self):
        self.app = Application.builder().token(TG_BOT_TOKEN).build()
        self._register_handlers()
        logger.info("Bot initialized successfully")
    
    def _register_handlers(self):
        handlers = {
            "start": self.cmd_start,
            "help": self.cmd_help,
            "ping": self.cmd_ping,
            "quiz": self.cmd_quiz,
            "dailyquiz": self.cmd_dailyquiz,
            "profile": self.cmd_profile,
            "leaderboard": self.cmd_leaderboard,
        }
        
        for cmd, handler in handlers.items():
            self.app.add_handler(CommandHandler(cmd, handler))
        
        self.app.add_error_handler(self.error_handler)
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user        self.db.get_or_create_user(user.id, user.username, user.first_name)
        
        text = (
            f"🎓 *Welcome to Student Quiz Bot!*\n\n"
            f"Hello *{user.first_name}*!\n\n"
            f"Available commands:\n"
            f"/quiz - Start a practice quiz\n"
            f"/dailyquiz - Daily challenge\n"
            f"/profile - Your stats\n"
            f"/leaderboard - Top students\n"
            f"/help - All commands\n\n"
            f"Let's learn! 📚"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "*📚 Available Commands:*\n\n"
            "*Quizzes:*\n"
            "/quiz - Random quiz question\n"
            "/dailyquiz - Daily challenge (+streak!)\n\n"
            "*Progress:*\n"
            "/profile - Your stats & level\n"
            "/leaderboard - Top 10 students\n\n"
            "*Other:*\n"
            "/start - Welcome message\n"
            "/ping - Check bot status\n"
            "/help - This help message"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🏓 *Pong!* Bot is online!", parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        questions = [
            {
                "q": "What is 15 + 27?",
                "options": ["40", "42", "45", "52"],
                "a": 1,
                "e": "15 + 27 = 42"
            },
            {
                "q": "Capital of France?",
                "options": ["London", "Berlin", "Paris", "Madrid"],
                "a": 2,
                "e": "Paris is the capital of France"
            },
            {
                "q": "H2O is?",                "options": ["Salt", "Sugar", "Water", "Oxygen"],
                "a": 2,
                "e": "H2O is the chemical formula for water"
            },
            {
                "q": "What is 7 × 8?",
                "options": ["54", "56", "63", "48"],
                "a": 1,
                "e": "7 × 8 = 56"
            }
        ]
        
        q = random.choice(questions)
        
        await update.message.reply_poll(
            question=q["q"],
            options=q["options"],
            type="quiz",
            correct_option_id=q["a"],
            explanation=q["e"],
            is_anonymous=False
        )
        
        user_id = update.effective_user.id
        self.db.add_xp(user_id, 5)
    
    async def cmd_dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        streak = self.db.update_streak(user_id)
        
        questions = [
            {
                "q": "Largest planet in solar system?",
                "options": ["Earth", "Mars", "Jupiter", "Saturn"],
                "a": 2,
                "e": "Jupiter is the largest planet"
            },
            {
                "q": "What is 100 ÷ 4?",
                "options": ["20", "25", "30", "15"],
                "a": 1,
                "e": "100 ÷ 4 = 25"
            }
        ]
        
        q = random.choice(questions)
        
        await update.message.reply_poll(
            question=f"🌟 Daily Quiz (Streak: {streak['streak']}!): {q['q']}",
            options=q["options"],            type="quiz",
            correct_option_id=q["a"],
            explanation=q["e"],
            is_anonymous=False
        )
        
        xp_gain = 10 + (streak['streak'] * 2)
        self.db.add_xp(user_id, xp_gain)
        await update.message.reply_text(f"✨ +{xp_gain} XP! Keep it up! 🔥")
    
    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        stats = self.db.get_user_stats(user_id)
        
        if not stats:
            await update.message.reply_text("❌ Profile not found. Use /start first.")
            return
        
        level = stats.get("level", 1)
        xp = stats.get("xp", 0)
        xp_for_next = level * XP_PER_LEVEL
        progress = (xp / xp_for_next) * 100 if xp_for_next > 0 else 0
        
        text = (
            f"👤 *Your Profile*\n\n"
            f"📊 Level: {level} ({xp}/{xp_for_next} XP)\n"
            f"📈 Progress: {progress:.0f}%\n"
            f"🔥 Streak: {stats.get('streak', 0)} days\n"
            f"📅 Member since: {stats.get('joined_date', 'N/A')}"
        )
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        top = self.db.get_leaderboard(10)
        
        if not top:
            await update.message.reply_text("📭 No users yet. Be the first!")
            return
        
        text = "🏆 *Leaderboard - Top Students*\n\n"
        
        for i, u in enumerate(top, 1):
            name = u["username"] or u["first_name"] or f"User{u['user_id']}"
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} *{name}*: {u['xp']} XP (Lvl {u['level']})\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):        logger.error(f"Update {update} caused error: {context.error}")
    
    async def run(self):
        await self.initialize()
        logger.info("Starting bot...")
        await self.app.run_polling(drop_pending_updates=True)


async def main():
    bot = QuizBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

#!/usr/bin/env python3
import os
import sys
import json
import logging
import sqlite3
import threading
import asyncio
import random
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import NetworkError

from groq import Groq
from flask import Flask, jsonify
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ─── CONFIGURATION ────────────────────────────────────────────────
TG_BOT_TOKEN   = os.environ.get("TG_BOT_TOKEN", "")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL     = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
ADMIN_USER_ID  = int(os.environ.get("ADMIN_USER_ID", "0"))
TARGET_GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
PORT           = int(os.environ.get("PORT", 8080))
DATABASE_PATH  = "bot_database.db"

BOT_NAME    = "Student Quiz Bot"
BOT_VERSION = "3.0"
XP_PER_LEVEL = 100

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── DATABASE MANAGER ─────────────────────────────────────────────
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def execute_safe(self, query: str, params=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"DB Error: {e}")
            raise
        finally:
            conn.close()

    def fetch_one(self, query: str, params=None) -> Optional[Dict]:
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

    def fetch_all(self, query: str, params=None) -> List[Dict]:
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
                joined_date DATE DEFAULT (date('now')),
                class_level INTEGER DEFAULT 10,
                total_quizzes INTEGER DEFAULT 0,
                correct_answers INTEGER DEFAULT 0,
                language TEXT DEFAULT 'en'
            )""",
            """CREATE TABLE IF NOT EXISTS quizzes (
                quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                question TEXT,
                options TEXT,
                correct_index INTEGER,
                explanation TEXT,
                subject TEXT,
                is_correct BOOLEAN DEFAULT 0,
                time_taken INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                content TEXT,
                subject TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS homework (
                homework_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject TEXT,
                task TEXT,
                due_date DATE,
                is_completed BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS reminders (
                reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                reminder_time TEXT,
                message TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        ]

        for q in queries:
            self.execute_safe(q)
        logger.info("Database initialized successfully")

    def get_or_create_user(self, user_id: int, username=None, first_name=None) -> Dict:
        user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not user:
            self.execute_safe(
                "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username, first_name)
            )
            user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return user or {}

    def add_xp(self, user_id: int, xp_amount: int) -> Dict:
        user = self.fetch_one("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
        if not user:
            return {"xp": xp_amount, "level": 1}

        new_xp = user["xp"] + xp_amount
        new_level = user["level"]

        while new_xp >= (new_level * XP_PER_LEVEL):
            new_xp -= (new_level * XP_PER_LEVEL)
            new_level += 1

        self.execute_safe(
            "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
            (new_xp, new_level, user_id)
        )
        return {"xp": new_xp, "level": new_level}

    def update_streak(self, user_id: int) -> Dict:
        user = self.fetch_one("SELECT streak, last_active FROM users WHERE user_id = ?", (user_id,))
        if not user:
            return {"streak": 1}

        current_streak = user["streak"]
        if user["last_active"]:
            last_date = datetime.strptime(user["last_active"], "%Y-%m-%d").date()
            days_diff = (date.today() - last_date).days
            if days_diff == 1:
                current_streak += 1
            elif days_diff > 1:
                current_streak = 1
        else:
            current_streak = 1

        self.execute_safe(
            "UPDATE users SET streak = ?, last_active = ? WHERE user_id = ?",
            (current_streak, date.today().isoformat(), user_id)
        )
        return {"streak": current_streak}

    def get_user_stats(self, user_id: int) -> Dict:
        user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not user:
            return {}

        stats = self.fetch_one(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct
               FROM quizzes WHERE user_id = ?""",
            (user_id,)
        )
        total = stats["total"] if stats else 0
        correct = stats["correct"] if stats else 0

        user["quiz_stats"] = {"total": total, "correct": correct}
        user["accuracy"] = round(correct / total * 100, 1) if total > 0 else 0
        return user

    def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        return self.fetch_all(
            "SELECT user_id, username, first_name, xp, level FROM users ORDER BY xp DESC LIMIT ?",
            (limit,)
        )

    def is_banned(self, user_id: int) -> bool:
        res = self.fetch_one("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        return res is not None

    def save_note(self, user_id: int, title: str, content: str, subject: str = "general") -> int:
        self.execute_safe(
            "INSERT INTO notes (user_id, title, content, subject) VALUES (?, ?, ?, ?)",
            (user_id, title, content, subject)
        )
        row = self.fetch_one("SELECT last_insert_rowid() as id")
        return row["id"] if row else -1

    def get_notes(self, user_id: int) -> List[Dict]:
        return self.fetch_all("SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC", (user_id,))

    def add_homework(self, user_id: int, subject: str, task: str, due_date: str) -> int:
        self.execute_safe(
            "INSERT INTO homework (user_id, subject, task, due_date) VALUES (?, ?, ?, ?)",
            (user_id, subject, task, due_date)
        )
        row = self.fetch_one("SELECT last_insert_rowid() as id")
        return row["id"] if row else -1

    def get_pending_homework(self, user_id: int) -> List[Dict]:
        return self.fetch_all(
            "SELECT * FROM homework WHERE user_id = ? AND is_completed = 0 ORDER BY due_date",
            (user_id,)
        )

    def complete_homework(self, user_id: int, homework_id: int) -> bool:
        self.execute_safe(
            "UPDATE homework SET is_completed = 1 WHERE homework_id = ? AND user_id = ?",
            (homework_id, user_id)
        )
        return True

# ─── AI MANAGER ───────────────────────────────────────────────────
class AIManager:
    def __init__(self, api_key: str, model: str):
        self.client = Groq(api_key=api_key) if api_key else None
        self.model = model
        self.fallback_questions = [
            {"question": "What is 15 + 27?", "options": ["40", "42", "45", "52"], "correct_index": 1, "explanation": "15 + 27 = 42"},
            {"question": "Capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"], "correct_index": 2, "explanation": "Paris is the capital of France."},
            {"question": "H₂O is?", "options": ["Salt", "Sugar", "Water", "Oxygen"], "correct_index": 2, "explanation": "H₂O is water."},
        ]

    def generate_quiz_question(self, subject: str, class_level: int, difficulty: str = "medium") -> Dict:
        if not self.client:
            return random.choice(self.fallback_questions)

        try:
            prompt = f"""Generate a {difficulty} level {subject} quiz question suitable for class {class_level} students.
Return **only** valid JSON in this exact format:
{{
  "question": "the question text",
  "options": ["opt1", "opt2", "opt3", "opt4"],
  "correct_index": 0-based index of correct answer,
  "explanation": "short explanation why it is correct"
}}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.7
            )
            content = response.choices[0].message.content.strip()
            if content.startswith("```json"):
                content = content.split("```json")[1].split("```")[0].strip()
            return json.loads(content)
        except Exception as e:
            logger.warning(f"Groq question generation failed: {e}")
            return random.choice(self.fallback_questions)

    def explain_concept(self, concept: str, class_level: int) -> str:
        if not self.client:
            return f"*{concept}*: Please refer to your textbook for a detailed explanation."

        try:
            prompt = f"Explain the concept '{concept}' in simple language suitable for a class {class_level} student. Use clear examples. Keep it concise."
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.6
            )
            return response.choices[0].message.content
        except Exception:
            return f"*{concept}* is an important topic. Try checking your notes or textbook!"

# ─── FLASK HEALTH CHECK ───────────────────────────────────────────
def create_flask_app() -> Flask:
    app = Flask(__name__)

    @app.route('/')
    def home():
        return jsonify({"bot": BOT_NAME, "status": "running", "version": BOT_VERSION})

    @app.route('/health')
    def health():
        return jsonify({"status": "healthy"}), 200

    @app.route('/ping')
    def ping():
        return jsonify({"status": "pong"}), 200

    return app

# ─── MAIN BOT CLASS ───────────────────────────────────────────────
class QuizBot:
    def __init__(self):
        self.db = DatabaseManager(DATABASE_PATH)
        self.ai = AIManager(GROQ_API_KEY, GROQ_MODEL)
        self.app: Optional[Application] = None
        self.scheduler: Optional[AsyncIOScheduler] = None
        logger.info(f"{BOT_NAME} v{BOT_VERSION} initializing...")

    async def initialize(self):
        if not TG_BOT_TOKEN:
            logger.error("TG_BOT_TOKEN is not set!")
            sys.exit(1)

        self.app = Application.builder().token(TG_BOT_TOKEN).build()
        self._register_handlers()
        self._setup_scheduler()
        self._start_flask()
        logger.info("Bot handlers and scheduler initialized")

    def _register_handlers(self):
        commands = {
            "start": self.cmd_start,
            "help": self.cmd_help,
            "ping": self.cmd_ping,
            "study": self.cmd_study,
            "explain": self.cmd_explain,
            "quiz": self.cmd_quiz,
            "dailyquiz": self.cmd_dailyquiz,
            "profile": self.cmd_profile,
            "leaderboard": self.cmd_leaderboard,
            "notes": self.cmd_notes,
            "savenote": self.cmd_savenote,
            "homework": self.cmd_homework,
            "word": self.cmd_word,
            "fact": self.cmd_fact,
            "calc": self.cmd_calc,
        }

        for cmd, handler in commands.items():
            self.app.add_handler(CommandHandler(cmd, handler))

        self.app.add_handler(CallbackQueryHandler(self.handle_callback))

    def _setup_scheduler(self):
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(self._daily_reminder, CronTrigger(hour=9, minute=0))
        self.scheduler.add_job(self._check_reminders, 'interval', minutes=1)
        self.scheduler.start()

    def _start_flask(self):
        flask_app = create_flask_app()
        def run_flask():
            flask_app.run(host='0.0.0.0', port=PORT, threaded=True, use_reloader=False)
        threading.Thread(target=run_flask, daemon=True).start()
        logger.info(f"Flask health server listening on port {PORT}")

    # ─── COMMAND HANDLERS ─────────────────────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if self.db.is_banned(user.id):
            await update.message.reply_text("Access restricted.")
            return

        self.db.get_or_create_user(user.id, user.username, user.first_name)
        text = f"*Welcome to {BOT_NAME}!* 🎓\n\nHello *{user.first_name}*!\nUse /help to see available commands."
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "*Available commands:*\n\n"
            "/start \\- Welcome message\n"
            "/help \\- This help\n"
            "/quiz \\[subject] \\- Get a quiz question\n"
            "/dailyquiz \\- Daily practice question\n"
            "/study <topic> \\- Learn about a topic\n"
            "/explain <concept> \\- Quick explanation\n"
            "/profile \\- Your stats\n"
            "/leaderboard \\- Top learners\n"
            "/savenote <title> <content> \\- Save a note\n"
            "/notes \\- List your notes\n"
            "/homework <sub> <task> <YYYY-MM-DD> \\- Add homework\n"
            "/homework list \\- Show pending tasks\n"
            "/word \\- Random English word\n"
            "/fact \\- Interesting fact\n"
            "/calc <expression> \\- Simple calculator"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("*Pong!* Bot is alive 🚀", parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_study(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /study <topic or subject>")
            return

        topic = " ".join(context.args)
        user = self.db.get_or_create_user(update.effective_user.id)
        class_level = user.get("class_level", 10)

        await update.message.chat.send_action("typing")
        explanation = self.ai.explain_concept(topic, class_level)
        await update.message.reply_text(explanation, parse_mode=ParseMode.MARKDOWN)

        self.db.add_xp(update.effective_user.id, 5)

    async def cmd_explain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /explain <concept>")
            return

        concept = " ".join(context.args)
        await update.message.chat.send_action("typing")
        explanation = self.ai.explain_concept(concept, 10)
        await update.message.reply_text(explanation, parse_mode=ParseMode.MARKDOWN)

    async def cmd_quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        subject = context.args[0] if context.args else "general"
        await update.message.chat.send_action("typing")

        q = self.ai.generate_quiz_question(subject, 10)

        await update.message.reply_poll(
            question=q.get("question", "Question?"),
            options=q.get("options", ["A", "B", "C", "D"]),
            type="quiz",
            correct_option_id=q.get("correct_index", 0),
            explanation=q.get("explanation", "No explanation provided."),
            is_anonymous=False,
            protect_content=True
        )

    async def cmd_dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.chat.send_action("typing")
        q = random.choice(self.ai.fallback_questions)

        streak = self.db.update_streak(update.effective_user.id)

        await update.message.reply_poll(
            question=f"Daily Quiz: {q['question']}",
            options=q["options"],
            type="quiz",
            correct_option_id=q["correct_index"],
            explanation=q["explanation"],
            is_anonymous=False
        )
        await update.message.reply_text(f"🔥 Current streak: *{streak['streak']}* days!", parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.db.get_user_stats(update.effective_user.id)
        text = (
            f"*Your Profile* 📊\n\n"
            f"Level: *{stats.get('level', 1)}*\n"
            f"XP: *{stats.get('xp', 0)}*\n"
            f"Streak: *{stats.get('streak', 0)}* days\n"
            f"Quizzes taken: *{stats.get('quiz_stats', {}).get('total', 0)}*\n"
            f"Accuracy: *{stats.get('accuracy', 0)}*% "
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        top = self.db.get_leaderboard(10)
        if not top:
            await update.message.reply_text("No users yet.")
            return

        lines = ["*Leaderboard* 🏆\n"]
        for i, u in enumerate(top, 1):
            name = u["username"] or u["first_name"] or f"User {u['user_id']}"
            lines.append(f"{i}. *{name}* – {u['xp']} XP (Lv. {u['level']})")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        notes = self.db.get_notes(update.effective_user.id)
        if not notes:
            await update.message.reply_text("You don't have any saved notes yet.")
            return

        text = "*Your Notes* 📝\n\n"
        for n in notes[:8]:  # limit display
            text += f"• *{n['title']}* ({n['subject']})\n"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_savenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /savenote <title> <content...>")
            return

        title = context.args[0]
        content = " ".join(context.args[1:])
        note_id = self.db.save_note(update.effective_user.id, title, content)
        await update.message.reply_text(f"Note saved successfully! ID: *#{note_id}*", parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_homework(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /homework <subject> <task> <YYYY-MM-DD>\n/homework list")
            return

        if context.args[0].lower() == "list":
            pending = self.db.get_pending_homework(update.effective_user.id)
            if not pending:
                await update.message.reply_text("No pending homework.")
                return
            text = "*Pending Homework* 📚\n\n"
            for h in pending:
                text += f"• *{h['subject']}* – {h['task']}  (due {h['due_date']})\n"
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if len(context.args) < 3:
            await update.message.reply_text("Not enough arguments. Need subject, task, due-date.")
            return

        subject = context.args[0]
        due_date = context.args[-1]
        task = " ".join(context.args[1:-1])

        hw_id = self.db.add_homework(update.effective_user.id, subject, task, due_date)
        await update.message.reply_text(f"Homework added! ID: *#{hw_id}*", parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_word(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        words = [
            {"w": "Ephemeral", "m": "lasting for a very short time"},
            {"w": "Resilient", "m": "able to withstand or recover quickly from difficult conditions"},
            {"w": "Eloquent", "m": "fluent or persuasive in speaking or writing"},
        ]
        w = random.choice(words)
        text = f"*Word of the moment*: {w['w']}\nMeaning: {w['m']}"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_fact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        facts = [
            "Honey never spoils — archaeologists found edible honey in ancient Egyptian tombs.",
            "The brain uses \~20% of the body's energy despite being only \~2% of body weight.",
            "Octopuses have three hearts.",
        ]
        await update.message.reply_text(f"✨ *Fact*: {random.choice(facts)}", parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_calc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /calc 3 + 4 * 2")
            return

        expr = " ".join(context.args)
        try:
            # Very restricted safe eval
            allowed_names = {"__builtins__": {}}
            result = eval(expr, allowed_names, {})
            await update.message.reply_text(f"`{expr}` = *{result}*", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"Calculation error: {str(e)}")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Placeholder — add callback handling later if needed (quiz tracking, buttons, etc.)
        await update.callback_query.answer()

    async def _daily_reminder(self):
        # Implement later: good morning + daily quiz or motivation
        pass

    async def _check_reminders(self):
        # Implement later: check reminder_time == current time
        pass


# ─── ENTRY POINT ──────────────────────────────────────────────────
async def main():
    bot = QuizBot()
    await bot.initialize()

    if bot.app:
        await bot.app.initialize()
        await bot.app.start()
        await bot.app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        logger.info("Bot polling started")

        # Keep running
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

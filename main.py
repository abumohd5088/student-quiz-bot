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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import NetworkError

from groq import Groq
from flask import Flask, jsonify
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# --- CONFIGURATION ---
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
TARGET_GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
PORT = int(os.environ.get("PORT", 8080))
DATABASE_PATH = "bot_database.db"

BOT_NAME = "Student Quiz Bot"
BOT_VERSION = "3.0"
XP_PER_LEVEL = 100

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- DATABASE MANAGER (No Decorators Used) ---
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_database()
    
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    # Replaced @contextmanager with explicit method to prevent syntax errors
    def execute_safe(self, query, params=None):        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            conn.commit()
            return cursor
        except Exception as e:
            conn.rollback()
            logger.error(f"DB Error: {e}")
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
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0,
                last_active DATE, joined_date DATE DEFAULT (date('now')),
                class_level INTEGER DEFAULT 10, total_quizzes INTEGER DEFAULT 0,
                correct_answers INTEGER DEFAULT 0, language TEXT DEFAULT 'en')""",
                        """CREATE TABLE IF NOT EXISTS quizzes (
                quiz_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                question TEXT, options TEXT, correct_index INTEGER,
                explanation TEXT, subject TEXT, is_correct BOOLEAN DEFAULT 0,
                time_taken INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            
            """CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                title TEXT, content TEXT, subject TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            
            """CREATE TABLE IF NOT EXISTS homework (
                homework_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                subject TEXT, task TEXT, due_date DATE,
                is_completed BOOLEAN DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            
            """CREATE TABLE IF NOT EXISTS reminders (
                reminder_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                reminder_time TEXT, message TEXT, is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            
            """CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY, reason TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        ]
        
        for q in queries:
            self.execute_safe(q)
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
                self.execute_safe("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (new_xp, new_level, user_id))
        return {"xp": new_xp, "level": new_level}
    
    def update_streak(self, user_id):
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
            
        self.execute_safe("UPDATE users SET streak = ? WHERE user_id = ?", (current_streak, user_id))
        return {"streak": current_streak}
    
    def get_user_stats(self, user_id):
        user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not user: return {}
        
        stats = self.fetch_one(
            "SELECT COUNT(*) as total, SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct FROM quizzes WHERE user_id = ?", 
            (user_id,)
        )
        total = stats.get("total", 0) if stats else 0
        correct = stats.get("correct", 0) if stats else 0
        
        user["quiz_stats"] = {"total": total, "correct": correct}
        user["accuracy"] = round(correct / total * 100, 1) if total > 0 else 0
        return user
    
    def get_leaderboard(self, limit=10):
        return self.fetch_all("SELECT user_id, username, first_name, xp, level FROM users ORDER BY xp DESC LIMIT ?", (limit,))
    
    def is_banned(self, user_id):
        res = self.fetch_one("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        return res is not None
    
    def save_note(self, user_id, title, content, subject="general"):
        self.execute_safe("INSERT INTO notes (user_id, title, content, subject) VALUES (?, ?, ?, ?)",
                         (user_id, title, content, subject))
        return self.fetch_one("SELECT last_insert_rowid() as id")['id']
    
    def get_notes(self, user_id):        return self.fetch_all("SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    
    def add_homework(self, user_id, subject, task, due_date):
        self.execute_safe("INSERT INTO homework (user_id, subject, task, due_date) VALUES (?, ?, ?, ?)",
                         (user_id, subject, task, due_date))
        return self.fetch_one("SELECT last_insert_rowid() as id")['id']
    
    def get_pending_homework(self, user_id):
        return self.fetch_all("SELECT * FROM homework WHERE user_id = ? AND is_completed = 0 ORDER BY due_date", (user_id,))
    
    def complete_homework(self, user_id, homework_id):
        self.execute_safe("UPDATE homework SET is_completed = 1 WHERE homework_id = ? AND user_id = ?", (homework_id, user_id))
        return True
    
    def add_reminder(self, user_id, reminder_time, message):
        self.execute_safe("INSERT INTO reminders (user_id, reminder_time, message) VALUES (?, ?, ?)",
                         (user_id, reminder_time, message))
        return self.fetch_one("SELECT last_insert_rowid() as id")['id']
    
    def get_active_reminders(self):
        current_time = datetime.now().strftime("%H:%M")
        return self.fetch_all("SELECT * FROM reminders WHERE reminder_time = ? AND is_active = 1", (current_time,))

# --- AI MANAGER ---
class AIManager:
    def __init__(self, api_key, model):
        self.client = Groq(api_key=api_key) if api_key else None
        self.model = model
        self.fallback_questions = [
            {"q": "What is 15 + 27?", "options": ["40", "42", "45", "52"], "a": 1, "e": "15 + 27 = 42"},
            {"q": "Capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"], "a": 2, "e": "Paris is capital"},
            {"q": "H2O is?", "options": ["Salt", "Sugar", "Water", "Oxygen"], "a": 2, "e": "H2O is water"},
        ]
    
    def generate_quiz_question(self, subject, class_level, difficulty="medium"):
        if not self.client:
            return random.choice(self.fallback_questions)
        try:
            prompt = f"Generate a {difficulty} {subject} quiz question for class {class_level}. Return JSON: {{'question': '', 'options': [], 'correct_index': 0, 'explanation': ''}}"
            response = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}], max_tokens=300, timeout=30
            )
            return json.loads(response.choices[0].message.content)
        except:
            return random.choice(self.fallback_questions)
    
    def explain_concept(self, concept, class_level):
        if not self.client:
            return f"*{concept}*: Check your textbook!"
        try:            prompt = f"Explain {concept} simply for class {class_level}."
            response = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}], max_tokens=300, timeout=30
            )
            return response.choices[0].message.content
        except:
            return f"*{concept}*: Important topic!"

# --- FLASK SERVER ---
def create_flask_app():
    app = Flask(__name__)
    @app.route('/')
    def home():
        return jsonify({"bot": BOT_NAME, "status": "running"})
    @app.route('/health')
    def health():
        return jsonify({"status": "healthy"}), 200
    @app.route('/ping')
    def ping():
        return jsonify({"status": "pong"}), 200
    return app

# --- MAIN BOT CLASS ---
class QuizBot:
    def __init__(self):
        self.db = DatabaseManager(DATABASE_PATH)
        self.ai = AIManager(GROQ_API_KEY, GROQ_MODEL)
        self.app = None
        self.scheduler = None
        logger.info(f"{BOT_NAME} v{BOT_VERSION} initializing...")
    
    async def initialize(self):
        self.app = Application.builder().token(TG_BOT_TOKEN).build()
        self._register_handlers()
        self._setup_scheduler()
        self._start_flask()
        logger.info("Bot initialized")
    
    def _register_handlers(self):
        cmds = {
            "start": self.cmd_start, "help": self.cmd_help, "ping": self.cmd_ping,
            "study": self.cmd_study, "explain": self.cmd_explain, "quiz": self.cmd_quiz,
            "dailyquiz": self.cmd_dailyquiz, "profile": self.cmd_profile,
            "leaderboard": self.cmd_leaderboard, "notes": self.cmd_notes,
            "savenote": self.cmd_savenote, "homework": self.cmd_homework,
            "word": self.cmd_word, "fact": self.cmd_fact, "calc": self.cmd_calc,
            "timer": self.cmd_timer, "admin": self.cmd_admin, "feedback": self.cmd_feedback
        }
        for cmd, handler in cmds.items():
            self.app.add_handler(CommandHandler(cmd, handler))        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
    
    def _setup_scheduler(self):
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(self._daily_reminder, CronTrigger(hour=9, minute=0))
        self.scheduler.add_job(self._check_reminders, 'interval', minutes=1)
    
    def _start_flask(self):
        flask_app = create_flask_app()
        def run():
            flask_app.run(host='0.0.0.0', port=PORT, threaded=True)
        threading.Thread(target=run, daemon=True).start()
        logger.info(f"Flask running on port {PORT}")
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if self.db.is_banned(user.id):
            await update.message.reply_text("Access restricted.")
            return
        self.db.get_or_create_user(user.id, user.username, user.first_name)
        text = f"*Welcome to {BOT_NAME}!*\n\nHello *{user.first_name}*!\n\n/start to begin.\n/help for commands."
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = "*Commands:*\n/study, /quiz, /dailyquiz, /profile, /leaderboard, /notes, /homework, /calc, /timer"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"*Pong!* Bot Online.", parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_study(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /study <topic>")
            return
        topic = " ".join(context.args)
        user = self.db.get_or_create_user(update.effective_user.id)
        await update.message.chat.send_action("typing")
        explanation = self.ai.explain_concept(topic, user.get("class_level", 10))
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
            question=q.get("q", q.get("question", "Question?")),
            options=q.get("options", ["A", "B", "C", "D"]),
            type="quiz",
            correct_option_id=q.get("a", q.get("correct_index", 0)),
            explanation=q.get("e", q.get("explanation", "")),
            is_anonymous=False
        )
    
    async def cmd_dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.chat.send_action("typing")
        q = random.choice(self.ai.fallback_questions)
        streak = self.db.update_streak(update.effective_user.id)
        await update.message.reply_poll(
            question=f"Daily Quiz: {q['q']}",
            options=q["options"],
            type="quiz",
            correct_option_id=q["a"],
            explanation=q["e"],
            is_anonymous=False
        )
        await update.message.reply_text(f"Streak: {streak['streak']} days!")
    
    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.db.get_user_stats(update.effective_user.id)
        text = f"*Profile*\nLevel: {stats.get('level', 1)}\nXP: {stats.get('xp', 0)}\nStreak: {stats.get('streak', 0)}"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        top = self.db.get_leaderboard(10)
        text = "*Leaderboard*\n"
        for i, u in enumerate(top, 1):
            name = u["username"] or u["first_name"] or f"User{u['user_id']}"
            text += f"{i}. *{name}*: {u['xp']} XP\n"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        notes = self.db.get_notes(update.effective_user.id)
        if not notes:
            await update.message.reply_text("No notes yet.")
            return
        text = "*Your Notes:*\n"
        for n in notes[:5]:
            text += f"- *{n['title']}*\n"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        async def cmd_savenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /savenote <title> <content>")
            return
        title = context.args[0]
        content = " ".join(context.args[1:])
        note_id = self.db.save_note(update.effective_user.id, title, content)
        await update.message.reply_text(f"Note saved! ID: #{note_id}")
    
    async def cmd_homework(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.args and context.args[0] != "list":
            if len(context.args) < 3:
                await update.message.reply_text("Usage: /homework <sub> <task> <date>")
                return
            hw_id = self.db.add_homework(update.effective_user.id, context.args[0], " ".join(context.args[1:-1]), context.args[-1])
            await update.message.reply_text(f"Homework added! ID: #{hw_id}")
            return
        pending = self.db.get_pending_homework(update.effective_user.id)
        if not pending:
            await update.message.reply_text("No pending homework!")
            return
        text = "*Pending Homework:*\n"
        for h in pending:
            text += f"- *{h['subject']}*: {h['task']}\n"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_word(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        words = [{"w": "Ephemeral", "m": "Short lived"}, {"w": "Resilient", "m": "Tough"}]
        w = random.choice(words)
        await update.message.reply_text(f"*Word:* {w['w']}\nMeaning: {w['m']}", parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_fact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        facts = ["Brain uses 20% energy!", "Honey never spoils!"]
        await update.message.reply_text(f"Fact: {random.choice(facts)}")
    
    async def cmd_calc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /calc <expr>")
            return
        expr = " ".join(context.args)
        try:
            result = eval(expr, {"__builtins__": {}}, {})
            await update.message.reply_text(f"{expr} = *{result}*", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
    
    async def cmd_timer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /timer <mins>")
            return        try:
            mins = int(context.args[0])
            await update.message.reply_text(f"Timer set for {mins} mins!")
            async def notify():
                await asyncio.sleep(mins * 60)
                await update.message.reply_text("Time's up!")
            asyncio.create_task(notify())
        except:
            await update.message.reply_text("Invalid number")
    
    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("Admin only!")
            return
        await update.message.reply_text(f"*Admin Panel*")
    
    async def cmd_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /feedback <msg>")
            return
        msg = " ".join(context.args)
        if ADMIN_USER_ID:
            try:
                await context.bot.send_message(ADMIN_USER_ID, f"Feedback: {msg}")
                await update.message.reply_text("Sent!")
            except:
                await update.message.reply_text("Failed")
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
    
    async def _daily_reminder(self):
        logger.info("Daily reminder")
    
    async def _check_reminders(self):
        reminders = self.db.get_active_reminders()
        for r in reminders:
            try:
                await self.app.bot.send_message(r["user_id"], f"Reminder: {r['message']}")
            except:
                pass
    
    async def error_handler(self, update, context):
        logger.error(f"Error: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text("Something went wrong.")
    
    async def run(self):
        await self.initialize()
        self.app.add_error_handler(self.error_handler)        self.scheduler.start()
        logger.info("Scheduler started")
        
        while True:
            try:
                await self.app.run_polling(drop_pending_updates=True)
                break
            except NetworkError as e:
                logger.warning(f"Network error: {e}")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Critical error: {e}")
                await asyncio.sleep(10)

def main():
    if not TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN not set!")
        sys.exit(1)
    bot = QuizBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

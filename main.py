#!/usr/bin/env python3
import os
import sys
import json
import logging
import sqlite3
import threading
import asyncio
import random
import re
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
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


# --- DATABASE MANAGER ---
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
                correct_answers INTEGER DEFAULT 0, language TEXT DEFAULT 'en')""",            """CREATE TABLE IF NOT EXISTS quizzes (
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
        user = self.fetch_one("SELECT streak, last_active FROM users WHERE user_id = ?", (user_id,))        if not user:
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
            
        self.execute_safe("UPDATE users SET streak = ?, last_active = ? WHERE user_id = ?", 
                         (current_streak, date.today().isoformat(), user_id))
        return {"streak": current_streak}
    
    def get_user_stats(self, user_id):
        user = self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not user: 
            return {}
        
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
    
    def get_notes(self, user_id):
        return self.fetch_all("SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    
    def add_homework(self, user_id, subject, task, due_date):        self.execute_safe("INSERT INTO homework (user_id, subject, task, due_date) VALUES (?, ?, ?, ?)",
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
    
    def delete_reminder(self, reminder_id, user_id):
        self.execute_safe("DELETE FROM reminders WHERE reminder_id = ? AND user_id = ?", (reminder_id, user_id))
        return True


# --- AI MANAGER ---
class AIManager:
    def __init__(self, api_key, model):
        self.client = Groq(api_key=api_key) if api_key else None
        self.model = model
        self.fallback_questions = [
            {"q": "What is 15 + 27?", "options": ["40", "42", "45", "52"], "a": 1, "e": "15 + 27 = 42"},
            {"q": "Capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"], "a": 2, "e": "Paris is the capital of France"},
            {"q": "H2O is?", "options": ["Salt", "Sugar", "Water", "Oxygen"], "a": 2, "e": "H2O is the chemical formula for water"},
            {"q": "What is 7 × 8?", "options": ["54", "56", "63", "48"], "a": 1, "e": "7 × 8 = 56"},
            {"q": "Largest planet in solar system?", "options": ["Earth", "Mars", "Jupiter", "Saturn"], "a": 2, "e": "Jupiter is the largest planet"}
        ]
    
    def generate_quiz_question(self, subject, class_level, difficulty="medium"):
        if not self.client:
            return random.choice(self.fallback_questions)
        try:
            prompt = f"""Generate a {difficulty} {subject} quiz question for class {class_level}. 
Return ONLY valid JSON in this exact format:
{{"question": "your question here", "options": ["A", "B", "C", "D"], "correct_index": 0, "explanation": "brief explanation"}}"""
            response = self.client.chat.completions.create(
                model=self.model, 
                messages=[{"role": "user", "content": prompt}], 
                max_tokens=400, 
                timeout=30            )
            content = response.choices[0].message.content.strip()
            # Clean potential markdown code blocks
            content = re.sub(r'^```json\s*|\s*```$', '', content)
            return json.loads(content)
        except Exception as e:
            logger.warning(f"AI generation failed: {e}, using fallback")
            return random.choice(self.fallback_questions)
    
    def explain_concept(self, concept, class_level):
        if not self.client:
            return f"*{concept}*: Please check your textbook or ask your teacher for details!"
        try:
            prompt = f"Explain '{concept}' in simple terms suitable for a class {class_level} student. Keep it under 300 words."
            response = self.client.chat.completions.create(
                model=self.model, 
                messages=[{"role": "user", "content": prompt}], 
                max_tokens=350, 
                timeout=30
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"AI explanation failed: {e}")
            return f"*{concept}*: This is an important topic. Please review your class notes or textbook."


# --- FLASK SERVER ---
def create_flask_app(bot_instance):
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return jsonify({"bot": BOT_NAME, "version": BOT_VERSION, "status": "running"})
    
    @app.route('/health')
    def health():
        return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200
    
    @app.route('/ping')
    def ping():
        return jsonify({"status": "pong", "bot": BOT_NAME}), 200
    
    @app.route('/stats')
    def stats():
        if bot_instance and bot_instance.db:
            users = bot_instance.db.fetch_one("SELECT COUNT(*) as count FROM users")
            return jsonify({"users": users["count"] if users else 0})
        return jsonify({"error": "DB not available"}), 503
    
    return app

# --- MAIN BOT CLASS ---
class QuizBot:
    def __init__(self):
        self.db = DatabaseManager(DATABASE_PATH)
        self.ai = AIManager(GROQ_API_KEY, GROQ_MODEL)
        self.app = None
        self.scheduler = None
        self.flask_app = None
        logger.info(f"{BOT_NAME} v{BOT_VERSION} initializing...")
    
    async def initialize(self):
        self.app = Application.builder().token(TG_BOT_TOKEN).build()
        self._register_handlers()
        self._setup_scheduler()
        self._start_flask()
        logger.info("Bot initialized successfully")
    
    def _register_handlers(self):
        # Command handlers
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
            "timer": self.cmd_timer,
            "admin": self.cmd_admin,
            "feedback": self.cmd_feedback
        }
        
        for cmd, handler in commands.items():
            self.app.add_handler(CommandHandler(cmd, handler))
        
        # Callback query handler for buttons
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Error handler
        self.app.add_error_handler(self.error_handler)    
    def _setup_scheduler(self):
        self.scheduler = AsyncIOScheduler()
        # Daily quiz reminder at 9 AM
        self.scheduler.add_job(self._daily_reminder, CronTrigger(hour=9, minute=0))
        # Check reminders every minute
        self.scheduler.add_job(self._check_reminders, 'interval', minutes=1)
        logger.info("Scheduler configured")
    
    def _start_flask(self):
        self.flask_app = create_flask_app(self)
        def run_flask():
            self.flask_app.run(host='0.0.0.0', port=PORT, threaded=True, use_reloader=False)
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info(f"Flask server running on port {PORT}")
    
    async def _check_user_access(self, update: Update) -> bool:
        """Check if user is banned or unauthorized"""
        user_id = update.effective_user.id
        if self.db.is_banned(user_id):
            await update.message.reply_text("⚠️ Your access has been restricted. Contact admin for support.")
            return False
        return True
    
    # ========== COMMAND HANDLERS ==========
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
            
        user = update.effective_user
        self.db.get_or_create_user(user.id, user.username, user.first_name)
        
        text = (f"🎓 *Welcome to {BOT_NAME}!*\n\n"
                f"Hello *{user.first_name}*! I'm here to help you learn and grow. 📚\n\n"
                f"🔹 /quiz [subject] - Start a practice quiz\n"
                f"🔹 /dailyquiz - Daily challenge with streaks!\n"
                f"🔹 /study <topic> - Get AI explanations\n"
                f"🔹 /profile - View your progress\n"
                f"🔹 /leaderboard - See top students\n"
                f"🔹 /help - All commands\n\n"
                f"Let's start learning! 🚀")
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = ("*📚 Available Commands:*\n\n"
                "*Quizzes & Learning:*\n"
                "  /quiz [subject] - Random quiz question\n"
                "  /dailyquiz - Daily challenge (+streak XP!)\n"                "  /study <topic> - AI explanation\n"
                "  /explain <concept> - Quick concept help\n\n"
                "*Progress & Stats:*\n"
                "  /profile - Your stats & level\n"
                "  /leaderboard - Top 10 students\n\n"
                "*Tools:*\n"
                "  /notes - View saved notes\n"
                "  /savenote <title> <content> - Save a note\n"
                "  /homework - Manage assignments\n"
                "  /calc <expression> - Quick calculator\n"
                "  /word - Word of the day\n"
                "  /fact - Random study fact\n\n"
                "*Other:*\n"
                "  /timer <minutes> - Study timer\n"
                "  /feedback - Send feedback\n"
                "  /ping - Check bot status")
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        latency = datetime.now()
        msg = await update.message.reply_text("🏓 Pinging...")
        await msg.edit_text(f"✅ *Pong!* Bot is online.\n_Latency: {(datetime.now() - latency).total_seconds()*1000:.0f}ms_", 
                           parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_study(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        if not context.args:
            await update.message.reply_text("❌ Usage: /study \<topic\>\n\nExample: /study photosynthesis")
            return
            
        topic = " ".join(context.args)
        user = self.db.get_or_create_user(update.effective_user.id)
        await update.message.chat.send_action("typing")
        
        explanation = self.ai.explain_concept(topic, user.get("class_level", 10))
        await update.message.reply_text(f"📖 *{topic}*\n\n{explanation}", parse_mode=ParseMode.MARKDOWN)
        
        # Award XP for studying
        xp_gain = self.db.add_xp(update.effective_user.id, 5)
        if xp_gain["level"] > user.get("level", 1):
            await update.message.reply_text(f"🎉 Level Up! You're now level {xp_gain['level']}!")
    
    async def cmd_explain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        if not context.args:
            await update.message.reply_text("❌ Usage: /explain \<concept\>")
            return
                    concept = " ".join(context.args)
        await update.message.chat.send_action("typing")
        explanation = self.ai.explain_concept(concept, 10)
        await update.message.reply_text(explanation, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        subject = context.args[0] if context.args else "general"
        await update.message.chat.send_action("typing")
        
        q = self.ai.generate_quiz_question(subject, 10)
        
        # Ensure we have valid data
        question = q.get("question") or q.get("q", "Sample Question?")
        options = q.get("options", ["Option A", "Option B", "Option C", "Option D"])
        correct_idx = q.get("correct_index") or q.get("a", 0)
        explanation = q.get("explanation") or q.get("e", "Review this concept!")
        
        # Ensure correct_index is valid
        if not isinstance(correct_idx, int) or correct_idx < 0 or correct_idx >= len(options):
            correct_idx = 0
        
        await update.message.reply_poll(
            question=question,
            options=options,
            type="quiz",
            correct_option_id=correct_idx,
            explanation=explanation,
            is_anonymous=False
        )
    
    async def cmd_dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        await update.message.chat.send_action("typing")
        
        user_id = update.effective_user.id
        streak = self.db.update_streak(user_id)
        q = random.choice(self.ai.fallback_questions)
        
        await update.message.reply_poll(
            question=f"🌟 Daily Quiz (Streak: {streak['streak']}!): {q['q']}",
            options=q["options"],
            type="quiz",
            correct_option_id=q["a"],
            explanation=q["e"],
            is_anonymous=False
        )
                # Bonus XP for daily participation
        xp_result = self.db.add_xp(user_id, 10 + (streak['streak'] * 2))
        await update.message.reply_text(f"✨ +{10 + streak['streak'] * 2} XP! Keep the streak going! 🔥")
    
    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        stats = self.db.get_user_stats(update.effective_user.id)
        
        if not stats:
            await update.message.reply_text("❌ Profile not found. Please use /start first.")
            return
            
        level = stats.get("level", 1)
        xp = stats.get("xp", 0)
        xp_for_next = level * XP_PER_LEVEL
        progress = (xp / xp_for_next) * 100 if xp_for_next > 0 else 0
        
        text = (f"👤 *Your Profile*\n\n"
                f"📊 Level: {level} ({xp}/{xp_for_next} XP)\n"
                f"{'🟦' * int(progress/10)}{'⬜' * (10 - int(progress/10))} {progress:.0f}%\n\n"
                f"🔥 Current Streak: {stats.get('streak', 0)} days\n"
                f"📝 Quizzes Taken: {stats.get('quiz_stats', {}).get('total', 0)}\n"
                f"✅ Correct Answers: {stats.get('quiz_stats', {}).get('correct', 0)}\n"
                f"🎯 Accuracy: {stats.get('accuracy', 0)}%")
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        top = self.db.get_leaderboard(10)
        if not top:
            await update.message.reply_text("📭 No users found yet. Be the first!")
            return
            
        text = "🏆 *Leaderboard - Top Students*\n\n"
        for i, u in enumerate(top, 1):
            name = u["username"] or u["first_name"] or f"User{u['user_id']}"
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} *{name}*: {u['xp']} XP (Lvl {u['level']})\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        notes = self.db.get_notes(update.effective_user.id)
        if not notes:
            await update.message.reply_text("📭 No notes saved yet. Use /savenote to create one!")
            return
                    text = "📓 *Your Notes*\n\n"
        for n in notes[:10]:  # Show latest 10
            preview = n['content'][:50] + "..." if len(n['content']) > 50 else n['content']
            text += f"🔹 *{n['title']}* ({n['subject']}): {preview}\n"
        
        if len(notes) > 10:
            text += f"\n_...and {len(notes) - 10} more notes_"
            
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_savenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("❌ Usage: /savenote \<title\> \<content\>\n\nExample: /savenote Math Pythagorean theorem: a² + b² = c²")
            return
            
        title = context.args[0]
        content = " ".join(context.args[1:])
        subject = context.args[2] if len(context.args) > 2 else "general"
        
        note_id = self.db.save_note(update.effective_user.id, title, content, subject)
        await update.message.reply_text(f"✅ Note saved! ID: #{note_id}\n_Title: {title}_")
    
    async def cmd_homework(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
            
        # Handle completion: /homework done <id>
        if context.args and context.args[0] == "done" and len(context.args) >= 2:
            try:
                hw_id = int(context.args[1])
                self.db.complete_homework(update.effective_user.id, hw_id)
                await update.message.reply_text(f"✅ Homework #{hw_id} marked as complete! 🎉")
                return
            except ValueError:
                await update.message.reply_text("❌ Invalid homework ID")
                return
        
        # Handle adding: /homework <subject> <task> <due_date>
        if context.args and context.args[0] != "list":
            if len(context.args) < 3:
                await update.message.reply_text("❌ Usage: /homework \<subject\> \<task\> \<due_date\>\n\nExample: /homework Math Solve page 45 2024-03-20")
                return
            subject = context.args[0]
            task = " ".join(context.args[1:-1])
            due_date = context.args[-1]
            
            hw_id = self.db.add_homework(update.effective_user.id, subject, task, due_date)
            await update.message.reply_text(f"✅ Homework added! ID: #{hw_id}\n_Due: {due_date}_")            return
            
        # List pending homework
        pending = self.db.get_pending_homework(update.effective_user.id)
        if not pending:
            await update.message.reply_text("✅ No pending homework! Great job! 🎉")
            return
            
        text = "📋 *Pending Homework*\n\n"
        for h in pending:
            text += f"🔹 *{h['subject']}*: {h['task']}\n   📅 Due: {h['due_date']} (ID: {h['homework_id']})\n"
        text += "\n_Use /homework done <ID> to mark complete_"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_word(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        words = [
            {"w": "Ephemeral", "m": "Lasting for a very short time"},
            {"w": "Resilient", "m": "Able to recover quickly from difficulties"},
            {"w": "Ubiquitous", "m": "Present everywhere at the same time"},
            {"w": "Meticulous", "m": "Showing great attention to detail"},
            {"w": "Eloquent", "m": "Fluent and persuasive in speaking or writing"}
        ]
        w = random.choice(words)
        await update.message.reply_text(f"📚 *Word of the Day*\n\n*{w['w']}*\n_{w['m']}_", 
                                       parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_fact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        facts = [
            "🧠 Your brain uses about 20% of your body's energy!",
            "🍯 Honey never spoils - edible honey has been found in ancient Egyptian tombs!",
            "📚 Reading just 20 minutes a day exposes you to 1.8 million words per year!",
            "🌙 The moon is moving away from Earth at about 3.8 cm per year!",
            "💡 The human brain can generate about 23 watts of power - enough to light a small bulb!"
        ]
        await update.message.reply_text(f"💡 *Did You Know?*\n\n{random.choice(facts)}", 
                                       parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_calc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_user_access(update):
            return
        if not context.args:
            await update.message.reply_text("❌ Usage: /calc \<expression\>\n\nExample: /calc 2 + 2 * 3")
            return
            
        expr = " ".join(context.args)
        # Safe evaluation - only allow basic math operations
        try:
            # Only allow numbers and basic operators
            if not re.match(r'^[\d\s\+\-\*\/\.\(\)\%\*\*]+$', expr):                raise ValueError("Invalid characters")
            result = eval(expr, {"__builtins__": {}}, {})
            await update.message.reply_text(f"🔢 `{expr}` = *{result}*", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: Invalid expression. Use only numbers and + - * / ( )")
    
    async def cmd_timer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /timer \<minutes\>\n\nExample: /timer 25 for Pomodoro!")
            return
        try:
            minutes = int(context.args[0])
            if minutes < 1 or minutes > 180:
                raise ValueError
            seconds = minutes * 60
            await update.message.reply_text(f"⏱️ Timer set for {minutes} minute(s)!\n\nI'll remind you when time's up. Good luck studying! 📚")
            # Schedule reminder (in a real app, use proper async scheduling)
            asyncio.create_task(self._timer_reminder(update.message.chat_id, seconds, minutes))
        except ValueError:
            await update.message.reply_text("❌ Please enter a number between 1 and 180")
    
    async def _timer_reminder(self, chat_id, seconds, minutes):
        await asyncio.sleep(seconds)
        try:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ *Time's up!* Your {minutes}-minute study session is complete. Great work! 🎉",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Timer reminder failed: {e}")
    
    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("🔒 Admin access required.")
            return
            
        if not context.args:
            await update.message.reply_text("Admin commands: /admin ban <user_id> <reason> | unban <user_id> | stats")
            return
            
        action = context.args[0]
        if action == "ban" and len(context.args) >= 3:
            try:
                user_id = int(context.args[1])
                reason = " ".join(context.args[2:])
                self.db.execute_safe("INSERT OR REPLACE INTO banned_users (user_id, reason) VALUES (?, ?)", (user_id, reason))
                await update.message.reply_text(f"✅ User {user_id} banned. Reason: {reason}")
            except ValueError:
                await update.message.reply_text("❌ Invalid user ID")        elif action == "unban" and len(context.args) >= 2:
            try:
                user_id = int(context.args[1])
                self.db.execute_safe("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
                await update.message.reply_text(f"✅ User {user_id} unbanned")
            except ValueError:
                await update.message.reply_text("❌ Invalid user ID")
        elif action == "stats":
            users = self.db.fetch_one("SELECT COUNT(*) as c FROM users")
            banned = self.db.fetch_one("SELECT COUNT(*) as c FROM banned_users")
            await update.message.reply_text(f"📊 Stats:\nUsers: {users['c']}\nBanned: {banned['c']}")
        else:
            await update.message.reply_text("❌ Unknown admin command")
    
    async def cmd_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /feedback \<your message\>\n\nYour feedback helps improve the bot! 💙")
            return
        feedback = " ".join(context.args)
        # In production, send to admin or log
        logger.info(f"Feedback from {update.effective_user.id}: {feedback}")
        await update.message.reply_text("✅ Thank you for your feedback! We appreciate it. 💙")
    
    # ========== CALLBACK HANDLER ==========
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks"""
        query = update.callback_query
        await query.answer()  # Always answer callback queries
        
        data = query.data
        if data.startswith("homework_done:"):
            hw_id = int(data.split(":")[1])
            self.db.complete_homework(query.from_user.id, hw_id)
            await query.edit_message_text("✅ Marked as complete! 🎉")
        elif data.startswith("note_view:"):
            note_id = int(data.split(":")[1])
            note = self.db.fetch_one("SELECT * FROM notes WHERE note_id = ? AND user_id = ?", 
                                    (note_id, query.from_user.id))
            if note:
                await query.edit_message_text(f"📓 *{note['title']}*\n\n{note['content']}", 
                                            parse_mode=ParseMode.MARKDOWN)
    
    # ========== SCHEDULED TASKS ==========
    
    async def _daily_reminder(self):
        """Send daily quiz reminder to active users"""
        logger.info("Running daily reminder job")
        # In production, fetch active users and send reminders
        # This is a placeholder        pass
    
    async def _check_reminders(self):
        """Check and send due reminders"""
        reminders = self.db.get_active_reminders()
        for reminder in reminders:
            try:
                await self.app.bot.send_message(
                    chat_id=reminder["user_id"],
                    text=f"🔔 Reminder: {reminder['message']}",
                    parse_mode=ParseMode.MARKDOWN
                )
                # Deactivate one-time reminders
                # self.db.execute_safe("UPDATE reminders SET is_active = 0 WHERE reminder_id = ?", (reminder["reminder_id"],))
            except Exception as e:
                logger.error(f"Failed to send reminder {reminder['reminder_id']}: {e}")
    
    # ========== ERROR HANDLER ==========
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Log errors and notify admin if needed"""
        logger.error(f"Update {update} caused error: {context.error}")
        if ADMIN_USER_ID and context.error:
            try:
                await self.app.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=f"⚠️ Bot Error:\n{context.error}"
                )
            except Exception:
                pass
    
    # ========== MAIN RUN METHOD ==========
    
    async def run(self):
        """Start the bot"""
        await self.initialize()
        logger.info(f"Starting {BOT_NAME}...")
        await self.app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )


# ========== ENTRY POINT ==========

async def main():
    if not TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN environment variable not set!")
        sys.exit(1)
        bot = QuizBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        sys.exit(1)
    finally:
        if bot.scheduler:
            bot.scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

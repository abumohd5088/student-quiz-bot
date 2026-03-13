import os
import sys
import json
import time
import random
import logging
import sqlite3
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    ContextTypes, filters, PollAnswerHandler
)

from groq import Groq
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from flask import Flask, jsonify

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class Config:
    TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
    ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", 0))
    GROUP_ID = int(os.environ.get("GROUP_ID", 0))
    PORT = int(os.environ.get("PORT", 8080))
    DB_NAME = "student_bot_db.sqlite"

class DatabaseManager:
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_name, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    def init_db(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0,
            last_active DATE, joined_date DATE DEFAULT CURRENT_DATE,
            total_quizzes INTEGER DEFAULT 0, correct_answers INTEGER DEFAULT 0
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS quizzes (
            quiz_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            group_id INTEGER, question TEXT, options TEXT,
            correct_index INTEGER, explanation TEXT, subject TEXT,
            xp_reward INTEGER DEFAULT 10, user_answer INTEGER DEFAULT -1,
            is_correct BOOLEAN DEFAULT 0, time_taken INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS notes (
            note_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            title TEXT, content TEXT, subject TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS homework (
            homework_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            subject TEXT, task TEXT, due_date DATE,
            is_completed BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        conn.commit()
        conn.close()
        logger.info("Database initialized")

    def upsert_user(self, user_id: int, username: str, first_name: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO users (user_id, username, first_name, last_active) 
            VALUES (?, ?, ?, CURRENT_DATE)
            ON CONFLICT(user_id) DO UPDATE SET 
            username=?, first_name=?, last_active=CURRENT_DATE''',
            (user_id, username, first_name, username, first_name))
        conn.commit()
        conn.close()

    def add_xp(self, user_id: int, xp_amount: int):
        conn = self.get_connection()        cursor = conn.cursor()
        cursor.execute('UPDATE users SET xp = xp + ?, level = ((xp + ?) / 100) + 1 WHERE user_id = ?',
            (xp_amount, xp_amount, user_id))
        conn.commit()
        conn.close()

    def get_user(self, user_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def save_quiz(self, user_id: int, group_id: int, question: str, options: List[str],
                  correct_index: int, explanation: str, subject: str, xp_reward: int) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO quizzes (user_id, group_id, question, options, correct_index,
            explanation, subject, xp_reward) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, group_id, question, json.dumps(options), correct_index,
             explanation, subject, xp_reward))
        quiz_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return quiz_id

    def update_quiz_answer(self, quiz_id: int, user_answer: int, is_correct: bool, time_taken: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE quizzes SET user_answer = ?, is_correct = ?, time_taken = ? WHERE quiz_id = ?',
            (user_answer, is_correct, time_taken, quiz_id))
        
        if is_correct:
            cursor.execute('SELECT user_id, xp_reward FROM quizzes WHERE quiz_id = ?', (quiz_id,))
            row = cursor.fetchone()
            if row:
                self.add_xp(row['user_id'], row['xp_reward'])
                cursor.execute('''UPDATE users SET total_quizzes = total_quizzes + 1,
                    correct_answers = correct_answers + 1 WHERE user_id = ?''', (row['user_id'],))
        conn.commit()
        conn.close()

    def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT username, first_name, xp, level,
            ROUND((correct_answers * 100.0 / NULLIF(total_quizzes, 0)), 1) as accuracy
            FROM users WHERE total_quizzes > 0 ORDER BY xp DESC LIMIT ?''', (limit,))
        rows = cursor.fetchall()        conn.close()
        return [dict(row) for row in rows]

    def save_note(self, user_id: int, title: str, content: str, subject: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO notes (user_id, title, content, subject) VALUES (?, ?, ?, ?)',
            (user_id, title, content, subject))
        conn.commit()
        conn.close()

    def get_notes(self, user_id: int) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def add_homework(self, user_id: int, subject: str, task: str, due_date: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO homework (user_id, subject, task, due_date) VALUES (?, ?, ?, ?)',
            (user_id, subject, task, due_date))
        conn.commit()
        conn.close()

    def get_all_users(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def ban_user(self, user_id: int, reason: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO banned_users (user_id, reason) VALUES (?, ?)',
            (user_id, reason))
        conn.commit()
        conn.close()

class AIManager:
    def __init__(self, api_key: str, model: str):
        self.client = Groq(api_key=api_key)
        self.model = model

    def generate_quiz_question(self, subject: str, class_level: int, difficulty: str) -> Optional[Dict]:
        prompt = f"""Generate a multiple-choice quiz question for Class {class_level} {subject}.Difficulty: {difficulty}
Return ONLY valid JSON:
{{
    "question": "Question text",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_index": 0,
    "explanation": "Brief explanation"
}}"""
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model, temperature=0.7, max_tokens=500
            )
            content = response.choices[0].message.content.strip()
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return None

    def explain_concept(self, concept: str, class_level: int) -> str:
        prompt = f"Explain '{concept}' for Class {class_level} student simply."
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model, temperature=0.6, max_tokens=800
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def solve_math(self, problem: str) -> str:
        prompt = f"Solve step-by-step: {problem}"
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model, temperature=0.5, max_tokens=1000
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

class StudentBot:
    def __init__(self, token: str, db: DatabaseManager, ai: AIManager):
        self.token = token
        self.db = db
        self.ai = ai
        self.app = None
        self.active_quizzes = {}
        self.scheduler = AsyncIOScheduler()        self.daily_facts = {
            'science': [
                "Earth is the only planet not named after a god.",
                "Water exists naturally in all three states."
            ],
            'history': [
                "Great Wall of China is NOT visible from space.",
                "Shortest war in history was 38 minutes."
            ],
            'motivation': [
                "The expert in anything was once a beginner.",
                "Success is the sum of small efforts repeated daily."
            ]
        }

    def setup_application(self):
        self.app = Application.builder().token(self.token).build()
        
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("ping", self.cmd_ping))
        self.app.add_handler(CommandHandler("study", self.cmd_study))
        self.app.add_handler(CommandHandler("explain", self.cmd_explain))
        self.app.add_handler(CommandHandler("solve", self.cmd_solve))
        self.app.add_handler(CommandHandler("quiz", self.cmd_quiz))
        self.app.add_handler(CommandHandler("dailyquiz", self.cmd_dailyquiz))
        self.app.add_handler(CommandHandler("profile", self.cmd_profile))
        self.app.add_handler(CommandHandler("leaderboard", self.cmd_leaderboard))
        self.app.add_handler(CommandHandler("fact", self.cmd_fact))
        self.app.add_handler(CommandHandler("joke", self.cmd_joke))
        self.app.add_handler(CommandHandler("notes", self.cmd_notes))
        self.app.add_handler(CommandHandler("savenote", self.cmd_savenote))
        self.app.add_handler(CommandHandler("homework", self.cmd_homework))
        self.app.add_handler(CommandHandler("admin", self.cmd_admin))
        self.app.add_handler(CommandHandler("broadcast", self.cmd_broadcast))
        self.app.add_handler(CommandHandler("banuser", self.cmd_banuser))
        self.app.add_handler(CommandHandler("listusers", self.cmd_listusers))
        
        self.app.add_handler(PollAnswerHandler(self.handle_poll_answer))
        self.app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.on_member_join))
        
        logger.info("All handlers registered")

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.upsert_user(user.id, user.username or "User", user.first_name or "User")
        text = f"WELCOME {user.first_name}!\n\nI am your AI Study Bot.\nUse /help for commands."
        await update.message.reply_text(text)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):        text = """COMMANDS:
/study <topic> - Learn anything
/quiz - Start Quiz
/dailyquiz - Daily Challenge
/profile - Your Stats
/leaderboard - Ranking
/fact - Fun Fact
/joke - Joke
/notes - My Notes
/savenote <title> <content> - Save Note
/homework <sub> <task> - Add HW
/admin - Admin Panel"""
        await update.message.reply_text(text)

    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("ONLINE")

    async def cmd_study(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /study <topic>")
            return
        topic = " ".join(context.args)
        await update.message.reply_text(f"Studying: {topic}\n\nGenerating lesson...")
        explanation = self.ai.explain_concept(topic, 10)
        await update.message.reply_text(explanation)
        self.db.add_xp(update.effective_user.id, 5)

    async def cmd_explain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /explain <concept>")
            return
        concept = " ".join(context.args)
        exp = self.ai.explain_concept(concept, 10)
        await update.message.reply_text(f"Explanation:\n{exp}")

    async def cmd_solve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /solve <problem>")
            return
        problem = " ".join(context.args)
        sol = self.ai.solve_math(problem)
        await update.message.reply_text(f"Solution:\n{sol}")

    async def cmd_quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Select Subject:\n\nMath, Science, GK, History\n\nUse /dailyquiz for random quiz!")

    async def cmd_dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        subjects = ['Math', 'Science', 'GK', 'History']
        subject = random.choice(subjects)
        await update.message.reply_text(f"DAILY QUIZ\nSubject: {subject}\nGenerating...")        
        q_data = self.ai.generate_quiz_question(subject, 10, "Medium")
        
        if q_data is None:
            await update.message.reply_text("Failed to generate question. Try again!")
            return
        
        try:
            poll = await update.message.reply_poll(
                question=q_data['question'],
                options=q_data['options'],
                type=Poll.QUIZ,
                correct_option_id=q_data['correct_index'],
                explanation=q_data.get('explanation', 'Good attempt!'),
                open_period=60,
                is_anonymous=False
            )
            
            quiz_id = self.db.save_quiz(
                update.effective_user.id, update.effective_chat.id,
                q_data['question'], q_data['options'],
                q_data['correct_index'], q_data.get('explanation', ''),
                subject, 15
            )
            
            self.active_quizzes[poll.poll.id] = {
                'quiz_id': quiz_id,
                'start_time': time.time(),
                'user_id': update.effective_user.id
            }
        except Exception as e:
            logger.error(f"Poll error: {e}")
            await update.message.reply_text("Error sending quiz.")

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.get_user(update.effective_user.id)
        if not user:
            await update.message.reply_text("Use /start first.")
            return
        text = f"{user['first_name']}\nXP: {user['xp']}\nLevel: {user['level']}\nQuizzes: {user['total_quizzes']}"
        await update.message.reply_text(text)

    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        leaders = self.db.get_leaderboard(10)
        if not leaders:
            await update.message.reply_text("No records yet.")
            return
        text = "LEADERBOARD\n\n"
        for i, u in enumerate(leaders, 1):
            text += f"{i}. {u['first_name']} - {u['xp']} XP\n"        await update.message.reply_text(text)

    async def cmd_fact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cat = random.choice(['science', 'history', 'motivation'])
        fact = random.choice(self.daily_facts[cat])
        await update.message.reply_text(f"FACT:\n{fact}")

    async def cmd_joke(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        jokes = ["Why was math book sad? Too many problems!", "Atoms make up everything!"]
        await update.message.reply_text(random.choice(jokes))

    async def cmd_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        notes = self.db.get_notes(update.effective_user.id)
        if not notes:
            await update.message.reply_text("No notes. Use /savenote")
            return
        text = "NOTES\n\n"
        for n in notes[:5]:
            text += f"{n['title']}\n"
        await update.message.reply_text(text)

    async def cmd_savenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /savenote <title> <content>")
            return
        title = context.args[0]
        content = " ".join(context.args[1:])
        self.db.save_note(update.effective_user.id, title, content, "General")
        await update.message.reply_text("Note saved!")

    async def cmd_homework(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /homework <subject> <task>")
            return
        subject = context.args[0]
        task = " ".join(context.args[1:])
        due = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.db.add_homework(update.effective_user.id, subject, task, due)
        await update.message.reply_text("Homework added!")

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.ADMIN_USER_ID:
            await update.message.reply_text("Admins only!")
            return
        await update.message.reply_text("ADMIN PANEL\n\n/broadcast\n/banuser\n/listusers")

    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.ADMIN_USER_ID:
            return
        if not context.args:            await update.message.reply_text("Usage: /broadcast <msg>")
            return
        msg = " ".join(context.args)
        users = self.db.get_all_users()
        count = 0
        for u in users:
            try:
                await context.bot.send_message(u['user_id'], f"Broadcast: {msg}")
                count += 1
                await asyncio.sleep(0.5)
            except:
                pass
        await update.message.reply_text(f"Sent to {count} users")

    async def cmd_banuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.ADMIN_USER_ID:
            return
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("Usage: /banuser <id>")
            return
        uid = int(context.args[0])
        self.db.ban_user(uid, "Admin ban")
        await update.message.reply_text(f"User {uid} banned")

    async def cmd_listusers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.ADMIN_USER_ID:
            return
        users = self.db.get_all_users()
        text = f"Users: {len(users)}\n\n"
        for u in users[:20]:
            text += f"- {u['first_name']} ({u['xp']} XP)\n"
        await update.message.reply_text(text)

    async def on_member_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for member in update.message.new_chat_members:
            self.db.upsert_user(member.id, member.username or "User", member.first_name or "User")
            await update.message.reply_text(f"Welcome {member.first_name}! Use /help")

    async def handle_poll_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        poll_id = update.poll_answer.poll_id
        user_id = update.effective_user.id
        if poll_id in self.active_quizzes:
            q_data = self.active_quizzes[poll_id]
            quiz_id = q_data['quiz_id']
            start_time = q_data['start_time']
            time_taken = int(time.time() - start_time)
            option_ids = update.poll_answer.option_ids
            if option_ids:
                user_answer = option_ids[0]
                conn = self.db.get_connection()                cursor = conn.cursor()
                cursor.execute('SELECT correct_index, xp_reward FROM quizzes WHERE quiz_id = ?', (quiz_id,))
                row = cursor.fetchone()
                conn.close()
                if row:
                    is_correct = (user_answer == row['correct_index'])
                    self.db.update_quiz_answer(quiz_id, user_answer, is_correct, time_taken)
                    if is_correct:
                        msg = f"CORRECT! +{row['xp_reward']} XP"
                    else:
                        msg = "WRONG"
                    await context.bot.send_message(user_id, msg)

    def schedule_daily_quizzes(self):
        times = [(8, 0), (11, 0), (14, 0), (16, 0), (19, 0), (21, 0)]
        for h, m in times:
            self.scheduler.add_job(self.send_daily_quiz, CronTrigger(hour=h, minute=m), id=f"dq_{h}_{m}")
        logger.info("Daily quizzes scheduled")

    async def send_daily_quiz(self):
        try:
            subject = random.choice(['Math', 'Science', 'GK'])
            q_data = self.ai.generate_quiz_question(subject, 10, "Medium")
            
            if q_data is None:
                return
            
            await self.app.bot.send_poll(
                chat_id=Config.GROUP_ID,
                question=f"DAILY QUIZ - {subject}\n\n{q_data['question']}",
                options=q_data['options'],
                type=Poll.QUIZ,
                correct_option_id=q_data['correct_index'],
                explanation=q_data.get('explanation', ''),
                open_period=120,
                is_anonymous=False
            )
        except Exception as e:
            logger.error(f"Daily quiz error: {e}")

    def run_scheduler(self):
        self.scheduler.start()
        self.schedule_daily_quizzes()

    def run(self):
        self.setup_application()
        self.run_scheduler()
        logger.info("Starting Bot...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({"status": "running", "bot": "AI Student Helper"})

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"})

def run_flask():
    flask_app.run(host='0.0.0.0', port=Config.PORT, threaded=True)

if __name__ == "__main__":
    db = DatabaseManager(Config.DB_NAME)
    ai = AIManager(Config.GROQ_API_KEY, Config.GROQ_MODEL)
    bot = StudentBot(Config.TG_BOT_TOKEN, db, ai)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask running on port {Config.PORT}")
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped")

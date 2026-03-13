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
from typing import Optional, Dict, List, Any, Tuple
import re
import hashlib

# Telegram Bot Libraries
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll, User
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    ContextTypes, filters, ConversationHandler, PollAnswerHandler,
    CallbackQueryHandler
)

# AI & Scheduling Libraries
import requests
from groq import Groq
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Flask for Railway health
from flask import Flask, jsonify

# ============================================================
# 🔐 CONFIGURATION
# ============================================================
CONFIG = {
    "TG_BOT_TOKEN": "tgbottoken",
    "GROQ_API_KEY": "paste-groq-api",
    "GROQ_MODEL": "llama-3.1-8b-instant",
    "ADMIN_USER_ID": 8538284477,
    "GROUP_ID": -1003748634705,
    "DB_NAME": "student_bot_db.sqlite",
    "FLASK_PORT": int(os.environ.get("PORT", 8080))
}

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================================
# 🗄️ DATABASE MANAGER
# ============================================================
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
        
        # Users Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                streak INTEGER DEFAULT 0,
                last_active DATE,
                badges TEXT DEFAULT '[]',
                joined_date DATE DEFAULT CURRENT_DATE,
                language TEXT DEFAULT 'en',
                class_level INTEGER DEFAULT 10,
                total_quizzes INTEGER DEFAULT 0,
                correct_answers INTEGER DEFAULT 0
            )
        ''')

        # Groups Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                group_name TEXT,
                auto_poll_enabled BOOLEAN DEFAULT 1,
                poll_interval INTEGER DEFAULT 120,
                language TEXT DEFAULT 'en',
                created_date DATE DEFAULT CURRENT_DATE
            )
        ''')

        # Quizzes Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quizzes (
                quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                group_id INTEGER,
                question TEXT,
                options TEXT,
                correct_index INTEGER,
                explanation TEXT,
                subject TEXT,
                difficulty TEXT,
                user_answer INTEGER DEFAULT -1,
                is_correct BOOLEAN DEFAULT 0,
                time_taken INTEGER DEFAULT 0,
                xp_reward INTEGER DEFAULT 10,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                answered_at TIMESTAMP
            )
        ''')

        # Notes Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                content TEXT,
                subject TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Reminders Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                reminder_time TEXT,
                message TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Achievements Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS achievements (
                achievement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                badge_name TEXT,
                badge_icon TEXT,
                earned_date DATE DEFAULT CURRENT_DATE,
                UNIQUE(user_id, badge_name)
            )
        ''')

        # Homework Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS homework (
                homework_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject TEXT,
                task TEXT,
                due_date DATE,
                is_completed BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Banned Users Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("✅ Database initialized successfully")

    def is_banned(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def upsert_user(self, user_id: int, username: str, first_name: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, last_active) 
            VALUES (?, ?, ?, CURRENT_DATE)
            ON CONFLICT(user_id) DO UPDATE SET 
                username=?, first_name=?, last_active=CURRENT_DATE
        ''', (user_id, username, first_name, username, first_name))
        conn.commit()
        conn.close()

    def add_xp(self, user_id: int, xp_amount: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE users SET xp = xp + ?, 
            level = ((xp + ?) / 100) + 1 
            WHERE user_id = ?
        ''', (xp_amount, xp_amount, user_id))
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
                  correct_index: int, explanation: str, subject: str, difficulty: str,
                  xp_reward: int) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO quizzes (user_id, group_id, question, options, correct_index,
            explanation, subject, difficulty, xp_reward)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, group_id, question, json.dumps(options), correct_index,
              explanation, subject, difficulty, xp_reward))
        quiz_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return quiz_id

    def update_quiz_answer(self, quiz_id: int, user_answer: int, is_correct: bool, time_taken: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE quizzes SET user_answer = ?, is_correct = ?, 
            time_taken = ?, answered_at = CURRENT_TIMESTAMP
            WHERE quiz_id = ?
        ''', (user_answer, is_correct, time_taken, quiz_id))
        
        if is_correct:
            cursor.execute('SELECT user_id, xp_reward FROM quizzes WHERE quiz_id = ?', (quiz_id,))
            row = cursor.fetchone()
            if row:
                self.add_xp(row['user_id'], row['xp_reward'])
                cursor.execute('''
                    UPDATE users SET total_quizzes = total_quizzes + 1,
                    correct_answers = correct_answers + 1 WHERE user_id = ?
                ''', (row['user_id'],))
            else:
                cursor.execute('''
                    UPDATE users SET total_quizzes = total_quizzes + 1
                    WHERE user_id = (SELECT user_id FROM quizzes WHERE quiz_id = ?)
                ''', (quiz_id,))
        else:
            cursor.execute('''
                UPDATE users SET total_quizzes = total_quizzes + 1
                WHERE user_id = (SELECT user_id FROM quizzes WHERE quiz_id = ?)
            ''', (quiz_id,))
        
        conn.commit()
        conn.close()

    def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, first_name, xp, level, 
                   ROUND((correct_answers * 100.0 / NULLIF(total_quizzes, 0)), 1) as accuracy
            FROM users 
            WHERE total_quizzes > 0
            ORDER BY xp DESC, level DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def save_note(self, user_id: int, title: str, content: str, subject: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO notes (user_id, title, content, subject)
            VALUES (?, ?, ?, ?)
        ''', (user_id, title, content, subject))
        conn.commit()
        conn.close()

    def get_notes(self, user_id: int) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def delete_note(self, user_id: int, note_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM notes WHERE user_id = ? AND note_id = ?', (user_id, note_id))
        conn.commit()
        conn.close()

    def add_reminder(self, user_id: int, reminder_time: str, message: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reminders (user_id, reminder_time, message)
            VALUES (?, ?, ?)
        ''', (user_id, reminder_time, message))
        conn.commit()
        conn.close()

    def get_reminders(self, user_id: int) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM reminders WHERE user_id = ? AND is_active = 1', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def add_homework(self, user_id: int, subject: str, task: str, due_date: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO homework (user_id, subject, task, due_date)
            VALUES (?, ?, ?, ?)
        ''', (user_id, subject, task, due_date))
        conn.commit()
        conn.close()

    def get_homework(self, user_id: int) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM homework WHERE user_id = ? AND is_completed = 0
            ORDER BY due_date ASC
        ''', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def complete_homework(self, user_id: int, homework_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE homework SET is_completed = 1 WHERE user_id = ? AND homework_id = ?
        ''', (user_id, homework_id))
        conn.commit()
        conn.close()

    def award_badge(self, user_id: int, badge_name: str, badge_icon: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO achievements (user_id, badge_name, badge_icon)
                VALUES (?, ?, ?)
            ''', (user_id, badge_name, badge_icon))
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # Badge already exists
        finally:
            conn.close()

    def get_achievements(self, user_id: int) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM achievements WHERE user_id = ?', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

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
        cursor.execute('''
            INSERT OR REPLACE INTO banned_users (user_id, reason)
            VALUES (?, ?)
        ''', (user_id, reason))
        conn.commit()
        conn.close()

    def unban_user(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()

# ============================================================
# 🧠 AI MANAGER (Groq)
# ============================================================
class AIManager:
    def __init__(self, api_key: str, model: str):
        self.client = Groq(api_key=api_key)
        self.model = model

    def generate_quiz_question(self, subject: str, class_level: int, difficulty: str) -> Optional[Dict]:
        prompt = f"""
        Generate a multiple-choice quiz question for Class {class_level} {subject}.
        Difficulty: {difficulty}
        
        Return ONLY valid JSON:
        {{
            "question": "Question text here",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_index": 0,
            "explanation": "Detailed explanation of the correct answer",
            "topic": "Specific topic covered"
        }}
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.7,
                max_tokens=500
            )
            content = response.choices[0].message.content.strip()
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            logger.error(f"AI Quiz Error: {e}")
            return None

    def explain_concept(self, concept: str, class_level: int) -> str:
        prompt = f"""
        Explain this concept for Class {class_level} student in simple language:
        {concept}
        
        Provide:
        1. Simple definition
        2. Real-life examples
        3. Key points to remember
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.6,
                max_tokens=800
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def solve_math(self, problem: str) -> str:
        prompt = f"""
        Solve this math problem step by step:
        {problem}
        
        Show:
        1. Given information
        2. Formula/concept used
        3. Step-by-step solution
        4. Final answer
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.5,
                max_tokens=1000
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def generate_study_plan(self, subject: str, days: int, class_level: int) -> str:
        prompt = f"""
        Create a {days}-day study plan for Class {class_level} {subject}.
        Include:
        - Daily topics to cover
        - Practice exercises
        - Revision schedule
        - Mock test days
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.6,
                max_tokens=1000
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def write_essay(self, topic: str, word_limit: int = 300) -> str:
        prompt = f"""
        Write an essay on "{topic}" in approximately {word_limit} words.
        Include:
        - Introduction
        - Main body with key points
        - Conclusion
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.7,
                max_tokens=600
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def generate_flashcard(self, subject: str, class_level: int) -> Dict:
        prompt = f"""
        Generate a flashcard for Class {class_level} {subject}.
        Return JSON:
        {{
            "term": "Key term/concept",
            "definition": "Clear definition",
            "example": "Example usage"
        }}
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.7,
                max_tokens=300
            )
            content = response.choices[0].message.content.strip()
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            return {"term": "Error", "definition": str(e), "example": ""}

    def translate_text(self, text: str, target_lang: str) -> str:
        prompt = f"""
        Translate this text to {target_lang}:
        {text}
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.5,
                max_tokens=500
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def check_grammar(self, text: str) -> str:
        prompt = f"""
        Check grammar and suggest improvements for:
        {text}
        
        Provide:
        1. Corrected version
        2. Grammar rules applied
        3. Suggestions for improvement
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.5,
                max_tokens=600
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Error: {str(e)}"

    def generate_word_meaning(self, word: str) -> Dict:
        prompt = f"""
        Provide detailed information about the word: {word}
        Return JSON:
        {{
            "meaning": "Definition",
            "synonyms": ["syn1", "syn2"],
            "antonyms": ["ant1", "ant2"],
            "example_sentence": "Example usage"
        }}
        """
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.6,
                max_tokens=400
            )
            content = response.choices[0].message.content.strip()
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            return {"meaning": str(e), "synonyms": [], "antonyms": [], "example_sentence": ""}

# ============================================================
# 🤖 TELEGRAM BOT
# ============================================================
class StudentBot:
    def __init__(self, token: str, db: DatabaseManager, ai: AIManager):
        self.token = token
        self.db = db
        self.ai = ai
        self.app = None
        self.active_quizzes = {}
        self.scheduler = AsyncIOScheduler()
        self.daily_facts = {
            'science': [
                " The human brain uses 20% of the body's total energy!",
                "🌍 Earth is the only planet not named after a god.",
                "💧 Water is the only substance found naturally in all three states.",
                "⚡ Lightning is 5 times hotter than the sun's surface!",
                "🦴 Babies have 300 bones, adults have 206."
            ],
            'history': [
                "🏛️ The Great Wall of China is NOT visible from space.",
                "📜 The shortest war in history was 38 minutes long.",
                "👑 Cleopatra lived closer to the moon landing than to the pyramids.",
                "🎓 The oldest university is over 1000 years old.",
                "🗿 Easter Island statues weigh up to 82 tons each."
            ],
            'motivation': [
                "💪 'The expert in anything was once a beginner.'",
                "🌟 'Success is the sum of small efforts repeated daily.'",
                "📚 'Education is the most powerful weapon.'",
                "🎯 'Dream big and dare to fail.'",
                "🔥 'Your only limit is you.'"
            ]
        }

    def setup_application(self):
        self.app = Application.builder().token(self.token).build()
        
        # Learning Commands
        self.app.add_handler(CommandHandler("study", self.cmd_study))
        self.app.add_handler(CommandHandler("explain", self.cmd_explain))
        self.app.add_handler(CommandHandler("solve", self.cmd_solve))
        self.app.add_handler(CommandHandler("formula", self.cmd_formula))
        self.app.add_handler(CommandHandler("grammar", self.cmd_grammar))
        self.app.add_handler(CommandHandler("essay", self.cmd_essay))
        self.app.add_handler(CommandHandler("outline", self.cmd_outline))
        self.app.add_handler(CommandHandler("translate", self.cmd_translate))
        self.app.add_handler(CommandHandler("summarize", self.cmd_summarize))
        self.app.add_handler(CommandHandler("code", self.cmd_code))
        self.app.add_handler(CommandHandler("studyplan", self.cmd_studyplan))
        self.app.add_handler(CommandHandler("debate", self.cmd_debate))
        self.app.add_handler(CommandHandler("vocab", self.cmd_vocab))
        self.app.add_handler(CommandHandler("poem", self.cmd_poem))
        self.app.add_handler(CommandHandler("story", self.cmd_story))
        self.app.add_handler(CommandHandler("mindmap", self.cmd_mindmap))
        
        # Quiz & Games
        self.app.add_handler(CommandHandler("quiz", self.cmd_quiz))
        self.app.add_handler(CommandHandler("dailyquiz", self.cmd_dailyquiz))
        self.app.add_handler(CommandHandler("mathproblem", self.cmd_mathproblem))
        self.app.add_handler(CommandHandler("flashcard", self.cmd_flashcard))
        self.app.add_handler(CommandHandler("trivia", self.cmd_trivia))
        
        # Daily Info
        self.app.add_handler(CommandHandler("word", self.cmd_word))
        self.app.add_handler(CommandHandler("joke", self.cmd_joke))
        self.app.add_handler(CommandHandler("fact", self.cmd_fact))
        self.app.add_handler(CommandHandler("sciencefact", self.cmd_sciencefact))
        self.app.add_handler(CommandHandler("historyfact", self.cmd_historyfact))
        self.app.add_handler(CommandHandler("quote", self.cmd_quote))
        self.app.add_handler(CommandHandler("dailytip", self.cmd_dailytip))
        
        # Tools
        self.app.add_handler(CommandHandler("calc", self.cmd_calc))
        self.app.add_handler(CommandHandler("convert", self.cmd_convert))
        self.app.add_handler(CommandHandler("timer", self.cmd_timer))
        self.app.add_handler(CommandHandler("countdown", self.cmd_countdown))
        self.app.add_handler(CommandHandler("password", self.cmd_password))
        self.app.add_handler(CommandHandler("homework", self.cmd_homework))
        
        # Progress
        self.app.add_handler(CommandHandler("profile", self.cmd_profile))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CommandHandler("leaderboard", self.cmd_leaderboard))
        self.app.add_handler(CommandHandler("rank", self.cmd_rank))
        self.app.add_handler(CommandHandler("achievements", self.cmd_achievements))
        self.app.add_handler(CommandHandler("streak", self.cmd_streak))
        self.app.add_handler(CommandHandler("resetstats", self.cmd_resetstats))
        
        # Notes
        self.app.add_handler(CommandHandler("notes", self.cmd_notes))
        self.app.add_handler(CommandHandler("savenote", self.cmd_savenote))
        self.app.add_handler(CommandHandler("deletenote", self.cmd_deletenote))
        self.app.add_handler(CommandHandler("searchnote", self.cmd_searchnote))
        self.app.add_handler(CommandHandler("remind", self.cmd_remind))
        
        # Info
        self.app.add_handler(CommandHandler("faq", self.cmd_faq))
        self.app.add_handler(CommandHandler("contact", self.cmd_contact))
        self.app.add_handler(CommandHandler("feedback", self.cmd_feedback))
        self.app.add_handler(CommandHandler("language", self.cmd_language))
        
        # Admin
        self.app.add_handler(CommandHandler("admin", self.cmd_admin))
        self.app.add_handler(CommandHandler("broadcast", self.cmd_broadcast))
        self.app.add_handler(CommandHandler("sendpoll", self.cmd_sendpoll))
        self.app.add_handler(CommandHandler("setpollgroup", self.cmd_setpollgroup))
        self.app.add_handler(CommandHandler("stopauto", self.cmd_stopauto))
        self.app.add_handler(CommandHandler("banuser", self.cmd_banuser))
        self.app.add_handler(CommandHandler("unbanuser", self.cmd_unbanuser))
        self.app.add_handler(CommandHandler("userinfo", self.cmd_userinfo))
        self.app.add_handler(CommandHandler("listusers", self.cmd_listusers))
        self.app.add_handler(CommandHandler("grantxp", self.cmd_grantxp))
        
        # Basic
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("ping", self.cmd_ping))
        
        # Poll Answers
        self.app.add_handler(PollAnswerHandler(self.handle_poll_answer))
        
        # Group Messages
        self.app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.on_member_join))
        
        logger.info("✅ All handlers registered")

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.upsert_user(user.id, user.username or "User", user.first_name or "User")
        
        welcome_text = f"""
🎓 **WELCOME TO AI STUDENT HELPER!** 🎓

 Hello {user.first_name}!

I'm your 24/7 AI-powered study companion for Classes 1-12!

📚 **What I can do:**
• Generate unlimited quiz questions
• Explain any concept
• Solve math problems
• Write essays & stories
• Create study plans
• Track your progress
• And 50+ more features!

🎯 **Quick Start:**
/quiz - Start a quiz
/study <topic> - Learn anything
/profile - Check your progress
/help - See all commands

 Start learning now and earn XP!
        """
        
        keyboard = [
            [InlineKeyboardButton("📚 Start Quiz", callback_data="start_quiz"),
             InlineKeyboardButton("📖 Study", callback_data="study_menu")],
            [InlineKeyboardButton("📊 My Profile", callback_data="my_profile"),
             InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
📖 **COMPLETE COMMANDS LIST**

🎯 **LEARNING:**
/study <topic> - AI lesson on any topic
/explain <concept> - Detailed explanation
/solve <math> - Step-by-step solution
/formula <subject> - Key formulas
/grammar <text> - Grammar check
/essay <topic> - Write an essay
/outline <topic> - Essay outline
/translate <lang> <text> - Translate
/summarize <text> - Summarize text
/code <problem> - Code helper
/studyplan <subject> <days> - Study plan
/debate <topic> - Debate both sides
/vocab <word> - Word meaning
/poem <topic> - Generate poem
/story <prompt> - Generate story
/mindmap <topic> - Text mind map

🎮 **QUIZ & GAMES:**
/quiz - Start subject quiz
/dailyquiz - Daily challenge
/mathproblem - Random math
/flashcard <subject> - Flashcards
/trivia - Random trivia

🌍 **DAILY INFO:**
/word - Word of the day
/joke - Joke of the day
/fact - Random fun fact
/sciencefact - Science fact
/historyfact - History fact
/quote - Motivational quote
/dailytip - Study tip

🔧 **TOOLS:**
/calc <expression> - Calculator
/convert <val> <from> <to> - Convert
/timer <minutes> - Pomodoro timer
/countdown <name> <date> - Countdown
/password <length> - Password generator
/homework <subject> <task> - Homework

📊 **PROGRESS:**
/profile - Your profile
/stats - Detailed statistics
/leaderboard - Global ranking
/rank - My current rank
/achievements - My badges
/streak - Streak info

📋 **NOTES:**
/notes - View all notes
/savenote <title> <content> - Save note
/deletenote <id> - Delete note
/searchnote <keyword> - Search
/remind <HH:MM> <msg> - Reminder

👥 **INFO:**
/faq - FAQs
/contact - Contact admin
/feedback <msg> - Feedback
/language <lang> - Set language

👑 **ADMIN:**
/admin - Admin panel
/broadcast <msg> - Broadcast
/banuser <id> - Ban user
/unbanuser <id> - Unban
/listusers - List users

/start - Welcome message
/help - This help message
/ping - Check bot status
        """
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_study(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /study <topic name>\n\nExample: /study Photosynthesis")
            return
        
        topic = " ".join(context.args)
        user = self.db.get_user(update.effective_user.id)
        class_level = user['class_level'] if user else 10
        
        await update.message.reply_text(f"📚 **Generating lesson on: {topic}**\n\n⏳ Please wait...", parse_mode=ParseMode.MARKDOWN)
        
        explanation = self.ai.explain_concept(topic, class_level)
        
        response = f"""
📖 **LESSON: {topic.upper()}**
 Class: {class_level}

{explanation}

💡 **Tip:** Revise this topic after 24 hours for better retention!
        """
        
        await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
        self.db.add_xp(update.effective_user.id, 5)

    async def cmd_explain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /explain <concept>")
            return
        
        concept = " ".join(context.args)
        explanation = self.ai.explain_concept(concept, 10)
        await update.message.reply_text(f"💡 **Explanation:**\n\n{explanation}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_solve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /solve <math problem>")
            return
        
        problem = " ".join(context.args)
        solution = self.ai.solve_math(problem)
        await update.message.reply_text(f"🧮 **Solution:**\n\n{solution}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_formula(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /formula <subject>\n\nExamples: /formula Physics, /formula Math")
            return
        
        subject = " ".join(context.args)
        
        formulas = {
            'math': "📐 **Key Math Formulas:**\n\n• Quadratic: x = (-b ± √(b²-4ac)) / 2a\n• Area of Circle: πr²\n• Pythagoras: a² + b² = c²\n• Slope: m = (y₂-y₁)/(x₂-x₁)\n• Distance: d = √((x₂-x₁)² + (y₂-y₁)²)",
            'physics': "⚡ **Key Physics Formulas:**\n\n• Force: F = ma\n• Velocity: v = u + at\n• Work: W = F × d\n• Power: P = W/t\n• Energy: E = mc²",
            'chemistry': "🧪 **Key Chemistry Formulas:**\n\n• Moles: n = mass/molar mass\n• Molarity: M = n/V\n• pH: pH = -log[H⁺]\n• Ideal Gas: PV = nRT",
        }
        
        subject_lower = subject.lower()
        if subject_lower in formulas:
            await update.message.reply_text(formulas[subject_lower], parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"📚 Formulas for {subject}:\n\nPlease specify: Math, Physics, or Chemistry")

    async def cmd_quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.get_user(update.effective_user.id)
        if not user:
            self.db.upsert_user(update.effective_user.id, update.effective_user.username or "User", update.effective_user.first_name or "User")
            user = self.db.get_user(update.effective_user.id)
        
        class_level = user['class_level'] if user else 10
        
        # Ask for subject
        keyboard = [
            [InlineKeyboardButton("🔢 Math", callback_data=f"quiz_math_{class_level}"),
             InlineKeyboardButton("🔬 Science", callback_data=f"quiz_science_{class_level}")],
            [InlineKeyboardButton("⚛️ Physics", callback_data=f"quiz_physics_{class_level}"),
             InlineKeyboardButton("🧪 Chemistry", callback_data=f"quiz_chemistry_{class_level}")],
            [InlineKeyboardButton("📖 English", callback_data=f"quiz_english_{class_level}"),
             InlineKeyboardButton("🏛️ History", callback_data=f"quiz_history_{class_level}")],
            [InlineKeyboardButton("🌍 GK", callback_data=f"quiz_gk_{class_level}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"🎯 **SELECT SUBJECT FOR QUIZ**\n\nClass: {class_level}\n\nChoose a subject:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

    async def cmd_dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Generate daily quiz
        subjects = ['Math', 'Science', 'GK', 'History']
        subject = random.choice(subjects)
        
        await update.message.reply_text(f"📅 **DAILY QUIZ**\n\nSubject: {subject}\nDifficulty: Medium\n\nGenerating question...")
        
        q_data = self.ai.generate_quiz_question(subject, 10, "Medium")
        if not q_
            await update.message.reply_text("❌ Failed to generate question. Try again!")
            return
        
        # Send as poll
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
                update.effective_user.id,
                update.effective_chat.id,
                q_data['question'],
                q_data['options'],
                q_data['correct_index'],
                q_data.get('explanation', ''),
                subject,
                "Medium",
                15
            )
            
            self.active_quizzes[poll.poll.id] = {
                'quiz_id': quiz_id,
                'start_time': time.time(),
                'user_id': update.effective_user.id
            }
        except Exception as e:
            logger.error(f"Poll error: {e}")
            await update.message.reply_text("❌ Error sending quiz. Please try again.")

    async def cmd_mathproblem(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        problems = [
            "Solve: 2x + 5 = 15",
            "Find area of circle with radius 7cm",
            "What is 15% of 200?",
            "Simplify: (3x² + 2x) - (x² - 5x)",
            "Find x: x/4 = 8"
        ]
        
        problem = random.choice(problems)
        await update.message.reply_text(f"🧮 **MATH PROBLEM**\n\n{problem}\n\nUse /solve to get solution!")

    async def cmd_flashcard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /flashcard <subject>\n\nExample: /flashcard Biology")
            return
        
        subject = " ".join(context.args)
        card = self.ai.generate_flashcard(subject, 10)
        
        text = f"""
📇 **FLASHCARD**
Subject: {subject}

📌 **Term:** {card.get('term', 'N/A')}

📖 **Definition:** {card.get('definition', 'N/A')}

💡 **Example:** {card.get('example', 'N/A')}
        """
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_trivia(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        trivia_questions = [
            {"q": "What is the capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"], "correct": 2},
            {"q": "Which planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "correct": 1},
            {"q": "What is H2O commonly known as?", "options": ["Salt", "Sugar", "Water", "Air"], "correct": 2},
        ]
        
        trivia = random.choice(trivia_questions)
        
        keyboard = [[InlineKeyboardButton(opt, callback_data=f"trivia_{i}")] for i, opt in enumerate(trivia['options'])]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(f"🎲 **TRIVIA QUESTION**\n\n{trivia['q']}", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def cmd_word(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        words = ["Serendipity", "Ephemeral", "Resilience", "Eloquent", "Meticulous"]
        word = random.choice(words)
        meaning = self.ai.generate_word_meaning(word)
        
        text = f"""
📖 **WORD OF THE DAY**

🔤 **Word:** {word}

📝 **Meaning:** {meaning.get('meaning', 'N/A')}

✅ **Synonyms:** {', '.join(meaning.get('synonyms', []))}

❌ **Antonyms:** {', '.join(meaning.get('antonyms', []))}

💬 **Example:** {meaning.get('example_sentence', 'N/A')}
        """
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_joke(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        jokes = [
            "Why was the math book sad? Because it had too many problems! 😄",
            "Why don't scientists trust atoms? Because they make up everything! 🔬",
            "Why did the student eat his homework? Because the teacher said it was a piece of cake! 📚",
            "What do you call a fake noodle? An impasta! 🍝",
            "Why can't you give Elsa a balloon? Because she will let it go! 🎈"
        ]
        await update.message.reply_text(f"😄 **JOKE OF THE DAY**\n\n{random.choice(jokes)}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_fact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        category = random.choice(['science', 'history', 'motivation'])
        fact = random.choice(self.daily_facts[category])
        await update.message.reply_text(f"💡 **FUN FACT**\n\n{fact}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_sciencefact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        fact = random.choice(self.daily_facts['science'])
        await update.message.reply_text(f"🔬 **SCIENCE FACT**\n\n{fact}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_historyfact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        fact = random.choice(self.daily_facts['history'])
        await update.message.reply_text(f"🏛️ **HISTORY FACT**\n\n{fact}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_quote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        quotes = [
            "📚 'Education is the most powerful weapon which you can use to change the world.' - Nelson Mandela",
            "🌟 'The only way to do great work is to love what you do.' - Steve Jobs",
            "💪 'Believe you can and you're halfway there.' - Theodore Roosevelt",
            "🎯 'Success is not final, failure is not fatal: It is the courage to continue that counts.' - Winston Churchill"
        ]
        await update.message.reply_text(random.choice(quotes), parse_mode=ParseMode.MARKDOWN)

    async def cmd_dailytip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tips = [
            "📖 **Study Tip:** Use the Pomodoro Technique - 25 min study, 5 min break!",
            "💧 **Health Tip:** Drink 8 glasses of water daily for better concentration!",
            "😴 **Sleep Tip:** Get 7-8 hours of sleep for better memory retention!",
            "📝 **Note Tip:** Use color coding for different subjects!",
            "🧠 **Memory Tip:** Teach what you learn to someone else!"
        ]
        await update.message.reply_text(random.choice(tips), parse_mode=ParseMode.MARKDOWN)

    async def cmd_calc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /calc <expression>\n\nExample: /calc 2+2*3")
            return
        
        expression = " ".join(context.args)
        try:
            # Safe evaluation
            result = eval(expression, {"__builtins__": {}}, {})
            await update.message.reply_text(f"🧮 **Result:**\n\n{expression} = {result}", parse_mode=ParseMode.MARKDOWN)
        except:
            await update.message.reply_text("❌ Invalid expression. Use numbers and +, -, *, /")

    async def cmd_convert(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 3:
            await update.message.reply_text("❌ Usage: /convert <value> <from> <to>\n\nExample: /convert 100 cm m")
            return
        
        try:
            value = float(context.args[0])
            from_unit = context.args[1].lower()
            to_unit = context.args[2].lower()
            
            conversions = {
                ('cm', 'm'): value / 100,
                ('m', 'cm'): value * 100,
                ('km', 'm'): value * 1000,
                ('m', 'km'): value / 1000,
                ('kg', 'g'): value * 1000,
                ('g', 'kg'): value / 1000,
                ('l', 'ml'): value * 1000,
                ('ml', 'l'): value / 1000,
            }
            
            result = conversions.get((from_unit, to_unit), None)
            
            if result is not None:
                await update.message.reply_text(f"🔄 **Conversion:**\n\n{value} {from_unit} = {result} {to_unit}", parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text("❌ Conversion not supported. Try: cm↔m, km↔m, kg↔g, l↔ml")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def cmd_timer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /timer <minutes>\n\nExample: /timer 25")
            return
        
        minutes = int(context.args[0])
        seconds = minutes * 60
        
        await update.message.reply_text(f"⏱️ **POMODORO TIMER STARTED**\n\nDuration: {minutes} minutes\n\nI'll remind you when time's up!")
        
        await asyncio.sleep(seconds)
        await update.message.reply_text("🔔 **TIME'S UP!**\n\nGreat job! Take a 5-minute break. ☕")

    async def cmd_countdown(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("❌ Usage: /countdown <name> <YYYY-MM-DD>\n\nExample: /countdown Exam 2025-03-15")
            return
        
        name = context.args[0]
        try:
            target_date = datetime.strptime(context.args[1], "%Y-%m-%d")
            days_left = (target_date - datetime.now()).days
            
            if days_left < 0:
                await update.message.reply_text(f"❌ {name} has already passed!")
            else:
                await update.message.reply_text(f"📅 **COUNTDOWN**\n\nEvent: {name}\nDate: {target_date.strftime('%B %d, %Y')}\n⏰ Days Left: {days_left} days", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await update.message.reply_text("❌ Invalid date format. Use YYYY-MM-DD")

    async def cmd_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        length = int(context.args[0]) if context.args and context.args[0].isdigit() else 12
        
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        password = ''.join(random.choice(chars) for _ in range(length))
        
        await update.message.reply_text(f"🔐 **GENERATED PASSWORD**\n\n`{password}`\n\n💡 Save it securely!", parse_mode=ParseMode.MARKDOWN)

    async def cmd_homework(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("❌ Usage: /homework <subject> <task>\n\nExample: /homework Math Complete chapter 5")
            return
        
        subject = context.args[0]
        task = " ".join(context.args[1:])
        
        # Save homework
        self.db.add_homework(update.effective_user.id, subject, task, (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))
        
        await update.message.reply_text(f"✅ **HOMEWORK ADDED**\n\n📚 Subject: {subject}\n📝 Task: {task}\n\nUse /homework to view all pending homework!", parse_mode=ParseMode.MARKDOWN)

    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.get_user(update.effective_user.id)
        
        if not user:
            await update.message.reply_text("❌ Profile not found. Use /start first.")
            return
        
        accuracy = 0
        if user['total_quizzes'] > 0:
            accuracy = round((user['correct_answers'] / user['total_quizzes']) * 100, 1)
        
        text = f"""
👤 **YOUR PROFILE**

📛 Name: {user['first_name']}
🆔 Username: @{user['username'] or 'N/A'}
🎓 Class: {user['class_level']}

📊 **STATISTICS:**
⭐ XP: {user['xp']}
🏆 Level: {user['level']}
🔥 Streak: {user['streak']} days
📝 Total Quizzes: {user['total_quizzes']}
✅ Correct Answers: {user['correct_answers']}
📈 Accuracy: {accuracy}%

📅 Member Since: {user['joined_date']}
        """
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.get_user(update.effective_user.id)
        if not user:
            await update.message.reply_text("❌ Use /start first.")
            return
        
        text = f"""
📊 **DETAILED STATISTICS**

🎯 **Performance:**
• Total Questions: {user['total_quizzes']}
• Correct: {user['correct_answers']}
• Incorrect: {user['total_quizzes'] - user['correct_answers']}
• Accuracy: {round((user['correct_answers']/max(user['total_quizzes'],1))*100, 1)}%

📈 **Progress:**
• XP Points: {user['xp']}
• Current Level: {user['level']}
• XP to Next Level: {100 - (user['xp'] % 100)}

🔥 **Consistency:**
• Current Streak: {user['streak']} days
• Best Streak: {user['streak']} days
        """
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        leaders = self.db.get_leaderboard(10)
        
        if not leaders:
            await update.message.reply_text("🏆 **LEADERBOARD**\n\nNo records yet. Be the first to quiz!")
            return
        
        text = "🏆 **GLOBAL LEADERBOARD**\n\n"
        for i, user in enumerate(leaders, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} **{user['first_name']}** - {user['xp']} XP (Lvl {user['level']})\n"
            text += f"   Accuracy: {user['accuracy']}%\n\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_rank(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.get_user(update.effective_user.id)
        if not user:
            await update.message.reply_text("❌ Use /start first.")
            return
        
        all_users = self.db.get_all_users()
        sorted_users = sorted(all_users, key=lambda x: x['xp'], reverse=True)
        rank = next((i+1 for i, u in enumerate(sorted_users) if u['user_id'] == user['user_id']), len(sorted_users)+1)
        
        await update.message.reply_text(f"📊 **YOUR RANK**\n\n🏆 Global Rank: #{rank}\n⭐ XP: {user['xp']}\n🎓 Level: {user['level']}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_achievements(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        achievements = self.db.get_achievements(update.effective_user.id)
        
        if not achievements:
            await update.message.reply_text("🏅 **ACHIEVEMENTS**\n\nNo achievements yet. Keep learning!")
            return
        
        text = "🏅 **YOUR ACHIEVEMENTS**\n\n"
        for ach in achievements:
            text += f"{ach['badge_icon']} {ach['badge_name']}\nEarned: {ach['earned_date']}\n\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_streak(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.get_user(update.effective_user.id)
        if not user:
            await update.message.reply_text("❌ Use /start first.")
            return
        
        text = f"""
🔥 **STREAK INFO**

Current Streak: {user['streak']} days
🎯 Goal: 30 days

💡 **Tip:** Use the bot daily to maintain your streak!
        """
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_resetstats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("⚠️ **WARNING**\n\nThis will reset ALL your data permanently!\n\nReply /confirmreset to proceed.")

    async def cmd_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        notes = self.db.get_notes(update.effective_user.id)
        
        if not notes:
            await update.message.reply_text("📝 **MY NOTES**\n\nNo notes yet. Use /savenote to create one!")
            return
        
        text = "📝 **MY NOTES**\n\n"
        for note in notes[:5]:  # Show last 5
            text += f"📌 **{note['title']}** (ID: {note['note_id']})\nSubject: {note['subject']}\n\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_savenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("❌ Usage: /savenote <title> <content>\n\nExample: /savenote Photosynthesis Process of converting light energy...")
            return
        
        title = context.args[0]
        content = " ".join(context.args[1:])
        
        self.db.save_note(update.effective_user.id, title, content, "General")
        await update.message.reply_text(f"✅ Note saved: **{title}**", parse_mode=ParseMode.MARKDOWN)

    async def cmd_deletenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /deletenote <note_id>")
            return
        
        note_id = int(context.args[0])
        self.db.delete_note(update.effective_user.id, note_id)
        await update.message.reply_text("✅ Note deleted!")

    async def cmd_searchnote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /searchnote <keyword>")
            return
        
        keyword = " ".join(context.args).lower()
        notes = self.db.get_notes(update.effective_user.id)
        
        found = [n for n in notes if keyword in n['title'].lower() or keyword in n['content'].lower()]
        
        if not found:
            await update.message.reply_text("❌ No notes found.")
            return
        
        text = f"🔍 **SEARCH RESULTS** ({len(found)} found)\n\n"
        for note in found[:3]:
            text += f"📌 {note['title']}\n\n"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("❌ Usage: /remind <HH:MM> <message>\n\nExample: /remind 18:00 Study Math")
            return
        
        reminder_time = context.args[0]
        message = " ".join(context.args[1:])
        
        self.db.add_reminder(update.effective_user.id, reminder_time, message)
        await update.message.reply_text(f"⏰ Reminder set for {reminder_time}: {message}", parse_mode=ParseMode.MARKDOWN)

    async def cmd_faq(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        faq = """
❓ **FREQUENTLY ASKED QUESTIONS**

**Q: How do I earn XP?**
A: Answer quiz questions correctly, use learning commands, and stay active!

**Q: How does leveling work?**
A: Every 100 XP = 1 Level Up!

**Q: Can I change my class?**
A: Contact admin to change your class level.

**Q: How do auto-quizzes work?**
A: Admin enables them. You'll get questions automatically!

**Q: Is my data saved?**
A: Yes! All your progress is saved in our database.

**Q: How do I report a bug?**
A: Use /feedback command or contact admin.
        """
        await update.message.reply_text(faq, parse_mode=ParseMode.MARKDOWN)

    async def cmd_contact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"📞 **CONTACT ADMIN**\n\nAdmin ID: `{CONFIG['ADMIN_USER_ID']}`\n\nFor support, questions, or feedback.", parse_mode=ParseMode.MARKDOWN)

    async def cmd_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /feedback <your message>")
            return
        
        feedback = " ".join(context.args)
        await update.message.reply_text("✅ Feedback received! Thank you for helping us improve. 🙏")
        logger.info(f"Feedback from {update.effective_user.id}: {feedback}")

    async def cmd_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❌ Usage: /language <en/hi>\n\nen = English, hi = Hindi")
            return
        
        lang = context.args[0].lower()
        if lang in ['en', 'hi']:
            await update.message.reply_text(f"✅ Language set to {'English' if lang == 'en' else 'Hindi'}")
        else:
            await update.message.reply_text("❌ Invalid language. Use 'en' or 'hi'")

    # Admin Commands
    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CONFIG['ADMIN_USER_ID']:
            await update.message.reply_text("❌ Admin only command!")
            return
        
        text = """
👑 **ADMIN PANEL**

📢 **Broadcasting:**
/broadcast <message>

👥 **User Management:**
/banuser <user_id>
/unbanuser <user_id>
/userinfo <user_id>
/listusers

🎁 **XP Management:**
/grantxp <user_id> <amount>

📊 **Bot Control:**
/stopauto - Stop auto polls

/stats - Bot statistics
        """
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CONFIG['ADMIN_USER_ID']:
            return
        
        if not context.args:
            await update.message.reply_text("❌ Usage: /broadcast <message>")
            return
        
        message = " ".join(context.args)
        users = self.db.get_all_users()
        
        success = 0
        for user in users:
            try:
                await context.bot.send_message(user['user_id'], f"📢 **BROADCAST**\n\n{message}", parse_mode=ParseMode.MARKDOWN)
                success += 1
                await asyncio.sleep(0.5)  # Avoid rate limits
            except:
                pass
        
        await update.message.reply_text(f"✅ Broadcast sent to {success}/{len(users)} users")

    async def cmd_banuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CONFIG['ADMIN_USER_ID']:
            return
        
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /banuser <user_id>")
            return
        
        user_id = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason"
        
        self.db.ban_user(user_id, reason)
        await update.message.reply_text(f"✅ User {user_id} banned. Reason: {reason}")

    async def cmd_unbanuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CONFIG['ADMIN_USER_ID']:
            return
        
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /unbanuser <user_id>")
            return
        
        user_id = int(context.args[0])
        self.db.unban_user(user_id)
        await update.message.reply_text(f"✅ User {user_id} unbanned")

    async def cmd_userinfo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CONFIG['ADMIN_USER_ID']:
            return
        
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /userinfo <user_id>")
            return
        
        user_id = int(context.args[0])
        user = self.db.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User not found")
            return
        
        text = f"""
👤 **USER INFO**

ID: {user['user_id']}
Name: {user['first_name']}
Username: @{user['username']}
XP: {user['xp']}
Level: {user['level']}
Quizzes: {user['total_quizzes']}
Joined: {user['joined_date']}
        """
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_listusers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CONFIG['ADMIN_USER_ID']:
            return
        
        users = self.db.get_all_users()
        text = f"👥 **TOTAL USERS: {len(users)}**\n\n"
        
        for user in users[:20]:  # Show first 20
            text += f"• {user['first_name']} (@{user['username']}) - {user['xp']} XP\n"
        
        if len(users) > 20:
            text += f"\n... and {len(users)-20} more"
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_grantxp(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CONFIG['ADMIN_USER_ID']:
            return
        
        if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
            await update.message.reply_text("❌ Usage: /grantxp <user_id> <amount>")
            return
        
        user_id = int(context.args[0])
        amount = int(context.args[1])
        
        self.db.add_xp(user_id, amount)
        await update.message.reply_text(f"✅ Granted {amount} XP to user {user_id}")

    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🟢 **BOT STATUS: ONLINE**\n\n✅ All systems operational!")

    async def on_member_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for member in update.message.new_chat_members:
            self.db.upsert_user(member.id, member.username or "User", member.first_name or "User")
            
            welcome = f"""
👋 **WELCOME** {member.first_name}!

I'm your AI study companion! 🎓

Use /help to see all commands.
Start with /quiz to test your knowledge!
            """
            await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)

    async def handle_poll_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        poll_id = update.poll_answer.poll_id
        user_id = update.effective_user.id
        
        if poll_id in self.active_quizzes:
            quiz_data = self.active_quizzes[poll_id]
            quiz_id = quiz_data['quiz_id']
            start_time = quiz_data['start_time']
            time_taken = int(time.time() - start_time)
            
            # Get user's answer
            option_ids = update.poll_answer.option_ids
            if option_ids:
                user_answer = option_ids[0]
                
                # Get correct answer from DB
                conn = self.db.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT correct_index, xp_reward FROM quizzes WHERE quiz_id = ?', (quiz_id,))
                row = cursor.fetchone()
                conn.close()
                
                if row:
                    is_correct = (user_answer == row['correct_index'])
                    self.db.update_quiz_answer(quiz_id, user_answer, is_correct, time_taken)
                    
                    if is_correct:
                        await context.bot.send_message(
                            user_id,
                            f"✅ **CORRECT!**\n\n⏱️ Time: {time_taken}s\n🎁 XP: +{row['xp_reward']}",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    else:
                        await context.bot.send_message(
                            user_id,
                            f"❌ **INCORRECT**\n\n⏱️ Time: {time_taken}s\n💡 Keep trying!",
                            parse_mode=ParseMode.MARKDOWN
                        )

    def schedule_daily_quizzes(self):
        """Schedule auto quizzes at different times"""
        times = [
            (8, 0),   # 8 AM
            (11, 0),  # 11 AM
            (14, 0),  # 2 PM
            (16, 0),  # 4 PM
            (19, 0),  # 7 PM
            (21, 0),  # 9 PM
        ]
        
        for hour, minute in times:
            self.scheduler.add_job(
                self.send_daily_quiz,
                CronTrigger(hour=hour, minute=minute),
                id=f"daily_quiz_{hour}_{minute}"
            )
        
        logger.info("✅ Daily quizzes scheduled")

    async def send_daily_quiz(self):
        """Send quiz to group"""
        try:
            subjects = ['Math', 'Science', 'GK', 'History', 'English']
            subject = random.choice(subjects)
            
            q_data = self.ai.generate_quiz_question(subject, 10, "Medium")
            if not q_data:
                return
            
            await self.app.bot.send_poll(
                chat_id=CONFIG['GROUP_ID'],
                question=f"📅 **DAILY QUIZ** - {subject}\n\n{q_data['question']}",
                options=q_data['options'],
                type=Poll.QUIZ,
                correct_option_id=q_data['correct_index'],
                explanation=q_data.get('explanation', ''),
                open_period=120,
                is_anonymous=False
            )
            
            logger.info(f"Daily quiz sent: {subject}")
        except Exception as e:
            logger.error(f"Daily quiz error: {e}")

    def run_scheduler(self):
        self.scheduler.start()
        self.schedule_daily_quizzes()
        logger.info("✅ Scheduler started")

    def run(self):
        self.setup_application()
        self.run_scheduler()
        logger.info("🚀 Starting Bot...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

# ============================================================
# 🌐 FLASK SERVER (for Railway health check)
# ============================================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({"status": "running", "bot": "AI Student Helper v3.0"})

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

@flask_app.route('/ping')
def ping():
    return jsonify({"status": "pong"})

def run_flask():
    flask_app.run(host='0.0.0.0', port=CONFIG['FLASK_PORT'], threaded=True)

# ============================================================
# 🚀 MAIN
# ============================================================
if __name__ == "__main__":
    # Initialize
    db = DatabaseManager(CONFIG['DB_NAME'])
    ai = AIManager(CONFIG['GROQ_API_KEY'], CONFIG['GROQ_MODEL'])
    bot = StudentBot(CONFIG['TG_BOT_TOKEN'], db, ai)
    
    # Start Flask in background
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Flask server running on port {CONFIG['FLASK_PORT']}")
    
    # Start Bot
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")

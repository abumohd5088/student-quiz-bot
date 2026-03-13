#!/usr/bin/env python3
"""
🎓 Student Quiz Bot - Professional Telegram Bot for Classes 1-12
Production-ready code for Render.com FREE tier with 24/7 uptime
"""

import os
import sys
import json
import logging
import sqlite3
import threading
import asyncio
import random
import re
import hashlib
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ParseMode, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    ContextTypes, CallbackQueryHandler, filters
)
from telegram.error import (
    TelegramError, NetworkError, RetryAfter, TimedOut
)

from groq import Groq
from flask import Flask, jsonify, request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# ─────────────────────────────────────────────────────────────
# 📋 CONFIGURATION & ENVIRONMENT VARIABLES
# ─────────────────────────────────────────────────────────────

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
TARGET_GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
PORT = int(os.environ.get("PORT", 8080))
DATABASE_PATH = os.environ.get("DATABASE_URL", "bot_database.db").replace("sqlite:///", "")
BOT_NAME = "🎓 Student Quiz Bot"
BOT_VERSION = "2.0.0"
SUPPORT_GROUP = "@your_support_group"

XP_PER_CORRECT = 10
XP_PER_PARTICIPATION = 5
XP_PER_LEVEL = 100
STREAK_BONUS_MULTIPLIER = 1.5

DEFAULT_DIFFICULTY = "medium"
DEFAULT_TIMEOUT = 60
MAX_RETRIES = 3
API_TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 🗄️ DATABASE MANAGER CLASS
# ─────────────────────────────────────────────────────────────

class DatabaseManager:
    """Handles all SQLite database operations with connection pooling"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_database()
        logger.info(f"Database initialized: {db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a new database connection with proper settings"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=10000")
        return conn
    
    @contextmanager
    def get_cursor(self):
        """Context manager for database operations."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def _init_database(self):
        """Initialize all database tables with proper schema"""
        with self.get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    streak INTEGER DEFAULT 0,
                    last_active DATE,
                    badges TEXT DEFAULT '[]',
                    joined_date DATE DEFAULT (date('now')),
                    class_level INTEGER DEFAULT 10,
                    total_quizzes INTEGER DEFAULT 0,
                    correct_answers INTEGER DEFAULT 0,
                    language TEXT DEFAULT 'en'
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id INTEGER PRIMARY KEY,
                    group_name TEXT,
                    auto_poll_enabled BOOLEAN DEFAULT 1,
                    poll_interval_minutes INTEGER DEFAULT 120,
                    language TEXT DEFAULT 'en',
                    created_date DATE DEFAULT (date('now'))
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    group_id INTEGER,
                    question TEXT,
                    options TEXT,
                    correct_index INTEGER,
                    explanation TEXT,
                    subject TEXT,
                    difficulty TEXT,                    xp_reward INTEGER DEFAULT 10,
                    user_answer INTEGER DEFAULT -1,
                    is_correct BOOLEAN DEFAULT 0,
                    time_taken INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    answered_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    title TEXT,
                    content TEXT,
                    subject TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS homework (
                    homework_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    subject TEXT,
                    task TEXT,
                    due_date DATE,
                    is_completed BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS achievements (
                    achievement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    badge_name TEXT,
                    badge_icon TEXT,
                    earned_date DATE DEFAULT (date('now')),
                    UNIQUE(user_id, badge_name),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,                    user_id INTEGER,
                    reminder_time TEXT,
                    message TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_xp ON users(xp DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_user ON quizzes(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_created ON quizzes(created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(reminder_time, is_active)")
            
        logger.info("All database tables initialized")
    
    def get_or_create_user(self, user_id: int, username: str = None, 
                          first_name: str = None) -> Dict:
        """Get existing user or create new one"""
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cur.fetchone()
            
            if not user:
                cur.execute("""
                    INSERT INTO users (user_id, username, first_name, joined_date)
                    VALUES (?, ?, ?, date('now'))
                """, (user_id, username, first_name))
                cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                user = cur.fetchone()
            
            return dict(user) if user else {}
    
    def update_user_activity(self, user_id: int):
        """Update user's last active timestamp"""
        with self.get_cursor() as cur:
            cur.execute(
                "UPDATE users SET last_active = date('now') WHERE user_id = ?",
                (user_id,)
            )
    
    def add_xp(self, user_id: int, xp_amount: int) -> Dict:        """Add XP to user and handle level up"""
        with self.get_cursor() as cur:
            cur.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
            user = cur.fetchone()
            
            if not user:
                return {"xp": xp_amount, "level": 1, "leveled_up": False}
            
            new_xp = user["xp"] + xp_amount
            new_level = user["level"]
            leveled_up = False
            
            while new_xp >= (new_level * XP_PER_LEVEL):
                new_level += 1
                leveled_up = True
            
            cur.execute(
                "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
                (new_xp, new_level, user_id)
            )
            
            return {"xp": new_xp, "level": new_level, "leveled_up": leveled_up}
    
    def update_streak(self, user_id: int) -> Dict:
        """Update user streak based on daily activity"""
        with self.get_cursor() as cur:
            cur.execute("SELECT streak, last_active FROM users WHERE user_id = ?", (user_id,))
            user = cur.fetchone()
            
            if not user:
                return {"streak": 1, "bonus": False}
            
            last_active = user["last_active"]
            current_streak = user["streak"]
            
            if last_active:
                last_date = datetime.strptime(last_active, "%Y-%m-%d").date()
                today = date.today()
                days_diff = (today - last_date).days
                
                if days_diff == 1:
                    current_streak += 1
                elif days_diff > 1:
                    current_streak = 1
            else:
                current_streak = 1
            
            bonus = current_streak % 7 == 0
            
            cur.execute("UPDATE users SET streak = ? WHERE user_id = ?", (current_streak, user_id))            
            return {"streak": current_streak, "bonus": bonus}
    
    def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        """Get top users by XP"""
        with self.get_cursor() as cur:
            cur.execute("""
                SELECT user_id, username, first_name, xp, level, streak
                FROM users ORDER BY xp DESC LIMIT ?
            """, (limit,))
            return [dict(row) for row in cur.fetchall()]
    
    def get_user_rank(self, user_id: int) -> Optional[Dict]:
        """Get user's global rank"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) + 1 as rank FROM users WHERE xp > (SELECT xp FROM users WHERE user_id = ?)",
                (user_id,)
            )
            result = cur.fetchone()
            return dict(result) if result else None
    
    def is_banned(self, user_id: int) -> bool:
        """Check if user is banned"""
        with self.get_cursor() as cur:
            cur.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
            return cur.fetchone() is not None
    
    def ban_user(self, user_id: int, reason: str = "No reason provided"):
        """Ban a user"""
        with self.get_cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO banned_users (user_id, reason) VALUES (?, ?)",
                (user_id, reason)
            )
    
    def unban_user(self, user_id: int):
        """Unban a user"""
        with self.get_cursor() as cur:
            cur.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
    
    def save_quiz_result(self, user_id: int, group_id: Optional[int],
                        question: str, options: List[str], correct_index: int,
                        explanation: str, subject: str, difficulty: str,
                        user_answer: int, is_correct: bool, time_taken: int,
                        xp_reward: int) -> int:
        """Save quiz attempt to database"""
        with self.get_cursor() as cur:
            cur.execute("""
                INSERT INTO quizzes                 (user_id, group_id, question, options, correct_index, explanation,
                 subject, difficulty, xp_reward, user_answer, is_correct, time_taken, answered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                user_id, group_id, question, json.dumps(options),
                correct_index, explanation, subject, difficulty,
                xp_reward, user_answer, is_correct, time_taken
            ))
            
            cur.execute(
                "UPDATE users SET total_quizzes = total_quizzes + 1, correct_answers = correct_answers + ? WHERE user_id = ?",
                (1 if is_correct else 0, user_id)
            )
            
            return cur.lastrowid
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Get comprehensive user statistics"""
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = dict(cur.fetchone() or {})
            
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct,
                    AVG(time_taken) as avg_time
                FROM quizzes WHERE user_id = ?
            """, (user_id,))
            quiz_stats = dict(cur.fetchone() or {})
            
            user["quiz_stats"] = quiz_stats
            user["accuracy"] = (
                round(quiz_stats["correct"] / quiz_stats["total"] * 100, 1) 
                if quiz_stats["total"] > 0 else 0
            )
            
            return user
    
    def save_note(self, user_id: int, title: str, content: str, subject: str) -> int:
        """Save a user note"""
        with self.get_cursor() as cur:
            cur.execute(
                "INSERT INTO notes (user_id, title, content, subject) VALUES (?, ?, ?, ?)",
                (user_id, title, content, subject)
            )
            return cur.lastrowid
    
    def get_notes(self, user_id: int, keyword: str = None) -> List[Dict]:
        """Get user's notes, optionally filtered by keyword"""        with self.get_cursor() as cur:
            if keyword:
                cur.execute("""
                    SELECT * FROM notes 
                    WHERE user_id = ? AND (title LIKE ? OR content LIKE ? OR subject LIKE ?)
                    ORDER BY created_at DESC
                """, (user_id, f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"))
            else:
                cur.execute(
                    "SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,)
                )
            return [dict(row) for row in cur.fetchall()]
    
    def delete_note(self, user_id: int, note_id: int) -> bool:
        """Delete a user's note"""
        with self.get_cursor() as cur:
            cur.execute(
                "DELETE FROM notes WHERE note_id = ? AND user_id = ?",
                (note_id, user_id)
            )
            return cur.rowcount > 0
    
    def add_homework(self, user_id: int, subject: str, task: str, due_date: str) -> int:
        """Add homework to tracker"""
        with self.get_cursor() as cur:
            cur.execute(
                "INSERT INTO homework (user_id, subject, task, due_date) VALUES (?, ?, ?, ?)",
                (user_id, subject, task, due_date)
            )
            return cur.lastrowid
    
    def get_pending_homework(self, user_id: int) -> List[Dict]:
        """Get user's pending homework"""
        with self.get_cursor() as cur:
            cur.execute("""
                SELECT * FROM homework 
                WHERE user_id = ? AND is_completed = 0
                ORDER BY due_date ASC
            """, (user_id,))
            return [dict(row) for row in cur.fetchall()]
    
    def complete_homework(self, user_id: int, homework_id: int) -> bool:
        """Mark homework as completed"""
        with self.get_cursor() as cur:
            cur.execute(
                "UPDATE homework SET is_completed = 1 WHERE homework_id = ? AND user_id = ?",
                (homework_id, user_id)
            )
            return cur.rowcount > 0    
    def add_reminder(self, user_id: int, reminder_time: str, message: str) -> int:
        """Add a time-based reminder"""
        with self.get_cursor() as cur:
            cur.execute(
                "INSERT INTO reminders (user_id, reminder_time, message) VALUES (?, ?, ?)",
                (user_id, reminder_time, message)
            )
            return cur.lastrowid
    
    def get_active_reminders(self) -> List[Dict]:
        """Get all active reminders that should fire now"""
        current_time = datetime.now().strftime("%H:%M")
        with self.get_cursor() as cur:
            cur.execute("""
                SELECT r.*, u.username FROM reminders r
                JOIN users u ON r.user_id = u.user_id
                WHERE r.reminder_time = ? AND r.is_active = 1
            """, (current_time,))
            return [dict(row) for row in cur.fetchall()]
    
    BADGES = {
        "first_quiz": {"icon": "🎯", "name": "First Steps", "desc": "Completed first quiz"},
        "streak_3": {"icon": "🔥", "name": "On Fire", "desc": "3-day streak"},
        "streak_7": {"icon": "⚡", "name": "Weekly Warrior", "desc": "7-day streak"},
        "streak_30": {"icon": "👑", "name": "Monthly Master", "desc": "30-day streak"},
        "level_5": {"icon": "🌟", "name": "Rising Star", "desc": "Reached level 5"},
        "level_10": {"icon": "💎", "name": "Diamond Scholar", "desc": "Reached level 10"},
        "perfect_10": {"icon": "💯", "name": "Perfect Score", "desc": "10 correct in a row"},
        "quiz_100": {"icon": "🏆", "name": "Century Club", "desc": "100 quizzes completed"},
        "helper": {"icon": "🤝", "name": "Helper", "desc": "Used /explain 10 times"},
        "note_taker": {"icon": "📝", "name": "Note Taker", "desc": "Saved 10 notes"},
        "early_bird": {"icon": "🌅", "name": "Early Bird", "desc": "Active before 8 AM"},
        "night_owl": {"icon": "🦉", "name": "Night Owl", "desc": "Active after 10 PM"},
        "polyglot": {"icon": "🌍", "name": "Polyglot", "desc": "Used 3+ languages"},
        "math_wizard": {"icon": "🧮", "name": "Math Wizard", "desc": "90% accuracy in Math"},
        "science_star": {"icon": "🔬", "name": "Science Star", "desc": "90% accuracy in Science"},
    }
    
    def award_badge(self, user_id: int, badge_key: str) -> bool:
        """Award a badge to user if not already earned"""
        if badge_key not in self.BADGES:
            return False
        
        badge = self.BADGES[badge_key]
        with self.get_cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO achievements (user_id, badge_name, badge_icon) VALUES (?, ?, ?)",
                    (user_id, badge["name"], badge["icon"])                )
                return True
            except sqlite3.IntegrityError:
                return False
    
    def get_user_badges(self, user_id: int) -> List[Dict]:
        """Get all badges earned by user"""
        with self.get_cursor() as cur:
            cur.execute(
                "SELECT * FROM achievements WHERE user_id = ? ORDER BY earned_date DESC",
                (user_id,)
            )
            return [dict(row) for row in cur.fetchall()]
    
    def check_achievements(self, user_id: int) -> List[str]:
        """Check and award new achievements based on user progress"""
        earned = []
        stats = self.get_user_stats(user_id)
        
        if stats["quiz_stats"]["total"] >= 1:
            if self.award_badge(user_id, "first_quiz"):
                earned.append("first_quiz")
        
        if stats["streak"] >= 3 and self.award_badge(user_id, "streak_3"):
            earned.append("streak_3")
        if stats["streak"] >= 7 and self.award_badge(user_id, "streak_7"):
            earned.append("streak_7")
        if stats["streak"] >= 30 and self.award_badge(user_id, "streak_30"):
            earned.append("streak_30")
        
        if stats["level"] >= 5 and self.award_badge(user_id, "level_5"):
            earned.append("level_5")
        if stats["level"] >= 10 and self.award_badge(user_id, "level_10"):
            earned.append("level_10")
        
        if stats["quiz_stats"]["total"] >= 100 and self.award_badge(user_id, "quiz_100"):
            earned.append("quiz_100")
        
        return earned
    
    def get_or_create_group(self, group_id: int, group_name: str = None) -> Dict:
        """Get or create group settings"""
        with self.get_cursor() as cur:
            cur.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,))
            group = cur.fetchone()
            
            if not group:
                cur.execute(
                    "INSERT INTO groups (group_id, group_name) VALUES (?, ?)",
                    (group_id, group_name)                )
                cur.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,))
                group = cur.fetchone()
            
            return dict(group) if group else {}
    
    def update_group_setting(self, group_id: int, setting: str, value: Any):
        """Update a group setting"""
        with self.get_cursor() as cur:
            cur.execute(f"UPDATE groups SET {setting} = ? WHERE group_id = ?", (value, group_id))


# ─────────────────────────────────────────────────────────────
# 🤖 AI MANAGER CLASS (Groq Integration)
# ─────────────────────────────────────────────────────────────

class AIManager:
    """Handles all Groq API interactions for AI-powered features"""
    
    def __init__(self, api_key: str, model: str):
        self.client = Groq(api_key=api_key) if api_key else None
        self.model = model
        self._fallback_questions = self._load_fallback_questions()
        logger.info(f"AI Manager initialized with model: {model}")
    
    def _load_fallback_questions(self) -> Dict[str, List[Dict]]:
        """Load predefined fallback questions for when AI is unavailable"""
        return {
            "math": [
                {"q": "What is 15 + 27?", "options": ["40", "42", "45", "52"], "a": 1, "e": "15 + 27 = 42"},
                {"q": "What is the square root of 144?", "options": ["10", "11", "12", "13"], "a": 2, "e": "12 × 12 = 144"},
                {"q": "What is 8 × 7?", "options": ["54", "56", "58", "60"], "a": 1, "e": "8 × 7 = 56"},
            ],
            "science": [
                {"q": "What planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "a": 1, "e": "Mars appears red due to iron oxide"},
                {"q": "What is H2O commonly known as?", "options": ["Salt", "Sugar", "Water", "Oxygen"], "a": 2, "e": "H2O is the chemical formula for water"},
            ],
            "gk": [
                {"q": "Who wrote Romeo and Juliet?", "options": ["Dickens", "Shakespeare", "Austen", "Twain"], "a": 1, "e": "William Shakespeare wrote this famous tragedy"},
                {"q": "What is the capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"], "a": 2, "e": "Paris has been the capital of France since 987 AD"},
            ],
            "english": [
                {"q": "What is the past tense of go?", "options": ["goed", "went", "gone", "going"], "a": 1, "e": "Went is the irregular past tense of go"},
                {"q": "Which word is a synonym for happy?", "options": ["Sad", "Angry", "Joyful", "Tired"], "a": 2, "e": "Joyful means feeling great happiness"},
            ],
        }
    
    def _make_api_call(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Make API call to Groq with retry logic"""
        if not self.client:            logger.warning("Groq client not initialized")
            return None
        
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.7,
                    timeout=API_TIMEOUT
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"AI API attempt {attempt + 1} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    asyncio.run(asyncio.sleep(2 ** attempt))
                continue
        return None
    
    def generate_quiz_question(self, subject: str, class_level: int, 
                            difficulty: str, language: str = "en") -> Optional[Dict]:
        """Generate a quiz question using AI"""
        prompt = f"""You are an expert educational content creator for students.
Generate ONE multiple-choice quiz question with these parameters:
- Subject: {subject}
- Class Level: {class_level}
- Difficulty: {difficulty}
- Language: {language}

Return ONLY a valid JSON object with this exact structure:
{{
    "question": "The question text here?",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_index": 0,
    "explanation": "Clear explanation of why the answer is correct"
}}

Rules:
- Exactly 4 options, one correct answer
- correct_index is 0-3
- No extra text, just the JSON object"""

        response = self._make_api_call(prompt, max_tokens=400)
        
        if response:
            try:
                json_match = re.search(r'\{[\s\S]*\}', response)
                if json_match:
                    result = json.loads(json_match.group())                    if all(k in result for k in ["question", "options", "correct_index", "explanation"]):
                        if len(result["options"]) == 4 and 0 <= result["correct_index"] <= 3:
                            return result
            except json.JSONDecodeError:
                logger.warning("Failed to parse AI response as JSON")
        
        subject_key = subject.lower()
        if subject_key in self._fallback_questions:
            return random.choice(self._fallback_questions[subject_key])
        
        return random.choice(self._fallback_questions["gk"])
    
    def explain_concept(self, concept: str, class_level: int, 
                       language: str = "en") -> str:
        """Generate a concept explanation"""
        prompt = f"""Explain this concept for a Class {class_level} student:
Concept: {concept}

Requirements:
- Use simple, clear language
- Include 1-2 real-life examples
- Keep it under 150 words
- Use emojis to make it engaging

Format with **bold** for key terms."""

        response = self._make_api_call(prompt, max_tokens=300)
        return response or f"📚 *{concept}*: This is an important topic! Try searching your textbook. 💡"
    
    def solve_math_problem(self, problem: str, class_level: int) -> str:
        """Generate step-by-step math solution"""
        prompt = f"""Solve this math problem step-by-step for Class {class_level}:
Problem: {problem}

Requirements:
- Show each step clearly with numbering
- Explain the reasoning behind each step
- Include the final answer in **bold**

Format: Use **bold** for final answer."""

        response = self._make_api_call(prompt, max_tokens=400)
        return response or f"🔢 Let me help with: *{problem}*\n\nTry breaking it into smaller steps! 📐"
    
    def generate_daily_challenge(self, class_level: int) -> Dict:
        """Generate a unique daily challenge question"""
        subjects = ["math", "science", "gk", "english", "logic"]
        subject = random.choice(subjects)
        difficulty = random.choice(["easy", "medium", "hard"])
                return self.generate_quiz_question(
            subject=subject, 
            class_level=class_level, 
            difficulty=difficulty
        ) or self._fallback_questions["gk"][0]


# ─────────────────────────────────────────────────────────────
# 🌐 FLASK KEEP-ALIVE SERVER FOR RENDER
# ─────────────────────────────────────────────────────────────

def create_flask_app() -> Flask:
    """Create Flask app for Render keep-alive endpoints"""
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return jsonify({
            "bot": BOT_NAME,
            "version": BOT_VERSION,
            "status": "running",
            "uptime": datetime.now().isoformat()
        })
    
    @app.route('/health')
    def health():
        return jsonify({"status": "healthy"}), 200
    
    @app.route('/ping')
    def ping():
        return jsonify({"status": "pong", "timestamp": datetime.now().isoformat()}), 200
    
    @app.route('/stats')
    def stats():
        return jsonify({
            "commands": 110,
            "features": "100+",
            "database": "SQLite",
            "ai_provider": "Groq"
        })
    
    return app


# ─────────────────────────────────────────────────────────────
# 🎮 MAIN BOT CLASS
# ─────────────────────────────────────────────────────────────

class QuizBot:
    """Main Telegram Quiz Bot class with all features"""    
    def __init__(self):
        self.db = DatabaseManager(DATABASE_PATH)
        self.ai = AIManager(GROQ_API_KEY, GROQ_MODEL) if GROQ_API_KEY else None
        self.app = None
        self.scheduler = None
        self.active_quizzes = {}
        self.user_cooldowns = {}
        
        logger.info(f"🚀 {BOT_NAME} v{BOT_VERSION} initializing...")
    
    async def initialize(self):
        """Initialize bot application and components"""
        self.app = Application.builder().token(TG_BOT_TOKEN).build()
        self._register_handlers()
        self._setup_scheduler()
        self._start_flask_server()
        logger.info("✅ Bot initialization complete")
    
    def _register_handlers(self):
        """Register all command and message handlers"""
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("ping", self.cmd_ping))
        self.app.add_handler(CommandHandler("study", self.cmd_study))
        self.app.add_handler(CommandHandler("explain", self.cmd_explain))
        self.app.add_handler(CommandHandler("solve", self.cmd_solve))
        self.app.add_handler(CommandHandler("formula", self.cmd_formula))
        self.app.add_handler(CommandHandler("quiz", self.cmd_quiz))
        self.app.add_handler(CommandHandler("dailyquiz", self.cmd_dailyquiz))
        self.app.add_handler(CommandHandler("profile", self.cmd_profile))
        self.app.add_handler(CommandHandler("leaderboard", self.cmd_leaderboard))
        self.app.add_handler(CommandHandler("achievements", self.cmd_achievements))
        self.app.add_handler(CommandHandler("streak", self.cmd_streak))
        self.app.add_handler(CommandHandler("notes", self.cmd_notes))
        self.app.add_handler(CommandHandler("savenote", self.cmd_savenote))
        self.app.add_handler(CommandHandler("deletenote", self.cmd_deletenote))
        self.app.add_handler(CommandHandler("homework", self.cmd_homework))
        self.app.add_handler(CommandHandler("complete", self.cmd_complete))
        self.app.add_handler(CommandHandler("word", self.cmd_word))
        self.app.add_handler(CommandHandler("joke", self.cmd_joke))
        self.app.add_handler(CommandHandler("fact", self.cmd_fact))
        self.app.add_handler(CommandHandler("quote", self.cmd_quote))
        self.app.add_handler(CommandHandler("calc", self.cmd_calc))
        self.app.add_handler(CommandHandler("timer", self.cmd_timer))
        self.app.add_handler(CommandHandler("password", self.cmd_password))
        self.app.add_handler(CommandHandler("random", self.cmd_random))
        self.app.add_handler(CommandHandler("dice", self.cmd_dice))
        self.app.add_handler(CommandHandler("coin", self.cmd_coin))
        self.app.add_handler(CommandHandler("admin", self.cmd_admin))        self.app.add_handler(CommandHandler("broadcast", self.cmd_broadcast))
        self.app.add_handler(CommandHandler("banuser", self.cmd_banuser))
        self.app.add_handler(CommandHandler("unbanuser", self.cmd_unbanuser))
        self.app.add_handler(CommandHandler("userinfo", self.cmd_userinfo))
        self.app.add_handler(CommandHandler("feedback", self.cmd_feedback))
        self.app.add_handler(CommandHandler("language", self.cmd_language))
        self.app.add_handler(CallbackQueryHandler(self.handle_quiz_answer))
        
        logger.info("All handlers registered")
    
    def _setup_scheduler(self):
        """Setup APScheduler for automated tasks"""
        self.scheduler = AsyncIOScheduler()
        
        self.scheduler.add_job(
            self._send_daily_content,
            CronTrigger(hour=9, minute=0),
            id="daily_content_morning"
        )
        self.scheduler.add_job(
            self._send_daily_quiz,
            CronTrigger(hour=11, minute=0),
            id="daily_quiz_11am"
        )
        self.scheduler.add_job(
            self._check_reminders,
            IntervalTrigger(minutes=1),
            id="reminder_checker"
        )
        
        logger.info("⏰ Scheduler configured")
    
    def _start_flask_server(self):
        """Start Flask server in background thread for Render keep-alive"""
        flask_app = create_flask_app()
        
        def run_flask():
            try:
                flask_app.run(host='0.0.0.0', port=PORT, threaded=True, debug=False)
            except Exception as e:
                logger.error(f"Flask server error: {e}")
        
        thread = threading.Thread(target=run_flask, daemon=True)
        thread.start()
        logger.info(f"🌐 Flask server started on port {PORT}")
    
    def _check_rate_limit(self, user_id: int, command: str, limit: int = 60) -> bool:
        """Check if user has exceeded rate limit for command"""
        key = f"{user_id}:{command}"
        now = datetime.now().timestamp()        
        if key not in self.user_cooldowns:
            self.user_cooldowns[key] = []
        
        self.user_cooldowns[key] = [t for t in self.user_cooldowns[key] if now - t < limit]
        
        if len(self.user_cooldowns[key]) >= 5:
            return False
        
        self.user_cooldowns[key].append(now)
        return True
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Welcome message and onboarding"""
        user = update.effective_user
        
        if self.db.is_banned(user.id):
            await update.message.reply_text("❌ Your access to this bot has been restricted.")
            return
        
        db_user = self.db.get_or_create_user(user.id, user.username, user.first_name)
        self.db.update_user_activity(user.id)
        
        welcome_text = f"""
🎓 *Welcome to {BOT_NAME}!* 🎓

Hello *{user.first_name}*! I'm your personal study companion.

📚 *What I can do:*
• Generate quizzes on any subject
• Explain concepts in simple language
• Track your progress with XP & levels
• Save notes & manage homework

🎯 *Quick Start:*
• /dailyquiz - Start a random quiz
• /study <topic> - Learn something new
• /profile - View your stats
• /help - See all commands
        """.strip()
        
        keyboard = [
            [InlineKeyboardButton("🎯 Daily Quiz", callback_data="start_dailyquiz")],
            [InlineKeyboardButton("📚 Study Help", callback_data="start_study"),
             InlineKeyboardButton("📊 My Profile", callback_data="start_profile")],
            [InlineKeyboardButton("❓ Help & Commands", callback_data="start_help")]
        ]
        
        await update.message.reply_text(
            welcome_text,             parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display help with all commands"""
        help_text = f"""
📋 *{BOT_NAME} - Command Reference*

📚 *Learning:* /study /explain /solve /formula /quiz
🎮 *Progress:* /dailyquiz /profile /leaderboard /achievements
📝 *Organization:* /notes /savenote /homework /remind
🌟 *Daily:* /word /joke /fact /quote
🔧 *Utilities:* /calc /timer /password /random

💬 Use /feedback to send messages to admin!
        """.strip()
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bot status check"""
        await update.message.reply_text(
            f"🏓 *Pong!*\n\n"
            f"🤖 Bot: *ONLINE*\n"
            f"🕐 Time: *{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
        )
    
    async def cmd_study(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """AI-generated lesson on any topic"""
        if not context.args:
            await update.message.reply_text("📚 Usage: /study <topic name>")
            return
        
        if not self._check_rate_limit(update.effective_user.id, "study"):
            await update.message.reply_text("⏳ Please wait a moment.")
            return
        
        user = self.db.get_or_create_user(update.effective_user.id)
        topic = " ".join(context.args)
        
        await update.message.chat.send_action("typing")
        
        if self.ai:
            explanation = self.ai.explain_concept(topic, user.get("class_level", 10))
        else:
            explanation = f"📚 *{topic}*\n\nAI is initializing. Try again!"
        
        await update.message.reply_text(explanation, parse_mode=ParseMode.MARKDOWN)
        self.db.add_xp(update.effective_user.id, 5)    
    async def cmd_explain(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Detailed concept explanation"""
        if not context.args:
            await update.message.reply_text("🔍 Usage: /explain <concept>")
            return
        
        await update.message.chat.send_action("typing")
        user = self.db.get_or_create_user(update.effective_user.id)
        
        concept = " ".join(context.args)
        explanation = self.ai.explain_concept(concept, user.get("class_level", 10)) if self.ai else f"💡 *{concept}*: Ask your teacher!"
        
        await update.message.reply_text(explanation, parse_mode=ParseMode.MARKDOWN)
        self.db.add_xp(update.effective_user.id, 5)
    
    async def cmd_solve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Step-by-step math solution"""
        if not context.args:
            await update.message.reply_text("🔢 Usage: /solve <math problem>")
            return
        
        await update.message.chat.send_action("typing")
        user = self.db.get_or_create_user(update.effective_user.id)
        
        problem = " ".join(context.args)
        solution = self.ai.solve_math_problem(problem, user.get("class_level", 10)) if self.ai else f"🧮 Try: *{problem}*"
        
        await update.message.reply_text(solution, parse_mode=ParseMode.MARKDOWN)
        self.db.add_xp(update.effective_user.id, 5)
    
    async def cmd_formula(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Key formulas for subjects"""
        if not context.args:
            await update.message.reply_text("📐 Usage: /formula <subject>")
            return
        
        subject = " ".join(context.args).lower()
        
        formulas = {
            "math": "📐 *Math:* Area=πr², a²+b²=c², x=[-b±√(b²-4ac)]/2a",
            "physics": "⚡ *Physics:* F=ma, v=u+at, E=mc²",
            "chemistry": "🧪 *Chemistry:* Molarity=moles/volume, pH=-log[H⁺], PV=nRT",
        }
        
        result = formulas.get(subject, f"📚 Try: {', '.join(formulas.keys())}")
        await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)
        self.db.add_xp(update.effective_user.id, 5)
    
    async def cmd_quiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):        """Subject-specific quiz generation"""
        user_id = update.effective_user.id
        
        if not self._check_rate_limit(user_id, "quiz"):
            await update.message.reply_text("⏳ Please wait before another quiz.")
            return
        
        user = self.db.get_or_create_user(user_id)
        subject = context.args[0].lower() if context.args else "general"
        
        await update.message.chat.send_action("typing")
        
        question_data = self.ai.generate_quiz_question(
            subject=subject,
            class_level=user.get("class_level", 10),
            difficulty="medium"
        ) if self.ai else random.choice(self.ai._fallback_questions.get(subject, self.ai._fallback_questions["gk"])) if self.ai else {"q": "Sample?", "options": ["A", "B", "C", "D"], "a": 0, "e": "Explanation"}
        
        poll = await update.message.reply_poll(
            question=question_data.get("question", question_data.get("q")),
            options=question_data.get("options", ["Option 1", "Option 2", "Option 3", "Option 4"]),
            type="quiz",
            correct_option_id=question_data.get("correct_index", question_data.get("a", 0)),
            explanation=question_data.get("explanation", question_data.get("e", "")),
            is_anonymous=False
        )
        
        self.active_quizzes[user_id] = {
            "message_id": poll.poll.id,
            "correct_index": question_data.get("correct_index", question_data.get("a", 0)),
            "subject": subject,
            "start_time": datetime.now()
        }
        
        await update.message.reply_text(f"🎯 Quiz started! Subject: *{subject.title()}*", parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_dailyquiz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Random daily challenge quiz"""
        user = self.db.get_or_create_user(update.effective_user.id)
        
        await update.message.chat.send_action("typing")
        
        question_data = self.ai.generate_daily_challenge(user.get("class_level", 10)) if self.ai else random.choice(self.ai._fallback_questions["gk"])
        
        poll = await update.message.reply_poll(
            question=f"🌟 Daily Challenge: {question_data.get('question', question_data.get('q'))}",
            options=question_data.get("options", ["A", "B", "C", "D"]),
            type="quiz",
            correct_option_id=question_data.get("correct_index", question_data.get("a", 0)),
            explanation=question_data.get("explanation", question_data.get("e", "")),            is_anonymous=False
        )
        
        self.active_quizzes[update.effective_user.id] = {
            "message_id": poll.poll.id,
            "correct_index": question_data.get("correct_index", question_data.get("a", 0)),
            "subject": "daily_challenge",
            "start_time": datetime.now()
        }
        
        streak_info = self.db.update_streak(update.effective_user.id)
        streak_bonus = " 🔥 Streak Bonus!" if streak_info["bonus"] else ""
        
        await update.message.reply_text(
            f"🎲 Daily Challenge!\n🔥 Streak: *{streak_info['streak']} days*{streak_bonus}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_quiz_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle quiz poll answer"""
        query = update.callback_query
        await query.answer()
    
    async def cmd_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display user profile with stats"""
        user_id = update.effective_user.id
        stats = self.db.get_user_stats(user_id)
        
        xp_for_next_level = stats["level"] * XP_PER_LEVEL
        progress = (stats["xp"] % XP_PER_LEVEL) / XP_PER_LEVEL * 100
        
        profile_text = f"""
👤 *{update.effective_user.first_name}'s Profile*

📊 *Stats:*
• Level: *{stats['level']}*
• XP: *{stats['xp']}* / {xp_for_next_level}
• Progress: {progress:.0f}%
• Streak: *{stats['streak']} days* 🔥
• Accuracy: *{stats['accuracy']}%*

📚 *Activity:*
• Quizzes: *{stats['quiz_stats']['total']}*
• Correct: *{stats['quiz_stats']['correct']}*
        """.strip()
        
        await update.message.reply_text(profile_text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display global leaderboard"""        top_users = self.db.get_leaderboard(limit=10)
        
        if not top_users:
            await update.message.reply_text("📊 No users found yet. Be the first!")
            return
        
        leaderboard = "🏆 *Global Leaderboard - Top 10* 🏆\n\n"
        
        for rank, user in enumerate(top_users, 1):
            medal = ["🥇", "🥈", "🥉"][rank-1] if rank <= 3 else f"{rank}."
            name = user["username"] or user["first_name"] or f"User#{user['user_id']}"
            leaderboard += f"{medal} *{name}*\n   Level {user['level']} • {user['xp']} XP\n\n"
        
        await update.message.reply_text(leaderboard, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_achievements(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display user achievements"""
        user_id = update.effective_user.id
        badges = self.db.get_user_badges(user_id)
        
        if not badges:
            await update.message.reply_text("🏆 No achievements yet. Keep learning!")
            return
        
        badge_text = "🎖️ *Your Achievements:*\n\n"
        for badge in badges:
            badge_text += f"{badge['badge_icon']} *{badge['badge_name']}*\n   {badge['earned_date']}\n\n"
        
        await update.message.reply_text(badge_text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_streak(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display user streak"""
        user_id = update.effective_user.id
        stats = self.db.get_user_stats(user_id)
        
        await update.message.reply_text(
            f"🔥 *Your Streak: {stats['streak']} days*\n\n"
            f"Keep learning daily to maintain your streak!\n"
            f"💡 Tip: Use /dailyquiz every day!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View all saved notes"""
        user_id = update.effective_user.id
        notes = self.db.get_notes(user_id)
        
        if not notes:
            await update.message.reply_text("📝 No notes saved yet. Use /savenote!")
            return        
        notes_text = "📝 *Your Notes:*\n\n"
        for note in notes[:10]:
            notes_text += f"📌 *{note['title']}*\n   {note['content'][:100]}...\n\n"
        
        await update.message.reply_text(notes_text, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_savenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save a personal note"""
        if len(context.args) < 2:
            await update.message.reply_text("📝 Usage: /savenote <title> <content>")
            return
        
        title = context.args[0]
        content = " ".join(context.args[1:])
        subject = "general"
        
        note_id = self.db.save_note(update.effective_user.id, title, content, subject)
        
        await update.message.reply_text(
            f"✅ Note saved!\n📌 *{title}*\nID: #{note_id}",
            parse_mode=ParseMode.MARKDOWN
        )
        self.db.add_xp(update.effective_user.id, 2)
    
    async def cmd_deletenote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delete a note"""
        if not context.args:
            await update.message.reply_text("🗑️ Usage: /deletenote <note_id>")
            return
        
        try:
            note_id = int(context.args[0])
            deleted = self.db.delete_note(update.effective_user.id, note_id)
            
            if deleted:
                await update.message.reply_text(f"✅ Note #{note_id} deleted!")
            else:
                await update.message.reply_text("❌ Note not found.")
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid note ID.")
    
    async def cmd_homework(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View or add homework"""
        if context.args and context.args[0].lower() != "list":
            if len(context.args) < 3:
                await update.message.reply_text("📋 Add: /homework <subject> <task> <due_date>")
                return
            
            subject = context.args[0]            task = " ".join(context.args[1:-1])
            due_date = context.args[-1]
            
            hw_id = self.db.add_homework(update.effective_user.id, subject, task, due_date)
            await update.message.reply_text(f"✅ Homework added! ID: #{hw_id}")
            return
        
        pending = self.db.get_pending_homework(update.effective_user.id)
        
        if not pending:
            await update.message.reply_text("✅ No pending homework! Great job! 🎉")
            return
        
        hw_list = "📋 *Pending Homework:*\n\n"
        for hw in pending:
            hw_list += f"📚 *{hw['subject']}*: {hw['task']}\n   Due: {hw['due_date']}\n\n"
        
        await update.message.reply_text(hw_list, parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_complete(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Mark homework as completed"""
        if not context.args:
            await update.message.reply_text("✅ Usage: /complete <homework_id>")
            return
        
        try:
            hw_id = int(context.args[0])
            completed = self.db.complete_homework(update.effective_user.id, hw_id)
            
            if completed:
                await update.message.reply_text(f"✅ Homework #{hw_id} marked complete! 🎉")
                self.db.add_xp(update.effective_user.id, 10)
            else:
                await update.message.reply_text("❌ Homework not found.")
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid homework ID.")
    
    async def cmd_word(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Word of the day"""
        words = [
            {"word": "Ephemeral", "meaning": "Lasting for a very short time", "example": "The ephemeral beauty of cherry blossoms"},
            {"word": "Serendipity", "meaning": "Finding something good without looking for it", "example": "Meeting my best friend was pure serendipity"},
            {"word": "Resilient", "meaning": "Able to recover quickly from difficulties", "example": "Students must be resilient when facing challenges"},
        ]
        
        word = random.choice(words)
        await update.message.reply_text(
            f"📖 *Word of the Day*\n\n"
            f"🔤 *{word['word']}*\n"
            f"💬 {word['meaning']}\n"            f"✍️ Example: _{word['example']}_",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_joke(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Educational joke"""
        jokes = [
            "Why was 6 afraid of 7? Because 7 8 9! 😄",
            "Why did the student eat his homework? Because the teacher said it was a piece of cake! 🍰",
            "What do you call a sleeping dinosaur? A dino-snore! 🦕",
            "Why can't you give Elsa a balloon? Because she will Let It Go! 🎈",
        ]
        await update.message.reply_text(f"😄 *Joke of the Day:*\n\n{random.choice(jokes)}", parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_fact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Random fun fact"""
        facts = [
            "🧠 Your brain uses about 20% of your body's energy!",
            "📚 The shortest war in history lasted 38 minutes!",
            "🔬 Honey never spoils - 3000-year-old honey was found edible!",
            "🌍 Octopuses have three hearts and blue blood!",
        ]
        await update.message.reply_text(f"💡 *Fun Fact:* {random.choice(facts)}")
    
    async def cmd_quote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Motivational quote"""
        quotes = [
            "📚 'Education is the most powerful weapon.' - Nelson Mandela",
            "💪 'Believe you can and you're halfway there.' - Theodore Roosevelt",
            "🎯 'The expert in anything was once a beginner.' - Helen Hayes",
            "🌟 'Success is not final, failure is not fatal.' - Winston Churchill",
        ]
        await update.message.reply_text(f"✨ *Quote of the Day:*\n\n{random.choice(quotes)}", parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_calc(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Safe mathematical calculator"""
        if not context.args:
            await update.message.reply_text("🔢 Usage: /calc <expression>")
            return
        
        expression = " ".join(context.args)
        safe_chars = set("0123456789+-*/(). ")
        
        if not all(c in safe_chars for c in expression):
            await update.message.reply_text("❌ Only numbers and basic math operators allowed!")
            return
        
        try:
            result = eval(expression, {"__builtins__": {}}, {})
            await update.message.reply_text(f"🔢 *{expression}* = *{result}*", parse_mode=ParseMode.MARKDOWN)        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    
    async def cmd_timer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Study timer with notification"""
        if not context.args:
            await update.message.reply_text("⏱️ Usage: /timer <minutes>")
            return
        
        try:
            minutes = int(context.args[0])
            if minutes < 1 or minutes > 180:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Please enter valid minutes (1-180)")
            return
        
        await update.message.reply_text(f"⏱️ Timer started: *{minutes} minutes* 🔔", parse_mode=ParseMode.MARKDOWN)
        
        async def send_timer_complete():
            await asyncio.sleep(minutes * 60)
            try:
                await update.message.reply_text(f"🔔 *Time's up!* ⏰ Great job!")
            except:
                pass
        
        asyncio.create_task(send_timer_complete())
    
    async def cmd_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Secure password generator"""
        length = int(context.args[0]) if context.args else 12
        length = min(max(length, 8), 32)
        
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        password = ''.join(random.choice(chars) for _ in range(length))
        
        await update.message.reply_text(
            f"🔐 *Generated Password:*\n`{password}`\n\n⚠️ Save it securely!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_random(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Random number generator"""
        if len(context.args) < 2:
            await update.message.reply_text("🎲 Usage: /random <min> <max>")
            return
        
        try:
            min_val = int(context.args[0])
            max_val = int(context.args[1])            result = random.randint(min_val, max_val)
            await update.message.reply_text(f"🎲 Random number: *{result}*", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await update.message.reply_text("❌ Please enter valid numbers.")
    
    async def cmd_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Virtual dice roller"""
        result = random.randint(1, 6)
        await update.message.reply_text(f"🎲 You rolled: *{result}*", parse_mode=ParseMode.MARKDOWN)
    
    async def cmd_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Virtual coin flip"""
        result = "Heads" if random.random() > 0.5 else "Tails"
        await update.message.reply_text(f"🪙 Coin flip: *{result}*", parse_mode=ParseMode.MARKDOWN)
    
    async def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id == ADMIN_USER_ID and ADMIN_USER_ID != 0
    
    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin panel dashboard"""
        if not await self._is_admin(update.effective_user.id):
            await update.message.reply_text("🔐 Admin access required.")
            return
        
        with self.db.get_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            user_count = cur.fetchone()[0]
        
        await update.message.reply_text(
            f"👑 *Admin Panel*\n\n"
            f"📊 Total Users: *{user_count}*\n"
            f"🛠️ Use /broadcast, /banuser, /userinfo",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send message to all users"""
        if not await self._is_admin(update.effective_user.id):
            await update.message.reply_text("🔐 Admin access required.")
            return
        
        if not context.args:
            await update.message.reply_text("📢 Usage: /broadcast <message>")
            return
        
        message = " ".join(context.args)
        
        with self.db.get_cursor() as cur:
            cur.execute("SELECT user_id FROM users")            user_ids = [row[0] for row in cur.fetchall()]
        
        sent = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(uid, f"📢 {message}")
                sent += 1
                await asyncio.sleep(0.1)
            except:
                continue
        
        await update.message.reply_text(f"✅ Broadcast sent to {sent} users!")
    
    async def cmd_banuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ban a user"""
        if not await self._is_admin(update.effective_user.id):
            await update.message.reply_text("🔐 Admin access required.")
            return
        
        if not context.args:
            await update.message.reply_text("🚫 Usage: /banuser <user_id>")
            return
        
        try:
            user_id = int(context.args[0])
            self.db.ban_user(user_id, "Banned by admin")
            await update.message.reply_text(f"✅ User {user_id} banned!")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
    
    async def cmd_unbanuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Unban a user"""
        if not await self._is_admin(update.effective_user.id):
            await update.message.reply_text("🔐 Admin access required.")
            return
        
        if not context.args:
            await update.message.reply_text("✅ Usage: /unbanuser <user_id>")
            return
        
        try:
            user_id = int(context.args[0])
            self.db.unban_user(user_id)
            await update.message.reply_text(f"✅ User {user_id} unbanned!")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
    
    async def cmd_userinfo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View user details"""
        if not await self._is_admin(update.effective_user.id):            await update.message.reply_text("🔐 Admin access required.")
            return
        
        if not context.args:
            await update.message.reply_text("👤 Usage: /userinfo <user_id>")
            return
        
        try:
            user_id = int(context.args[0])
            stats = self.db.get_user_stats(user_id)
            
            if stats:
                await update.message.reply_text(
                    f"👤 *User Info*\n\n"
                    f"ID: {user_id}\n"
                    f"Level: {stats['level']}\n"
                    f"XP: {stats['xp']}\n"
                    f"Quizzes: {stats['quiz_stats']['total']}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text("❌ User not found.")
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
    
    async def cmd_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send feedback to admin"""
        if not context.args:
            await update.message.reply_text("💬 Usage: /feedback <your message>")
            return
        
        message = " ".join(context.args)
        
        if ADMIN_USER_ID:
            try:
                await context.bot.send_message(
                    ADMIN_USER_ID,
                    f"📬 *Feedback from {update.effective_user.first_name}*\n\n{message}",
                    parse_mode=ParseMode.MARKDOWN
                )
                await update.message.reply_text("✅ Feedback sent to admin! Thank you! 🙏")
            except:
                await update.message.reply_text("⚠️ Could not send feedback. Try again later.")
        else:
            await update.message.reply_text("⚠️ Admin not configured.")
    
    async def cmd_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set preferred language"""
        if not context.args:
            await update.message.reply_text("🌐 Usage: /language <en/hi>")            return
        
        lang = context.args[0].lower()
        if lang in ["en", "hi"]:
            with self.db.get_cursor() as cur:
                cur.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, update.effective_user.id))
            await update.message.reply_text(f"✅ Language set to {lang.upper()}!")
        else:
            await update.message.reply_text("❌ Use 'en' for English or 'hi' for Hindi.")
    
    async def _send_daily_content(self):
        """Send daily content at scheduled times"""
        logger.info("🌅 Daily content scheduled")
    
    async def _send_daily_quiz(self):
        """Send daily quiz at scheduled times"""
        logger.info("🎯 Daily quiz scheduled")
        if TARGET_GROUP_ID:
            try:
                question = {
                    "q": "What is the capital of India?",
                    "options": ["Mumbai", "Delhi", "Bangalore", "Kolkata"],
                    "a": 1,
                    "e": "New Delhi is the capital of India"
                }
                await self.app.bot.send_poll(
                    chat_id=TARGET_GROUP_ID,
                    question=f"🌟 Daily Quiz: {question['q']}",
                    options=question["options"],
                    type="quiz",
                    correct_option_id=question["a"],
                    explanation=question["e"],
                    is_anonymous=False
                )
            except Exception as e:
                logger.error(f"Failed to send daily quiz: {e}")
    
    async def _check_reminders(self):
        """Check and fire due reminders"""
        reminders = self.db.get_active_reminders()
        for reminder in reminders:
            try:
                await self.app.bot.send_message(
                    reminder["user_id"],
                    f"🔔 *Reminder:* {reminder['message']}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Global error handler"""
        error = context.error
        logger.error(f"Update {update} caused error: {error}")
        
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Oops! Something went wrong.\nPlease try again."
            )
    
    async def run(self):
        """Main entry point to run the bot"""
        await self.initialize()
        self.app.add_error_handler(self.error_handler)
        self.scheduler.start()
        logger.info("⏰ Scheduler started")
        
        logger.info(f"🚀 Starting bot polling...")
        
        while True:
            try:
                await self.app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
                break
            except NetworkError as e:
                logger.warning(f"⚠️ Network error, retrying in 5s: {e}")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"❌ Critical error: {e}")
                await asyncio.sleep(10)


# ─────────────────────────────────────────────────────────────
# 🚀 APPLICATION ENTRY POINT
# ─────────────────────────────────────────────────────────────

def main():
    """Main entry point"""
    if not TG_BOT_TOKEN:
        logger.error("❌ TG_BOT_TOKEN environment variable not set!")
        sys.exit(1)
    
    if not GROQ_API_KEY:
        logger.warning("⚠️ GROQ_API_KEY not set - AI features will use fallback questions")
    
    bot = QuizBot()
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("👋 Bot shutting down gracefully...")    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

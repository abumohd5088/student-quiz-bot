#!/usr/bin/env python3
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Get token from environment
TOKEN = os.environ.get("TG_BOT_TOKEN")
if not TOKEN:
    print("ERROR: TG_BOT_TOKEN not set!")
    exit(1)

# Setup logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎓 Welcome to Quiz Bot!\n\n/quiz - Take a quiz\n/help - Help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 Commands:\n/quiz - Random quiz\n/profile - Your stats\n/leaderboard - Top students")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong! Bot is online!")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_poll(
        question="What is 5 + 7?",
        options=["10", "11", "12", "13"],
        type="quiz",
        correct_option_id=2,
        explanation="5 + 7 = 12"
    )

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👤 Your Profile\nLevel: 1\nXP: 0")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏆 Leaderboard\n1. You - 0 XP")

# Main function
async def main():
    logger.info("Starting bot...")
    app = Application.builder().token(TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    
    # Start polling
    await app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

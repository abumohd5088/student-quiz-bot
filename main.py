import os
import asyncio
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot running"

@app.route("/ping")
def ping():
    return {"ping": "pong"}

# -------- TELEGRAM COMMANDS --------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Quiz Bot is working!")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong!")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_poll(
        question="What is 7 × 8?",
        options=["54", "56", "63", "48"],
        type="quiz",
        correct_option_id=1,
        explanation="7 × 8 = 56",
        is_anonymous=False
    )

# -------- FLASK THREAD --------

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

# -------- BOT --------

async def main():

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("quiz", quiz))

    print("Bot started")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())

import os
import asyncio
import logging
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

@app.route("/")
def home():
    return {"status": "Bot running"}

@app.route("/ping")
def ping():
    return {"ping": "pong"}

# -------- TELEGRAM COMMANDS --------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎓 Welcome to Student Quiz Bot!\n\n"
        "/quiz - random quiz\n"
        "/ping - check bot"
    )

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong! Bot is working.")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):

    question = "What is 7 × 8?"
    options = ["54", "56", "63", "48"]
    correct = 1

    await update.message.reply_poll(
        question=question,
        options=options,
        type="quiz",
        correct_option_id=correct,
        explanation="7 × 8 = 56",
        is_anonymous=False
    )

# -------- BOT START --------

async def run_bot():

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("quiz", quiz))

    print("Bot started...")
    await application.run_polling()

# -------- MAIN --------

def main():
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot())

    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()

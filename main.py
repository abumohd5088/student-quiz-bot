import os
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TG_BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

app = Flask(__name__)

@app.route("/")
def home():
    return "Telegram Quiz Bot running"

@app.route("/ping")
def ping():
    return {"status": "ok"}

# -------- TELEGRAM COMMANDS --------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot working!")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong!")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_poll(
        question="What is 7 × 8?",
        options=["54","56","63","48"],
        type="quiz",
        correct_option_id=1,
        explanation="7 × 8 = 56",
        is_anonymous=False
    )

# -------- TELEGRAM BOT --------

def run_bot():

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("quiz", quiz))

    logging.info("Telegram bot started")

    application.run_polling()

# -------- MAIN --------

if __name__ == "__main__":

    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()

    app.run(host="0.0.0.0", port=PORT)

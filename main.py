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

# -------- TELEGRAM COMMANDS --------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Quiz bot working ✅")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# -------- BOT START --------

async def run_bot():

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("quiz", quiz))

    print("Telegram bot started...")
    await application.run_polling()

def start_bot():
    asyncio.run(run_bot())

# -------- MAIN --------

if __name__ == "__main__":

    bot_thread = threading.Thread(target=start_bot)
    bot_thread.start()

    app.run(host="0.0.0.0", port=PORT)

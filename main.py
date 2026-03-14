import os
import asyncio
import logging
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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

# ---------- TELEGRAM COMMANDS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot working!")

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

# ---------- RUN TELEGRAM BOT ----------

async def run_bot():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("quiz", quiz))

    logging.info("Bot started")

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

# ---------- RUN FLASK ----------

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

# ---------- MAIN ----------

async def main():

    loop = asyncio.get_event_loop()

    # start flask in background
    loop.run_in_executor(None, run_flask)

    # start telegram bot
    await run_bot()

if __name__ == "__main__":
    asyncio.run(main())

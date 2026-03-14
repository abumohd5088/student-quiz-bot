import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request

TOKEN = os.environ.get("TG_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8080")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎓 Quiz Bot is working! /quiz")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_poll(
        question="What is 2 + 2?",
        options=["3", "4", "5", "6"],
        type="quiz",
        correct_option_id=1
    )

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_flask(request.get_json())
    asyncio.create_task(app.process_update(update))
    return '', 200

@flask_app.route('/setwebhook', methods=['GET'])
def set_webhook():
    url = f"{URL}/webhook"
    app.bot.set_webhook(url)
    return f"Webhook set to {url}"

@flask_app.route('/health', methods=['GET'])
def health():
    return 'OK', 200

if __name__ == '__main__':
    logger.info("Starting bot with webhooks...")
    flask_app.run(host='0.0.0.0', port=PORT)

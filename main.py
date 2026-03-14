import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request, jsonify

TOKEN = os.environ.get("TG_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8080")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize bot application
app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /start from user {update.effective_user.id}")
    try:
        await update.message.reply_text("🎓 Quiz Bot is working! Try /quiz")
        logger.info("Sent response to /start")
    except Exception as e:
        logger.error(f"Error in /start: {e}")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /quiz from user {update.effective_user.id}")
    try:
        await update.message.reply_poll(
            question="What is 2 + 2?",
            options=["3", "4", "5", "6"],
            type="quiz",
            correct_option_id=1,
            explanation="Correct! 2 + 2 = 4"
        )
        logger.info("Sent quiz")
    except Exception as e:
        logger.error(f"Error in /quiz: {e}")
        await update.message.reply_text("Quiz error, please try again")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /help from user {update.effective_user.id}")
    try:
        await update.message.reply_text("📚 Available commands:\n/quiz - Take a quiz\n/start - Welcome message")
    except Exception as e:
        logger.error(f"Error in /help: {e}")

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("quiz", quiz))
app.add_handler(CommandHandler("help", help_cmd))

@flask_app.route('/', methods=['GET'])
def index():
    return "🎓 Bot is running! Visit /setwebhook"

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = Update.de_flask(request.get_json())
        logger.info(f"Received webhook update: {update}")
        asyncio.create_task(app.process_update(update))
        return '', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@flask_app.route('/setwebhook', methods=['GET'])
def set_webhook():
    try:
        url = f"{URL}/webhook"
        result = app.bot.set_webhook(url)
        logger.info(f"Webhook set result: {result}")
        if result:
            return f"✅ Webhook set to: {url}"
        else:
            return f"❌ Failed to set webhook"
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        return f"Error: {e}"

@flask_app.route('/health', methods=['GET'])
def health():
    return 'OK', 200

@flask_app.route('/logs', methods=['GET'])
def get_logs():
    return "Bot is healthy", 200

if __name__ == '__main__':
    logger.info("Starting bot with webhooks...")
    logger.info(f"URL: {URL}")
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)

import os
import logging
import random
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from groq import Groq

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TG_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ---- AI ----
groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ---- Scheduler (fixed) ----
scheduler = BackgroundScheduler()
scheduler.start()

# ---- Memory DB ----
users = {}

def get_user(uid):
    if uid not in users:
        users[uid] = {"xp":0,"quizzes":0}
    return users[uid]

# ---- AI Answer ----
def ai_answer(prompt):
    if not groq:
        return "AI not configured"

    try:
        r = groq.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role":"user","content":prompt}],
        )
        return r.choices[0].message.content
    except:
        return "AI error"

# ---- Commands ----

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    user=get_user(update.effective_user.id)

    msg=f"""
🎓 Welcome {update.effective_user.first_name}

XP: {user['xp']}
Quizzes: {user['quizzes']}

Commands:
/quiz
/ai question
/profile
"""
    await update.message.reply_text(msg)

async def quiz(update:Update,context:ContextTypes.DEFAULT_TYPE):

    questions=[
        ("2+2?",["3","4","5","6"],1),
        ("Capital of France?",["Paris","Rome","Berlin","London"],0)
    ]

    q=random.choice(questions)

    await update.message.reply_poll(
        question=q[0],
        options=q[1],
        type="quiz",
        correct_option_id=q[2],
        is_anonymous=False
    )

    user=get_user(update.effective_user.id)
    user["quizzes"]+=1

async def ai(update:Update,context:ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text("Usage: /ai question")
        return

    question=" ".join(context.args)

    await update.message.reply_text("🤖 Thinking...")

    answer=ai_answer(question)

    await update.message.reply_text(answer)

async def profile(update:Update,context:ContextTypes.DEFAULT_TYPE):

    user=get_user(update.effective_user.id)

    await update.message.reply_text(
        f"XP: {user['xp']}\nQuizzes: {user['quizzes']}"
    )

async def reminder(update:Update,context:ContextTypes.DEFAULT_TYPE):

    minutes=int(context.args[0])
    chat=update.effective_chat.id

    def send():
        context.bot.send_message(chat_id=chat,text="⏰ Reminder!")

    scheduler.add_job(send,"date",run_date=datetime.now()+timedelta(minutes=minutes))

    await update.message.reply_text("Reminder set")

# ---- Bot ----

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start",start))
app.add_handler(CommandHandler("quiz",quiz))
app.add_handler(CommandHandler("ai",ai))
app.add_handler(CommandHandler("profile",profile))
app.add_handler(CommandHandler("reminder",reminder))

print("Bot started")

app.run_polling()

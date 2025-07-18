import os
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from gtts import gTTS
from supabase_client import supabase

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TZ = pytz.timezone(os.getenv("TIMEZONE", "UTC"))

VOICE_DIR = "voice_memos"
os.makedirs(VOICE_DIR, exist_ok=True)

def create_voice(text, filename):
    try:
        tts = gTTS(text=text, lang='en')
        fp = os.path.join(VOICE_DIR, filename)
        tts.save(fp)
        return fp
    except Exception:
        return None

async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data["user_id"]
    task = job_data["task"]
    repeat_type = job_data["repeat_type"]
    r_id = job_data["r_id"]

    voice = create_voice(f"Reminder: {task}", f"{user_id}_{task[:10]}.mp3")
    if voice:
        with open(voice, 'rb') as vf:
            await context.bot.send_voice(chat_id=user_id, voice=vf)
    else:
        await context.bot.send_message(chat_id=user_id, text=f"🔔 Reminder: {task}")

    if repeat_type == "once":
        supabase.table("reminders").update({"status": "done"}).eq("id", r_id).execute()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! Commands:\n"
        "/remind HH:MM Task — one-time\n"
        "/daily HH:MM Task — daily\n"
        "/list — active reminders\n"
        "/cancel <id> — cancel reminder"
    )

async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        t, *task = context.args
        hour, minute = map(int, t.split(":"))
        dt = datetime.now(TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt < datetime.now(TZ):
            dt += timedelta(days=1)
        user_id = update.effective_chat.id
        task_text = " ".join(task)

        r = supabase.table("reminders").insert({
            "user_id": user_id,
            "task": task_text,
            "time": dt.time().strftime("%H:%M:%S"),
            "type": "once",
            "status": "active"
        }).execute()
        r_id = r.data[0]["id"]
        
        job_data = {"user_id": user_id, "task": task_text, "repeat_type": "once", "r_id": r_id}
        context.job_queue.run_once(reminder_job, dt, data=job_data, name=str(r_id))
        
        await update.message.reply_text(f"✅ Set one-time reminder #{r_id} at {t}: {task_text}")
    except Exception:
        await update.message.reply_text("⚠️ Usage: /remind HH:MM Task")

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        t, *task = context.args
        hour, minute = map(int, t.split(":"))
        job_time = datetime.now(TZ).replace(hour=hour, minute=minute, second=0, microsecond=0).time()
        user_id = update.effective_chat.id
        task_text = " ".join(task)
        r = supabase.table("reminders").insert({
            "user_id": user_id,
            "task": task_text,
            "time": job_time.strftime("%H:%M:%S"),
            "type": "daily",
            "status": "active"
        }).execute()
        r_id = r.data[0]["id"]

        job_data = {"user_id": user_id, "task": task_text, "repeat_type": "daily", "r_id": r_id}
        context.job_queue.run_daily(reminder_job, job_time, data=job_data, name=str(r_id), tzinfo=TZ)

        await update.message.reply_text(f"✅ Set daily reminder #{r_id} at {t}: {task_text}")
    except Exception:
        await update.message.reply_text("⚠️ Usage: /daily HH:MM Task")

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    res = supabase.table("reminders").select("*").eq("user_id", user_id).eq("status", "active").execute()
    if not res.data:
        await update.message.reply_text("📭 No active reminders.")
    else:
        lines = [f"{r['id']}: {r['type']} at {r['time']}: {r['task']}" for r in res.data]
        await update.message.reply_text("Active reminders:\n" + "\n".join(lines))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rid = int(context.args[0])
        
        jobs = context.job_queue.get_jobs_by_name(str(rid))
        if jobs:
            for job in jobs:
                job.schedule_removal()
        
        supabase.table("reminders").update({"status": "cancelled"}).eq("id", rid).execute()
        await update.message.reply_text(f"❌ Cancelled reminder #{rid}")
    except Exception:
        await update.message.reply_text("⚠️ Usage: /cancel <id>")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel))

    app.run_polling()

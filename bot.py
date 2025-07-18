import os
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from gtts import gTTS
from supabase_client import supabase

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TZ = pytz.timezone(os.getenv("TIMEZONE", "UTC"))

# States for conversation
TASK, TIME = range(2)

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
        "Hi! I'm your friendly reminder bot.\n\n"
        "Here are the commands you can use:\n"
        "  /remind - Set a one-time reminder.\n"
        "  /daily HH:MM AM/PM Task - Set a daily reminder.\n"
        "  /list - Show your active reminders.\n"
        "  /cancel <id> - Cancel a specific reminder.\n\n"
        "You can use either 12-hour (e.g., 03:00 PM) or 24-hour (e.g., 15:00) time formats."
    )

async def remind_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks for the reminder task."""
    await update.message.reply_text("What do you want to be reminded about?")
    return TASK

async def get_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the task and asks for the time."""
    context.user_data["task"] = update.message.text
    await update.message.reply_text("Great! Now, at what time? (e.g., HH:MM)")
    return TIME

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the time, sets the reminder, and ends the conversation."""
    try:
        t_str = update.message.text
        # Try parsing 12-hour format first, then fall back to 24-hour
        try:
            dt_obj = datetime.strptime(t_str, "%I:%M %p")
        except ValueError:
            dt_obj = datetime.strptime(t_str, "%H:%M")

        now = datetime.now(TZ)
        dt = now.replace(hour=dt_obj.hour, minute=dt_obj.minute, second=0, microsecond=0)
        
        if dt < now:
            dt += timedelta(days=1)

        user_id = update.effective_chat.id
        task_text = context.user_data["task"]

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
        
        # Display time in 12-hour format
        display_time = dt.strftime("%I:%M %p")
        await update.message.reply_text(f"✅ Set one-time reminder #{r_id} at {display_time}: {task_text}")
        context.user_data.clear()
        return ConversationHandler.END
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid time format. Please use HH:MM or HH:MM AM/PM. Let's try again.")
        return TIME

async def remind_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Reminder setup cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        t_str, *task_parts = context.args
        task_text = " ".join(task_parts)

        if not task_text:
            await update.message.reply_text("⚠️ Please provide a task for the reminder.")
            return

        # Try parsing 12-hour format first, then fall back to 24-hour
        try:
            dt_obj = datetime.strptime(t_str, "%I:%M %p")
        except ValueError:
            dt_obj = datetime.strptime(t_str, "%H:%M")
        
        job_time = dt_obj.time()
        user_id = update.effective_chat.id

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

        display_time = dt_obj.strftime("%I:%M %p")
        await update.message.reply_text(f"✅ Set daily reminder #{r_id} at {display_time}: {task_text}")
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Usage: /daily HH:MM AM/PM Task")
    except Exception as e:
        print(f"Error in /daily: {e}")
        await update.message.reply_text("An error occurred while setting the daily reminder.")

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    res = supabase.table("reminders").select("*").eq("user_id", user_id).eq("status", "active").execute()
    if not res.data:
        await update.message.reply_text("📭 No active reminders.")
    else:
        lines = []
        for r in res.data:
            # Parse time and format to 12-hour
            time_obj = datetime.strptime(r['time'], "%H:%M:%S").time()
            display_time = time_obj.strftime("%I:%M %p")
            lines.append(f"#{r['id']}: {r['type']} at {display_time} — {r['task']}")
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

async def reschedule_reminders(app):
    """Load active reminders from DB and reschedule them."""
    reminders = supabase.table("reminders").select("*").eq("status", "active").execute().data
    if not reminders:
        return

    job_queue = app.job_queue
    now = datetime.now(TZ)

    for r in reminders:
        user_id = r["user_id"]
        task = r["task"]
        r_id = r["id"]
        job_data = {"user_id": user_id, "task": task, "repeat_type": r["type"], "r_id": r_id}
        
        try:
            t_obj = datetime.strptime(r["time"], "%H:%M:%S").time()
        except (ValueError, TypeError):
            continue

        if r["type"] == "daily":
            job_queue.run_daily(reminder_job, t_obj, data=job_data, name=str(r_id), tzinfo=TZ)
        elif r["type"] == "once":
            dt = now.replace(hour=t_obj.hour, minute=t_obj.minute, second=t_obj.second, microsecond=0)
            if dt < now:
                # If the time has passed for today, it might be an old reminder.
                # You could add logic here to handle it, e.g., notify user or mark as done.
                # For now, we'll assume it should have already run.
                continue
            job_queue.run_once(reminder_job, dt, data=job_data, name=str(r_id))

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(reschedule_reminders).build()

    remind_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("remind", remind_start)],
        states={
            TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        },
        fallbacks=[CommandHandler("cancel", remind_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(remind_conv_handler)
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel))

    app.run_polling()

from flask import Flask
import threading

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Bot is running!"

# Import required libraries
import re
import os
import pandas as pd
from datetime import datetime

# Telegram bot libraries
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters
)

# Scheduler for monthly report
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Get values from Render environment variables
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

print("TOKEN:", TOKEN)
print("CHAT_ID:", CHAT_ID)

# File to store expenses
DATA_FILE = "expenses.csv"

# Create CSV file if it doesn't exist
if not os.path.exists(DATA_FILE):
    df = pd.DataFrame(columns=["date", "description", "amount", "category"])
    df.to_csv(DATA_FILE, index=False)

def detect_category(text):
    text = text.lower()

    if any(word in text for word in ["coffee", "lunch", "dinner", "food"]):
        return "food"
    elif any(word in text for word in ["grab", "taxi", "bus", "train"]):
        return "transport"
    elif any(word in text for word in ["rent", "bill", "utilities"]):
        return "bills"
    elif any(word in text for word in ["shopping", "clothes"]):
        return "shopping"
    
    return "others"

def parse_input(text):
    # Find number (amount) in message
    amount_match = re.search(r'(\d+\.?\d*)', text)

    # If no number found → invalid input
    if not amount_match:
        return None

    # Extract amount
    amount = float(amount_match.group(1))

    # Remove amount from text → remaining = description
    description = text.replace(amount_match.group(1), "").strip()

    # Auto detect category
    category = detect_category(text)

    return description, amount, category

def save_expense(entry):
    df = pd.DataFrame([entry])

    # Append to CSV file
    df.to_csv(DATA_FILE, mode='a', header=False, index=False)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # Parse user input
    parsed = parse_input(text)

    # If format is wrong
    if not parsed:
        await update.message.reply_text("❌ Use format: lunch 12 or coffee 5")
        return

    description, amount, category = parsed

    # Create entry
    entry = {
        "date": datetime.now(),
        "description": description,
        "amount": amount,
        "category": category
    }

    # Save to CSV
    save_expense(entry)

    # Reply to user
    await update.message.reply_text(
        f"✅ Saved: {description} - ${amount:.2f} ({category})"
    )

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    df = pd.read_csv(DATA_FILE)
    df['date'] = pd.to_datetime(df['date'])

    today = datetime.now().date()

    # Filter today's expenses
    today_df = df[df['date'].dt.date == today]

    if today_df.empty:
        await update.message.reply_text("No expenses today.")
        return

    total = today_df['amount'].sum()

    # Group by category
    breakdown = today_df.groupby("category")["amount"].sum()

    msg = f"📊 Today Total: ${total:.2f}\n"

    for cat, amt in breakdown.items():
        msg += f"{cat}: ${amt:.2f}\n"

    await update.message.reply_text(msg)

def generate_report():
    df = pd.read_csv(DATA_FILE)
    df['date'] = pd.to_datetime(df['date'])

    now = datetime.now()

    # Filter current month
    monthly = df[
        (df['date'].dt.month == now.month) &
        (df['date'].dt.year == now.year)
    ]

    if monthly.empty:
        return None

    # Group by category
    summary = monthly.groupby("category")["amount"].sum()

    file_name = f"expense_report_{now.month}_{now.year}.xlsx"

    # Create Excel file
    with pd.ExcelWriter(file_name) as writer:
        monthly.to_excel(writer, sheet_name="All Expenses", index=False)
        summary.to_excel(writer, sheet_name="Summary")

    return file_name


# -------- SEND REPORT COMMAND -------- #
async def send_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = generate_report()

    if not file:
        await update.message.reply_text("No data this month.")
        return

    with open(file, "rb") as f:
        await update.message.reply_document(f)

# -------- AUTO MONTHLY -------- #
async def auto_send(context: ContextTypes.DEFAULT_TYPE):
    file = generate_report()

    if not file:
        return

    with open(file, "rb") as f:
        await context.bot.send_document(chat_id=CHAT_ID, document=f)

# -------- MAIN -------- #
def run_bot():
    print("Starting bot...")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("month", send_report))

    print("🤖 Bot running...")
    app.run_polling()


if __name__ == "__main__":
    # Run bot in separate thread
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()

    # Run Flask (this keeps Render alive)
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

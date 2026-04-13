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
import sqlite3
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
DB_FILE = "expenses.db"

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    user_id INTEGER,
    description TEXT,
    amount REAL,
    category TEXT
)
""")

conn.commit()

# Create CSV file if it doesn't exist


def detect_category(text):
    text = text.lower()

    if any(word in text for word in ["coffee","breakfast", "lunch", "dinner", "food"]):
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
    cursor.execute("""
        INSERT INTO expenses (date, user_id, description, amount, category)
        VALUES (?, ?, ?, ?, ?)
    """, (
        entry["date"],
        entry["user_id"],
        entry["description"],
        entry["amount"],
        entry["category"]
    ))
    conn.commit()

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
        "user_id": update.message.chat_id,
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
    query = "SELECT * FROM expenses WHERE user_id = ?"
    df = pd.read_sql_query(query, conn, params=(update.message.chat_id,))
    df['date'] = pd.to_datetime(df['date'])

    today = datetime.now().date()

    # Filter today's expenses
    user_id = update.message.chat_id

    today_df = df[
        (df['date'].dt.date == today) &
        (df['user_id'] == user_id)
]

    if today_df.empty:
        await update.message.reply_text("No expenses today.")
        return

    total = today_df['amount'].sum()

    msg = f"📊 Today Total: ${total:.2f}\n\n"

    # Group by category
    grouped = today_df.groupby("category")

    for category, group in grouped:
        cat_total = group['amount'].sum()
        msg += f"{category.capitalize()} ${cat_total:.2f}\n"

        for _, row in group.iterrows():
            msg += f"• {row['description']}: ${row['amount']:.2f}\n"

        msg += "\n"

    await update.message.reply_text(msg)

async def monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = "SELECT * FROM expenses WHERE user_id = ?"
    df = pd.read_sql_query(query, conn, params=(update.message.chat_id,))
    df['date'] = pd.to_datetime(df['date'])

    user_id = update.message.chat_id

    df = df[df['user_id'] == user_id]

    if df.empty:
        await update.message.reply_text("No expenses this month.")
        return

    now = datetime.now()

    monthly_df = df[
        (df['date'].dt.month == now.month) &
        (df['date'].dt.year == now.year)
    ]

    if monthly_df.empty:
        await update.message.reply_text("No expenses this month.")
        return

    total = monthly_df['amount'].sum()

    breakdown = monthly_df.groupby("category")["amount"].sum().sort_values(ascending=False)

    msg = f"📅 Monthly Summary\n\nTotal: ${total:.2f}\n\n"

    for cat, amt in breakdown.items():
        msg += f"{cat.capitalize()}: ${amt:.2f}\n"

    await update.message.reply_text(msg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "👋 *Welcome to Expense Tracker Bot!*\n\n"
        "💸 *Log your expenses easily:*\n"
        "Just type:\n"
        "• coffee 5\n"
        "• lunch $12\n"
        "• grab 18\n\n"
        "📊 *View your spending:*\n"
        "/summary — Today’s breakdown\n"
        "/monthly — Monthly spending summary\n"
        "/month — Monthly Excel report\n"
        "🛠 *Manage entries:*\n"
        "/undo — Remove last entry\n"
        "/delete <name> — Remove specific entry\n"
        "Example: /delete coffee\n\n"
        "⚡ Tip: Keep it simple — description + amount\n\n"
        "Start tracking now 🚀"
    )

    await update.message.reply_text(message, parse_mode="Markdown")

def generate_report(user_id):
    query = "SELECT * FROM expenses WHERE user_id = ?"
    df = pd.read_sql_query(query, conn, params=(user_id,))
    df['date'] = pd.to_datetime(df['date'])

    if df.empty:
        return None


    # ✅ Get current month
    now = datetime.now()

    monthly = df[
        (df['date'].dt.month == now.month) &
        (df['date'].dt.year == now.year)
    ]

    if monthly.empty:
        return None

    # ✅ Group by category
    summary = monthly.groupby("category")["amount"].sum()

    file_name = f"expense_report_{now.month}_{now.year}.xlsx"

    # ✅ Create Excel
    with pd.ExcelWriter(file_name) as writer:
        monthly.to_excel(writer, sheet_name="All Expenses", index=False)
        summary.to_excel(writer, sheet_name="Summary")

    return file_name

# -------- UNDO LAST ENTRY -------- #
async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id

    query = "SELECT * FROM expenses WHERE user_id = ? ORDER BY id DESC"
    df = pd.read_sql_query(query, conn, params=(user_id,))

    if df.empty:
        await update.message.reply_text("No entries to remove.")
        return

    removed = df.iloc[0]

    cursor.execute("DELETE FROM expenses WHERE id = ?", (removed["id"],))
    conn.commit()

    await update.message.reply_text(
        f"🗑 Removed: {removed['description']} - ${removed['amount']:.2f}"
    )

# -------- DELETE SPECIFIC ENTRY -------- #
async def delete_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: /delete <description>")
        return

    keyword = " ".join(context.args).lower()
    user_id = update.message.chat_id

    query = "SELECT * FROM expenses WHERE user_id = ?"
    df = pd.read_sql_query(query, conn, params=(user_id,))

    matches = df[df['description'].str.lower().str.contains(keyword)]

    if matches.empty:
        await update.message.reply_text("❌ No matching entry found.")
        return

    removed = matches.iloc[0]

    cursor.execute("DELETE FROM expenses WHERE id = ?", (removed["id"],))
    conn.commit()

    await update.message.reply_text(
        f"🗑 Deleted: {removed['description']} - ${removed['amount']:.2f}"
    )

    
# -------- SEND REPORT COMMAND -------- #
async def send_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id  # 👈 GET USER

    file = generate_report(user_id)   # 👈 PASS USER

    if not file:
        await update.message.reply_text("No data this month.")
        return

    with open(file, "rb") as f:
        await update.message.reply_document(f)

# -------- AUTO MONTHLY -------- #
async def auto_send(context: ContextTypes.DEFAULT_TYPE):
    return

# -------- MAIN -------- #
import threading
import asyncio

# Run Flask in background
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # Start Flask in background thread
    web_thread = threading.Thread(target=run_web)
    web_thread.start()

    print("Starting bot...")

    # ✅ Create Telegram app
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))  # 👈 ADD THIS FIRST

    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("month", send_report))
    app.add_handler(CommandHandler("monthly", monthly))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("delete", delete_entry))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot running...")

    # ✅ FIX: Create event loop (Python 3.14 requirement)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # ✅ Start bot
    app.run_polling()

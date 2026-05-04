import os
import sqlite3
import calendar
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

TOKEN = os.getenv("BOT_TOKEN", "8615039614:AAHE9gpAoX5uOgPCbfob9pepmKsw1rQjIIo")
DB_PATH = "journal.db"

# Conversation states
(PAIR, DIRECTION, ENTRY, EXIT, LOT, RESULT, COMMENT) = range(7)

# ─── DATABASE ───────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            pair TEXT,
            direction TEXT,
            entry REAL,
            exit REAL,
            lot REAL,
            result REAL,
            comment TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_trade(user_id, pair, direction, entry, exit_price, lot, result, comment):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (user_id, date, pair, direction, entry, exit, lot, result, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, date.today().isoformat(), pair, direction, entry, exit_price, lot, result, comment))
    conn.commit()
    conn.close()

def get_trades(user_id, filter_date=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if filter_date:
        c.execute("SELECT * FROM trades WHERE user_id=? AND date=? ORDER BY id DESC", (user_id, filter_date))
    else:
        c.execute("SELECT * FROM trades WHERE user_id=? ORDER BY id DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_stats(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT result, pair, direction FROM trades WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_trade_dates(user_id, year, month):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    prefix = f"{year}-{month:02d}"
    c.execute("SELECT DISTINCT date FROM trades WHERE user_id=? AND date LIKE ?", (user_id, f"{prefix}%"))
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

def delete_trade(trade_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM trades WHERE id=? AND user_id=?", (trade_id, user_id))
    conn.commit()
    conn.close()

# ─── MAIN MENU ───────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить сделку", callback_data="add")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
         InlineKeyboardButton("📅 Календарь", callback_data="calendar")],
        [InlineKeyboardButton("📋 История сделок", callback_data="history")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Торговый журнал*\n\nВыбери действие:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📊 *Главное меню*\n\nВыбери действие:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ─── ADD TRADE ───────────────────────────────────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD", "USD/CHF", "AUD/USD", "Другая"]
    keyboard = [[InlineKeyboardButton(p, callback_data=f"pair_{p}")] for p in pairs]
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="menu")])
    await query.edit_message_text(
        "📈 *Новая сделка*\n\nВыбери валютную пару:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PAIR

async def pair_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pair = query.data.replace("pair_", "")
    if pair == "Другая":
        context.user_data["awaiting"] = "pair"
        await query.edit_message_text("✏️ Введи название пары (например: NAS100, BTC/USD):")
        return PAIR
    context.user_data["pair"] = pair
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Long (Buy)", callback_data="dir_Long"),
         InlineKeyboardButton("📉 Short (Sell)", callback_data="dir_Short")],
        [InlineKeyboardButton("◀️ Назад", callback_data="add")]
    ])
    await query.edit_message_text(
        f"Пара: *{pair}*\n\nНаправление сделки:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return DIRECTION

async def pair_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting") == "pair":
        context.user_data["pair"] = update.message.text.upper()
        context.user_data.pop("awaiting")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Long (Buy)", callback_data="dir_Long"),
             InlineKeyboardButton("📉 Short (Sell)", callback_data="dir_Short")]
        ])
        await update.message.reply_text(
            f"Пара: *{context.user_data['pair']}*\n\nНаправление сделки:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return DIRECTION

async def direction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["direction"] = query.data.replace("dir_", "")
    await query.edit_message_text(
        f"Пара: *{context.user_data['pair']}* | {context.user_data['direction']}\n\n✏️ Введи *цену входа*:",
        parse_mode="Markdown"
    )
    return ENTRY

async def entry_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["entry"] = float(update.message.text.replace(",", "."))
    except:
        await update.message.reply_text("❌ Введи число, например: 1.0845")
        return ENTRY
    await update.message.reply_text("✏️ Введи *цену выхода*:", parse_mode="Markdown")
    return EXIT

async def exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["exit"] = float(update.message.text.replace(",", "."))
    except:
        await update.message.reply_text("❌ Введи число, например: 1.0920")
        return EXIT
    await update.message.reply_text("✏️ Введи *размер лота* (например: 0.1):", parse_mode="Markdown")
    return LOT

async def lot_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["lot"] = float(update.message.text.replace(",", "."))
    except:
        await update.message.reply_text("❌ Введи число, например: 0.1")
        return LOT
    await update.message.reply_text(
        "✏️ Введи *результат сделки* в $:\n(+50 если профит, -30 если убыток)",
        parse_mode="Markdown"
    )
    return RESULT

async def result_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.replace(",", ".").replace(" ", "")
        context.user_data["result"] = float(text)
    except:
        await update.message.reply_text("❌ Введи число со знаком, например: +50 или -30")
        return RESULT
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустить", callback_data="skip_comment")]
    ])
    await update.message.reply_text(
        "✏️ Добавь *комментарий* к сделке (или пропусти):",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return COMMENT

async def comment_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["comment"] = update.message.text
    return await save_trade(update, context)

async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["comment"] = ""
    return await save_trade_query(query, context)

async def save_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    uid = update.effective_user.id
    add_trade(uid, d["pair"], d["direction"], d["entry"], d["exit"], d["lot"], d["result"], d.get("comment", ""))
    result_emoji = "✅" if d["result"] > 0 else "❌"
    msg = (
        f"{result_emoji} *Сделка сохранена!*\n\n"
        f"📌 Пара: {d['pair']} | {d['direction']}\n"
        f"🔵 Вход: {d['entry']} → 🔴 Выход: {d['exit']}\n"
        f"📦 Лот: {d['lot']}\n"
        f"💰 Результат: {'+'if d['result']>0 else ''}{d['result']}$"
    )
    if d.get("comment"):
        msg += f"\n💬 {d['comment']}"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

async def save_trade_query(query, context):
    d = context.user_data
    uid = query.from_user.id
    add_trade(uid, d["pair"], d["direction"], d["entry"], d["exit"], d["lot"], d["result"], d.get("comment", ""))
    result_emoji = "✅" if d["result"] > 0 else "❌"
    msg = (
        f"{result_emoji} *Сделка сохранена!*\n\n"
        f"📌 Пара: {d['pair']} | {d['direction']}\n"
        f"🔵 Вход: {d['entry']} → 🔴 Выход: {d['exit']}\n"
        f"📦 Лот: {d['lot']}\n"
        f"💰 Результат: {'+'if d['result']>0 else ''}{d['result']}$"
    )
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# ─── STATISTICS ──────────────────────────────────────────────────────────────

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    rows = get_stats(uid)

    if not rows:
        await query.edit_message_text(
            "📊 *Статистика*\n\nУ тебя пока нет сделок.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu")]])
        )
        return

    results = [r[0] for r in rows]
    total = len(results)
    wins = sum(1 for r in results if r > 0)
    losses = sum(1 for r in results if r < 0)
    winrate = (wins / total * 100) if total > 0 else 0
    total_pnl = sum(results)
    avg_win = (sum(r for r in results if r > 0) / wins) if wins > 0 else 0
    avg_loss = (sum(r for r in results if r < 0) / losses) if losses > 0 else 0
    best = max(results)
    worst = min(results)

    pair_results = {}
    for r, pair, _ in rows:
        if pair not in pair_results:
            pair_results[pair] = []
        pair_results[pair].append(r)
    best_pair = max(pair_results, key=lambda p: sum(pair_results[p])) if pair_results else "—"
    worst_pair = min(pair_results, key=lambda p: sum(pair_results[p])) if pair_results else "—"

    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    msg = (
        f"📊 *Статистика*\n\n"
        f"🔢 Всего сделок: {total}\n"
        f"✅ Профит: {wins} | ❌ Убыток: {losses}\n"
        f"🎯 Винрейт: {winrate:.1f}%\n\n"
        f"{pnl_emoji} Общий P&L: {'+'if total_pnl>=0 else ''}{total_pnl:.2f}$\n"
        f"📈 Средний профит: +{avg_win:.2f}$\n"
        f"📉 Средний убыток: {avg_loss:.2f}$\n\n"
        f"🏆 Лучшая сделка: +{best:.2f}$\n"
        f"💀 Худшая сделка: {worst:.2f}$\n\n"
        f"🌟 Лучшая пара: {best_pair}\n"
        f"⚠️ Худшая пара: {worst_pair}"
    )
    await query.edit_message_text(
        msg, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu")]])
    )

# ─── CALENDAR ────────────────────────────────────────────────────────────────

async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    now = datetime.now()
    context.user_data["cal_year"] = now.year
    context.user_data["cal_month"] = now.month
    await render_calendar(query, context)

async def render_calendar(query, context):
    uid = query.from_user.id
    year = context.user_data.get("cal_year", datetime.now().year)
    month = context.user_data.get("cal_month", datetime.now().month)

    trade_dates = get_trade_dates(uid, year, month)
    trade_days = set(int(d.split("-")[2]) for d in trade_dates)

    month_name = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"][month]

    keyboard = []
    keyboard.append([InlineKeyboardButton(f"📅 {month_name} {year}", callback_data="noop")])
    keyboard.append([InlineKeyboardButton(d, callback_data="noop") for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]])

    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="noop"))
            elif day in trade_days:
                row.append(InlineKeyboardButton(f"🟢{day}", callback_data=f"day_{year}-{month:02d}-{day:02d}"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"day_{year}-{month:02d}-{day:02d}"))
        keyboard.append(row)

    nav = []
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    nav.append(InlineKeyboardButton("◀️", callback_data=f"cal_{prev_year}_{prev_month}"))
    nav.append(InlineKeyboardButton("▶️", callback_data=f"cal_{next_year}_{next_month}"))
    keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="menu")])

    await query.edit_message_text(
        f"📅 *Календарь сделок*\n🟢 — дни со сделками\n\nВыбери день для просмотра:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month = query.data.split("_")
    context.user_data["cal_year"] = int(year)
    context.user_data["cal_month"] = int(month)
    await render_calendar(query, context)

async def show_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day_str = query.data.replace("day_", "")
    uid = query.from_user.id
    trades = get_trades(uid, filter_date=day_str)

    day_fmt = datetime.strptime(day_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    if not trades:
        msg = f"📅 *{day_fmt}*\n\nСделок в этот день нет."
    else:
        msg = f"📅 *{day_fmt}* — {len(trades)} сделок\n\n"
        day_pnl = sum(t[8] for t in trades)
        for t in trades:
            emoji = "✅" if t[8] > 0 else "❌"
            msg += f"{emoji} {t[3]} | {t[4]} | {'+'if t[8]>=0 else ''}{t[8]}$\n"
            msg += f"   Вход: {t[5]} → Выход: {t[6]}, Лот: {t[7]}\n"
            if t[9]:
                msg += f"   💬 {t[9]}\n"
            msg += "\n"
        pnl_emoji = "📈" if day_pnl >= 0 else "📉"
        msg += f"{pnl_emoji} Итого за день: {'+'if day_pnl>=0 else ''}{day_pnl:.2f}$"

    await query.edit_message_text(
        msg, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ К календарю", callback_data="calendar")]
        ])
    )

# ─── HISTORY ─────────────────────────────────────────────────────────────────

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    trades = get_trades(uid)[:20]

    if not trades:
        await query.edit_message_text(
            "📋 *История сделок*\n\nУ тебя пока нет сделок.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu")]])
        )
        return

    msg = "📋 *Последние 20 сделок*\n\n"
    for t in trades:
        emoji = "✅" if t[8] > 0 else "❌"
        msg += f"{emoji} `#{t[0]}` {t[2]} | {t[3]} {t[4]} | {'+'if t[8]>=0 else ''}{t[8]}$\n"

    msg += "\nДля удаления напиши /delete_ID (например /delete_5)"
    await query.edit_message_text(
        msg, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu")]])
    )

async def delete_trade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        trade_id = int(update.message.text.split("_")[1])
        delete_trade(trade_id, uid)
        await update.message.reply_text(f"✅ Сделка #{trade_id} удалена.", reply_markup=main_menu_keyboard())
    except:
        await update.message.reply_text("❌ Неверный формат. Используй /delete_5")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern="^add$")],
        states={
            PAIR: [
                CallbackQueryHandler(pair_selected, pattern="^pair_"),
                CallbackQueryHandler(menu, pattern="^menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, pair_manual)
            ],
            DIRECTION: [CallbackQueryHandler(direction_selected, pattern="^dir_")],
            ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, entry_price)],
            EXIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, exit_price)],
            LOT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lot_size)],
            RESULT: [MessageHandler(filters.TEXT & ~filters.COMMAND, result_input)],
            COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, comment_input),
                CallbackQueryHandler(skip_comment, pattern="^skip_comment$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(show_stats, pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(show_calendar, pattern="^calendar$"))
    app.add_handler(CallbackQueryHandler(calendar_nav, pattern="^cal_"))
    app.add_handler(CallbackQueryHandler(show_day, pattern="^day_"))
    app.add_handler(CallbackQueryHandler(show_history, pattern="^history$"))
    app.add_handler(MessageHandler(filters.Regex(r"^/delete_\d+$"), delete_trade_cmd))

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()

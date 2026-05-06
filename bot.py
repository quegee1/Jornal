import os
import calendar
import httpx
import base64
import json
import re
import psycopg2
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

TOKEN = os.getenv("BOT_TOKEN", "8615039614:AAHE9gpAoX5uOgPCbfob9pepmKsw1rQjIIo")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

(PAIR, DIRECTION, ENTRY, EXIT, STOP, LOT, RESULT, SESSION, TRADE_DATE, COMMENT, CHART_PHOTO) = range(11)
SESSIONS = ["🌏 Азия", "🇬🇧 Лондон", "🇺🇸 Нью-Йорк", "🌐 Другая"]

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id SERIAL PRIMARY KEY, user_id BIGINT, date TEXT, pair TEXT, direction TEXT,
        entry REAL, exit_price REAL, stop_loss REAL, lot REAL, result REAL,
        rr REAL, session TEXT, comment TEXT, chart_file_id TEXT)""")
    for col in ["stop_loss REAL","rr REAL","session TEXT","chart_file_id TEXT"]:
        try:
            c.execute(f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {col}")
            conn.commit()
        except: conn.rollback()
    conn.commit(); conn.close()

def calc_rr(entry, exit_price, stop_loss, direction):
    try:
        risk = abs(entry - stop_loss)
        reward = abs(exit_price - entry) if direction == "Long" else abs(entry - exit_price)
        return round(reward / risk, 2) if risk > 0 else None
    except: return None

def save_trade_db(uid, trade_date, pair, direction, entry, exit_p, sl, lot, result, rr, session, comment, chart=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO trades (user_id,date,pair,direction,entry,exit_price,stop_loss,lot,result,rr,session,comment,chart_file_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (uid,trade_date,pair,direction,entry,exit_p,sl,lot,result,rr,session,comment,chart))
    conn.commit(); conn.close()

def get_trades(uid, filter_date=None, limit=None):
    conn = get_conn(); c = conn.cursor()
    if filter_date:
        c.execute("SELECT id,user_id,date,pair,direction,entry,exit_price,stop_loss,lot,result,rr,session,comment,chart_file_id FROM trades WHERE user_id=%s AND date=%s ORDER BY id DESC",(uid,filter_date))
    else:
        q = "SELECT id,user_id,date,pair,direction,entry,exit_price,stop_loss,lot,result,rr,session,comment,chart_file_id FROM trades WHERE user_id=%s ORDER BY id DESC"
        if limit: q += f" LIMIT {limit}"
        c.execute(q,(uid,))
    rows = c.fetchall(); conn.close(); return rows

def get_all_trades(uid):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT result,pair,direction,session,rr,date FROM trades WHERE user_id=%s ORDER BY date DESC",(uid,))
    rows = c.fetchall(); conn.close(); return rows

def get_trade_dates(uid, year, month):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT DISTINCT date, SUM(result) FROM trades WHERE user_id=%s AND date LIKE %s GROUP BY date",(uid,f"{year}-{month:02d}%"))
    rows = {r[0]:r[1] for r in c.fetchall()}; conn.close(); return rows

def delete_trade_db(tid, uid):
    conn = get_conn(); c = conn.cursor()
    c.execute("DELETE FROM trades WHERE id=%s AND user_id=%s",(tid,uid))
    conn.commit(); conn.close()

async def analyze_mt5(image_bytes):
    if not GEMINI_API_KEY: return None
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = """MetaTrader 5 screenshot. Return ONLY JSON:
{"pair":"GBPUSD","direction":"Long","lot":1.4,"entry":1.35005,"exit":1.35178929,"result":243.50,"date":"2026-04-30"}
buy/long=Long sell/short=Short. result=positive float. date YYYY-MM-DD. ONLY JSON."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}",
                headers={"content-type":"application/json"},
                json={"contents":[{"parts":[{"inline_data":{"mime_type":"image/png","data":b64}},{"text":prompt}]}],"generationConfig":{"temperature":0,"maxOutputTokens":200}}
            )
        data = resp.json()
        print(f"Gemini: {data}")
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = re.sub(r"```json|```","",text).strip()
        m = re.search(r"\{.*?\}",text,re.DOTALL)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        print(f"Gemini error: {e}"); return None

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Скрин MT5",callback_data="screenshot"),InlineKeyboardButton("➕ Вручную",callback_data="add")],
        [InlineKeyboardButton("📊 Статистика",callback_data="stats"),InlineKeyboardButton("📅 Календарь",callback_data="calendar")],
        [InlineKeyboardButton("📋 История",callback_data="history")],
    ])

def sess_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(SESSIONS[0],callback_data="sess_0"),InlineKeyboardButton(SESSIONS[1],callback_data="sess_1")],
        [InlineKeyboardButton(SESSIONS[2],callback_data="sess_2"),InlineKeyboardButton(SESSIONS[3],callback_data="sess_3")],
        [InlineKeyboardButton("⏭ Пропустить",callback_data="sess_skip")],
    ])

async def start(update,context):
    await update.message.reply_text("👋 *Торговый журнал v2*\n\n📸 Скинь скрин MT5 — всё заполнится само\n✏️ Или добавь вручную\n\n🟢/🔴 Календарь | 📐 RR | 🕐 Сессии | 🖼 Графики",parse_mode="Markdown",reply_markup=main_kb())

async def menu_cb(update,context):
    q=update.callback_query; await q.answer()
    await q.edit_message_text("📊 *Меню*",parse_mode="Markdown",reply_markup=main_kb())

async def screenshot_prompt(update,context):
    q=update.callback_query; await q.answer()
    await q.edit_message_text("📸 Отправь скрин закрытой сделки из MT5:",parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад",callback_data="menu")]]))

async def handle_photo(update,context):
    if context.user_data.get("awaiting_chart"):
        context.user_data["chart_file_id"]=update.message.photo[-1].file_id
        context.user_data.pop("awaiting_chart")
        await _finalize(update,context,is_msg=True)
        return
    if not GEMINI_API_KEY:
        await update.message.reply_text("⚠️ GEMINI_API_KEY не настроен.",reply_markup=main_kb()); return
    msg=await update.message.reply_text("🔍 Анализирую скрин MT5...")
    file=await context.bot.get_file(update.message.photo[-1].file_id)
    ib=await file.download_as_bytearray()
    t=await analyze_mt5(bytes(ib))
    if not t or not t.get("pair"):
        await msg.edit_text("❌ Не распознал. Попробуй другой скрин или добавь вручную.",reply_markup=main_kb()); return
    context.user_data["mt5"]=t
    d=t.get("direction","Long"); r=t.get("result",0)
    await msg.edit_text(
        f"🔍 *Распознано:*\n\n📌 *{t.get('pair')}* | {'📈' if d=='Long' else '📉'} {d}\n"
        f"📦 Лот: {t.get('lot')} | 💰 {'+'if r>0 else ''}{r}$\n"
        f"🔵 {t.get('entry')} → 🔴 {t.get('exit')}\n📅 {t.get('date')}\n\nВсё верно?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Далее",callback_data="mt5_next"),InlineKeyboardButton("✏️ Изменить",callback_data="add")],
            [InlineKeyboardButton("❌ Отмена",callback_data="menu")]]))

async def mt5_next(update,context):
    q=update.callback_query; await q.answer()
    await q.edit_message_text("🎯 Стоп-лосс для расчёта RR:\n_(необязательно)_",parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="mt5_skip_sl")]]))
    context.user_data["mt5_step"]="sl"

async def mt5_skip_sl(update,context):
    q=update.callback_query; await q.answer()
    context.user_data.pop("mt5_step",None); context.user_data["sl"]=None
    context.user_data["flow"]="mt5"
    await q.edit_message_text("🕐 Торговая сессия:",parse_mode="Markdown",reply_markup=sess_kb())

async def mt5_text(update,context):
    if context.user_data.get("mt5_step")=="sl":
        try: context.user_data["sl"]=float(update.message.text.replace(",","."))
        except: await update.message.reply_text("❌ Введи число"); return
        context.user_data.pop("mt5_step"); context.user_data["flow"]="mt5"
        await update.message.reply_text("🕐 Сессия:",parse_mode="Markdown",reply_markup=sess_kb())

async def sess_cb(update,context):
    q=update.callback_query; await q.answer()
    context.user_data["session"]=None if q.data=="sess_skip" else SESSIONS[int(q.data.replace("sess_",""))]
    await q.edit_message_text("📸 Скрин графика (необязательно):",parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_chart")]]))
    context.user_data["awaiting_chart"]=True

async def skip_chart(update,context):
    q=update.callback_query; await q.answer()
    context.user_data.pop("awaiting_chart",None); context.user_data["chart_file_id"]=None
    await _finalize(q,context,is_msg=False)

async def _finalize(src,context,is_msg=True):
    d=context.user_data
    if hasattr(src, 'message'):
        uid=src.from_user.id  # callback query
    elif hasattr(src, 'from_user') and src.from_user:
        uid=src.from_user.id  # message
    else:
        uid=src.effective_user.id
    flow=d.get("flow","mt5")
    if flow=="mt5":
        t=d.get("mt5",{})
        pair=t.get("pair",""); direction=t.get("direction","Long")
        entry=t.get("entry",0); exit_p=t.get("exit",0)
        lot=t.get("lot",0); result=t.get("result",0)
        td=t.get("date") or date.today().isoformat()
        comment="📸 MT5"
    else:
        pair=d.get("pair",""); direction=d.get("direction","Long")
        entry=d.get("entry",0); exit_p=d.get("exit",0)
        lot=d.get("lot",0); result=d.get("result",0)
        td=d.get("trade_date") or date.today().isoformat()
        comment=d.get("comment","")
    sl=d.get("sl"); session=d.get("session"); chart=d.get("chart_file_id")
    rr=calc_rr(entry,exit_p,sl,direction) if sl else None
    save_trade_db(uid,td,pair,direction,entry,exit_p,sl,lot,result,rr,session,comment,chart)
    re_e="✅" if result>0 else "❌"
    msg=(f"{re_e} *Сохранено!*\n\n📌 {pair} | {direction}\n"
         f"💰 {'+'if result>0 else ''}{result}$"
         +(f"\n📐 RR: {rr}" if rr else "")
         +(f"\n🕐 {session}" if session else "")
         +(f"\n🖼 График ✓" if chart else "")
         +f"\n📅 {td}")
    if is_msg:
        await src.message.reply_text(msg,parse_mode="Markdown",reply_markup=main_kb())
    else:
        await src.edit_message_text(msg,parse_mode="Markdown",reply_markup=main_kb())
    for k in ["mt5","sl","session","chart_file_id","flow","trade_date","pair","direction","entry","exit","lot","result","comment"]:
        context.user_data.pop(k,None)

# ─── MANUAL CONV ─────────────────────────────────────────────────────────────

async def add_start(update,context):
    q=update.callback_query; await q.answer(); context.user_data.clear()
    pairs=["EUR/USD","GBP/USD","USD/JPY","XAU/USD","NAS100","GBP/JPY","Другая"]
    kb=[[InlineKeyboardButton(p,callback_data=f"pair_{p}")] for p in pairs]
    kb.append([InlineKeyboardButton("◀️ Назад",callback_data="menu")])
    await q.edit_message_text("📈 *Новая сделка*\n\nПара:",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))
    return PAIR

async def pair_sel(update,context):
    q=update.callback_query; await q.answer()
    p=q.data.replace("pair_","")
    if p=="Другая":
        context.user_data["aw"]="pair"
        await q.edit_message_text("✏️ Введи пару (например EURCAD):"); return PAIR
    context.user_data["pair"]=p
    await q.edit_message_text(f"*{p}*\n\nНаправление:",parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📈 Long",callback_data="dir_Long"),InlineKeyboardButton("📉 Short",callback_data="dir_Short")]]))
    return DIRECTION

async def pair_txt(update,context):
    if context.user_data.get("aw")=="pair":
        context.user_data["pair"]=update.message.text.upper(); context.user_data.pop("aw")
        await update.message.reply_text(f"*{context.user_data['pair']}*\n\nНаправление:",parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📈 Long",callback_data="dir_Long"),InlineKeyboardButton("📉 Short",callback_data="dir_Short")]]))
        return DIRECTION

async def dir_sel(update,context):
    q=update.callback_query; await q.answer()
    context.user_data["direction"]=q.data.replace("dir_","")
    await q.edit_message_text(f"*{context.user_data['pair']}* {context.user_data['direction']}\n\n✏️ Цена входа:",parse_mode="Markdown")
    return ENTRY

async def entry_h(update,context):
    try: context.user_data["entry"]=float(update.message.text.replace(",","."))
    except: await update.message.reply_text("❌ Число, например: 1.0845"); return ENTRY
    await update.message.reply_text("✏️ Цена выхода:"); return EXIT

async def exit_h(update,context):
    try: context.user_data["exit"]=float(update.message.text.replace(",","."))
    except: await update.message.reply_text("❌ Число"); return EXIT
    await update.message.reply_text("🎯 Стоп-лосс (необязательно):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_sl")]]))
    return STOP

async def stop_h(update,context):
    try: context.user_data["sl"]=float(update.message.text.replace(",","."))
    except: await update.message.reply_text("❌ Число"); return STOP
    await update.message.reply_text("✏️ Лот (например 0.1):"); return LOT

async def skip_sl_cb(update,context):
    q=update.callback_query; await q.answer(); context.user_data["sl"]=None
    await q.edit_message_text("✏️ Лот:"); return LOT

async def lot_h(update,context):
    try: context.user_data["lot"]=float(update.message.text.replace(",","."))
    except: await update.message.reply_text("❌ Число"); return LOT
    await update.message.reply_text("💰 Результат в $ (+50 или -30):"); return RESULT

async def result_h(update,context):
    try: context.user_data["result"]=float(update.message.text.replace(",",".").replace(" ",""))
    except: await update.message.reply_text("❌ Число со знаком: +50 или -30"); return RESULT
    context.user_data["flow"]="manual"
    await update.message.reply_text("🕐 Сессия:",reply_markup=sess_kb())
    return SESSION

async def sess_in_conv(update,context):
    q=update.callback_query; await q.answer()
    context.user_data["session"]=None if q.data=="sess_skip" else SESSIONS[int(q.data.replace("sess_",""))]
    today=date.today().isoformat()
    await q.edit_message_text(f"📅 Дата сделки\nСегодня: {today}\n\nВведи дату ГГГГ-ММ-ДД или нажми Сегодня:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Сегодня",callback_data="date_today")]]))
    return TRADE_DATE

async def date_today_cb(update,context):
    q=update.callback_query; await q.answer()
    context.user_data["trade_date"]=date.today().isoformat()
    await q.edit_message_text("💬 Комментарий (необязательно):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_comment")]]))
    return COMMENT

async def date_h(update,context):
    try:
        context.user_data["trade_date"]=datetime.strptime(update.message.text.strip(),"%Y-%m-%d").date().isoformat()
    except: await update.message.reply_text("❌ Формат: 2026-04-30"); return TRADE_DATE
    await update.message.reply_text("💬 Комментарий:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_comment")]]))
    return COMMENT

async def comment_h(update,context):
    context.user_data["comment"]=update.message.text
    await update.message.reply_text("📸 Скрин графика (необязательно):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_chart")]]))
    context.user_data["awaiting_chart"]=True; return CHART_PHOTO

async def skip_comment_cb(update,context):
    q=update.callback_query; await q.answer(); context.user_data["comment"]=""
    await q.edit_message_text("📸 Скрин графика (необязательно):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_chart")]]))
    context.user_data["awaiting_chart"]=True; return CHART_PHOTO

async def chart_in_conv(update,context):
    context.user_data["chart_file_id"]=update.message.photo[-1].file_id
    context.user_data.pop("awaiting_chart",None)
    await _finalize(update,context,is_msg=True); return ConversationHandler.END

async def cancel_h(update,context):
    context.user_data.clear()
    await update.message.reply_text("Отменено.",reply_markup=main_kb()); return ConversationHandler.END

# ─── STATS ───────────────────────────────────────────────────────────────────

async def show_stats(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    rows=get_all_trades(uid)
    if not rows:
        await q.edit_message_text("📊 Пока нет сделок.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад",callback_data="menu")]])); return
    results=[r[0] for r in rows]; total=len(results)
    wins=sum(1 for r in results if r>0); losses=sum(1 for r in results if r<0)
    wr=(wins/total*100) if total else 0; pnl=sum(results)
    aw=(sum(r for r in results if r>0)/wins) if wins else 0
    al=(sum(r for r in results if r<0)/losses) if losses else 0
    rrs=[r[4] for r in rows if r[4]]; avg_rr=(sum(rrs)/len(rrs)) if rrs else None
    pp={}
    for r in rows: pp[r[1]]=pp.get(r[1],0)+r[0]
    bp=max(pp,key=pp.get) if pp else "—"; wp=min(pp,key=pp.get) if pp else "—"
    sp={}
    for r in rows:
        s=r[3] or "Нет"; sp[s]=sp.get(s,0)+r[0]
    bs=max(sp,key=sp.get) if sp else "—"
    msg=(f"📊 *Статистика*\n\n"
         f"🔢 Сделок: {total} | ✅ {wins} / ❌ {losses}\n"
         f"🎯 Винрейт: {wr:.1f}%"
         +(f" | 📐 RR: {avg_rr:.2f}" if avg_rr else "")+"\n\n"
         f"💰 *PnL: {'+'if pnl>=0 else ''}{pnl:.2f}$*\n"
         f"📈 Ср.профит: +{aw:.2f}$ | 📉 Ср.убыток: {al:.2f}$\n\n"
         f"🏆 Лучшая пара: {bp} ({pp.get(bp,0):+.0f}$)\n"
         f"⚠️  Слабая пара: {wp} ({pp.get(wp,0):+.0f}$)\n"
         f"🕐 Лучшая сессия: {bs}")
    await q.edit_message_text(msg,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад",callback_data="menu")]]))

# ─── CALENDAR ────────────────────────────────────────────────────────────────

async def show_calendar(update,context):
    q=update.callback_query; await q.answer(); now=datetime.now()
    context.user_data["cy"]=now.year; context.user_data["cm"]=now.month
    await render_cal(q,context)

async def render_cal(q,context):
    uid=q.from_user.id; y=context.user_data.get("cy"); m=context.user_data.get("cm")
    td=get_trade_dates(uid,y,m)
    mn=["","Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"][m]
    kb=[[InlineKeyboardButton(f"📅 {mn} {y}",callback_data="noop")],
        [InlineKeyboardButton(d,callback_data="noop") for d in ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]]]
    for week in calendar.monthcalendar(y,m):
        row=[]
        for day in week:
            if day==0: row.append(InlineKeyboardButton(" ",callback_data="noop"))
            else:
                ds=f"{y}-{m:02d}-{day:02d}"
                if ds in td: row.append(InlineKeyboardButton(f"{'🟢' if td[ds]>=0 else '🔴'}{day}",callback_data=f"day_{ds}"))
                else: row.append(InlineKeyboardButton(str(day),callback_data=f"day_{ds}"))
        kb.append(row)
    pm=m-1 if m>1 else 12; py=y if m>1 else y-1
    nm=m+1 if m<12 else 1; ny=y if m<12 else y+1
    kb.append([InlineKeyboardButton("◀️",callback_data=f"cal_{py}_{pm}"),InlineKeyboardButton("▶️",callback_data=f"cal_{ny}_{nm}")])
    kb.append([InlineKeyboardButton("◀️ Назад",callback_data="menu")])
    await q.edit_message_text("📅 *Календарь*\n🟢 профит | 🔴 убыток",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))

async def cal_nav(update,context):
    q=update.callback_query; await q.answer()
    _,y,m=q.data.split("_"); context.user_data["cy"]=int(y); context.user_data["cm"]=int(m)
    await render_cal(q,context)

async def show_day(update,context):
    q=update.callback_query; await q.answer()
    ds=q.data.replace("day_",""); uid=q.from_user.id
    trades=get_trades(uid,filter_date=ds)
    df=datetime.strptime(ds,"%Y-%m-%d").strftime("%d.%m.%Y")
    if not trades: msg=f"📅 *{df}*\n\nСделок нет."
    else:
        dp=sum(t[9] for t in trades); pe="📈" if dp>=0 else "📉"
        msg=f"📅 *{df}* | {pe} {dp:+.2f}$\n\n"
        for t in trades:
            e="✅" if t[9]>0 else "❌"
            msg+=f"{e} `#{t[0]}` *{t[3]}* {t[4]}"
            if t[10]: msg+=f" | RR {t[10]}"
            if t[11]: msg+=f" | {t[11]}"
            msg+=f"\n   {t[5]} → {t[6]} | Лот {t[8]} | {t[9]:+.2f}$\n"
            if t[12]: msg+=f"   💬 {t[12]}\n"
            msg+="\n"
    btns=[[InlineKeyboardButton("◀️ Календарь",callback_data="calendar")]]
    if trades:
        for t in trades:
            if t[13]: btns.insert(0,[InlineKeyboardButton(f"🖼 График #{t[0]}",callback_data=f"chart_{t[0]}")])
    await q.edit_message_text(msg,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(btns))

async def show_chart(update,context):
    q=update.callback_query; await q.answer()
    tid=int(q.data.replace("chart_","")); uid=q.from_user.id
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT chart_file_id FROM trades WHERE id=%s AND user_id=%s",(tid,uid))
    row=c.fetchone(); conn.close()
    if row and row[0]: await context.bot.send_photo(q.message.chat_id,photo=row[0],caption=f"🖼 График #{tid}")
    else: await q.answer("График не найден",show_alert=True)

# ─── HISTORY ─────────────────────────────────────────────────────────────────

async def show_history(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    trades=get_trades(uid,limit=20)
    if not trades:
        await q.edit_message_text("📋 Пока нет сделок.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад",callback_data="menu")]])); return
    msg="📋 *Последние 20 сделок*\n\n"
    for t in trades:
        e="✅" if t[9]>0 else "❌"
        msg+=f"{e} `#{t[0]}` {t[2]} | *{t[3]}* {t[4]}"
        if t[10]: msg+=f" RR{t[10]}"
        msg+=f" | {t[9]:+.2f}$\n"
    msg+="\nУдалить: /delete\\_5"
    await q.edit_message_text(msg,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад",callback_data="menu")]]))

async def delete_cmd(update,context):
    uid=update.effective_user.id
    try:
        delete_trade_db(int(update.message.text.split("_")[1]),uid)
        await update.message.reply_text("✅ Удалено.",reply_markup=main_kb())
    except: await update.message.reply_text("❌ Формат: /delete_5")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app=Application.builder().token(TOKEN).build()
    conv=ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start,pattern="^add$")],
        states={
            PAIR:[CallbackQueryHandler(pair_sel,pattern="^pair_"),CallbackQueryHandler(menu_cb,pattern="^menu$"),MessageHandler(filters.TEXT&~filters.COMMAND,pair_txt)],
            DIRECTION:[CallbackQueryHandler(dir_sel,pattern="^dir_")],
            ENTRY:[MessageHandler(filters.TEXT&~filters.COMMAND,entry_h)],
            EXIT:[MessageHandler(filters.TEXT&~filters.COMMAND,exit_h)],
            STOP:[MessageHandler(filters.TEXT&~filters.COMMAND,stop_h),CallbackQueryHandler(skip_sl_cb,pattern="^skip_sl$")],
            LOT:[MessageHandler(filters.TEXT&~filters.COMMAND,lot_h)],
            RESULT:[MessageHandler(filters.TEXT&~filters.COMMAND,result_h)],
            SESSION:[CallbackQueryHandler(sess_in_conv,pattern="^sess_")],
            TRADE_DATE:[MessageHandler(filters.TEXT&~filters.COMMAND,date_h),CallbackQueryHandler(date_today_cb,pattern="^date_today$")],
            COMMENT:[MessageHandler(filters.TEXT&~filters.COMMAND,comment_h),CallbackQueryHandler(skip_comment_cb,pattern="^skip_comment$")],
            CHART_PHOTO:[MessageHandler(filters.PHOTO,chart_in_conv),CallbackQueryHandler(skip_chart,pattern="^skip_chart$")],
        },
        fallbacks=[CommandHandler("cancel",cancel_h)],per_message=False
    )
    app.add_handler(CommandHandler("start",start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(menu_cb,pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(screenshot_prompt,pattern="^screenshot$"))
    app.add_handler(CallbackQueryHandler(mt5_next,pattern="^mt5_next$"))
    app.add_handler(CallbackQueryHandler(mt5_skip_sl,pattern="^mt5_skip_sl$"))
    app.add_handler(CallbackQueryHandler(sess_cb,pattern="^sess_"))
    app.add_handler(CallbackQueryHandler(skip_chart,pattern="^skip_chart$"))
    app.add_handler(CallbackQueryHandler(show_stats,pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(show_calendar,pattern="^calendar$"))
    app.add_handler(CallbackQueryHandler(cal_nav,pattern="^cal_"))
    app.add_handler(CallbackQueryHandler(show_day,pattern="^day_"))
    app.add_handler(CallbackQueryHandler(show_chart,pattern="^chart_"))
    app.add_handler(CallbackQueryHandler(show_history,pattern="^history$"))
    app.add_handler(CallbackQueryHandler(lambda u,c:None,pattern="^noop$"))
    app.add_handler(MessageHandler(filters.PHOTO,handle_photo))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,mt5_text))
    app.add_handler(MessageHandler(filters.Regex(r"^/delete_\d+$"),delete_cmd))
    print("Бот запущен v9...")
    app.run_polling(drop_pending_updates=True,allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()

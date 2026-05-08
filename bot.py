import os
import requests
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN          = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY")

# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def fmt(n, dec=0):
    if n is None: return "-"
    if dec:
        return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{int(round(n)):,}".replace(",", ".")

def fmt_vol(v):
    if not v: return "-"
    if v >= 1_000_000_000: return f"{v/1_000_000_000:.2f}M"
    if v >= 1_000_000:     return f"{v/1_000_000:.1f}Jt"
    if v >= 1_000:         return f"{v/1_000:.1f}K"
    return str(v)

def pct(a, b):
    if not b: return 0
    return (a - b) / b * 100

# ══════════════════════════════════════════════════════════════════
#  DATA SAHAM
# ══════════════════════════════════════════════════════════════════

def get_stock_data(ticker: str) -> dict:
    symbol = ticker.upper() + ".JK"
    url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=20d"
    r      = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.raise_for_status()
    data   = r.json()
    res    = data["chart"]["result"]
    if not res:
        raise ValueError("Saham tidak ditemukan. Pastikan kode saham benar.")
    meta   = res[0]["meta"]
    closes = res[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
    closes = [c for c in closes if c is not None]

    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    prev  = meta.get("previousClose")      or meta.get("chartPreviousClose")

    return {
        "symbol" : ticker.upper(),
        "name"   : meta.get("shortName", ticker.upper()),
        "price"  : price,
        "open"   : meta.get("regularMarketOpen"),
        "high"   : meta.get("regularMarketDayHigh"),
        "low"    : meta.get("regularMarketDayLow"),
        "prev"   : prev,
        "volume" : meta.get("regularMarketVolume"),
        "high52" : meta.get("fiftyTwoWeekHigh"),
        "low52"  : meta.get("fiftyTwoWeekLow"),
        "closes" : closes,
    }

# ══════════════════════════════════════════════════════════════════
#  KALKULASI TEKNIKAL
# ══════════════════════════════════════════════════════════════════

def calc_technicals(d: dict) -> dict:
    price  = d["price"]
    high   = d["high"]
    low    = d["low"]
    closes = d["closes"]

    # Pivot Point
    pivot = (high + low + price) / 3
    r1 = 2 * pivot - low
    r2 = pivot + (high - low)
    r3 = high + 2 * (pivot - low)
    s1 = 2 * pivot - high
    s2 = pivot - (high - low)
    s3 = low - 2 * (high - pivot)

    # Moving Average
    ma5  = sum(closes[-5:])  / len(closes[-5:])  if len(closes) >= 5  else price
    ma10 = sum(closes[-10:]) / len(closes[-10:]) if len(closes) >= 10 else price
    ma20 = sum(closes[-20:]) / len(closes[-20:]) if len(closes) >= 20 else price

    # ATR sederhana
    atr = (high - low)
    if len(closes) >= 6:
        ranges = [abs(closes[i] - closes[i-1]) for i in range(-5, 0)]
        atr = sum(ranges) / len(ranges)

    # Trend
    trend = "UPTREND"
    if price < ma5 < ma20:
        trend = "DOWNTREND"
    elif ma5 < ma20:
        trend = "SIDEWAYS"

    return {
        "pivot": pivot,
        "r1": r1, "r2": r2, "r3": r3,
        "s1": s1, "s2": s2, "s3": s3,
        "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "atr": atr,
        "trend": trend,
    }

def calc_tp_sl(d: dict, tech: dict, sinyal: str) -> dict:
    price = d["price"]
    atr   = tech["atr"]

    if sinyal == "BELI":
        entry = price
        sl  = max(tech["s1"], price - 1.5 * atr)
        sl  = min(sl, price * 0.93)
        tp1 = max(min(tech["r1"], price + 1.0 * atr), price * 1.03)
        tp2 = max(min(tech["r2"], price + 2.0 * atr), price * 1.06)
        tp3 = max(min(tech["r3"], price + 3.5 * atr), price * 1.10)
    elif sinyal == "JUAL":
        entry = price
        sl  = max(min(tech["r1"], price + 1.5 * atr), price * 1.07)
        tp1 = min(max(tech["s1"], price - 1.0 * atr), price * 0.97)
        tp2 = min(max(tech["s2"], price - 2.0 * atr), price * 0.94)
        tp3 = min(max(tech["s3"], price - 3.5 * atr), price * 0.90)
    else:
        entry = price
        sl  = price * 0.95
        tp1 = price * 1.03
        tp2 = price * 1.06
        tp3 = price * 1.10

    return {
        "entry": round(entry),
        "sl":    round(sl),
        "tp1":   round(tp1),
        "tp2":   round(tp2),
        "tp3":   round(tp3),
    }

# ══════════════════════════════════════════════════════════════════
#  AI ANALYSIS
# ══════════════════════════════════════════════════════════════════

def get_ai_signal(d: dict, tech: dict) -> str:
    prompt = f"""Kamu adalah analis teknikal saham Indonesia profesional.
Berikan analisis SINGKAT dan AKURAT berdasarkan data berikut:

SAHAM  : {d['symbol']} ({d['name']})
Harga  : Rp {fmt(d['price'])} (prev Rp {fmt(d['prev'])}, {pct(d['price'],d['prev']):+.2f}%)
Open   : Rp {fmt(d['open'])}  High: Rp {fmt(d['high'])}  Low: Rp {fmt(d['low'])}
Volume : {fmt_vol(d['volume'])}
52W    : High Rp {fmt(d['high52'])} | Low Rp {fmt(d['low52'])}

INDIKATOR TEKNIKAL:
MA5    : Rp {fmt(tech['ma5'])}
MA10   : Rp {fmt(tech['ma10'])}
MA20   : Rp {fmt(tech['ma20'])}
Trend  : {tech['trend']}
ATR    : Rp {fmt(tech['atr'])}
Pivot  : Rp {fmt(tech['pivot'])}
R1/R2/R3: Rp {fmt(tech['r1'])} / Rp {fmt(tech['r2'])} / Rp {fmt(tech['r3'])}
S1/S2/S3: Rp {fmt(tech['s1'])} / Rp {fmt(tech['s2'])} / Rp {fmt(tech['s3'])}

Berikan analisis dalam format PERSIS ini:

SINYAL: [BELI atau JUAL atau HOLD]

KONDISI_PASAR:
[1-2 kalimat kondisi pasar saat ini]

ALASAN:
• [alasan 1]
• [alasan 2]
• [alasan 3]

KEKUATAN_SINYAL: [KUAT atau SEDANG atau LEMAH]

CATATAN:
[1 kalimat catatan risiko atau peluang]

Jawab langsung tanpa preamble. Bahasa Indonesia."""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key"         : ANTHROPIC_API_KEY,
            "anthropic-version" : "2023-06-01",
            "content-type"      : "application/json",
        },
        json={
            "model"     : "claude-sonnet-4-20250514",
            "max_tokens": 600,
            "messages"  : [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]

def parse_sinyal(ai_text: str) -> str:
    for line in ai_text.splitlines():
        if line.strip().upper().startswith("SINYAL:"):
            val = line.split(":", 1)[1].strip().upper()
            if "BELI" in val: return "BELI"
            if "JUAL" in val: return "JUAL"
    return "HOLD"

def build_signal_message(d: dict, tech: dict, ai_text: str, tpsl: dict, modal: int) -> str:
    sinyal = parse_sinyal(ai_text)
    change = pct(d["price"], d["prev"])

    if sinyal == "BELI":   sinyal_line = "🟢 *SINYAL: BELI* 🟢"
    elif sinyal == "JUAL": sinyal_line = "🔴 *SINYAL: JUAL* 🔴"
    else:                  sinyal_line = "🟡 *SINYAL: HOLD* 🟡"

    # Parse AI text
    kekuatan = "👌 SEDANG"
    kondisi = alasan = catatan = ""
    mode = None
    for line in ai_text.splitlines():
        l = line.strip()
        up = l.upper()
        if up.startswith("SINYAL:"): mode = None
        elif up.startswith("KONDISI_PASAR:"): mode = "k"
        elif up.startswith("ALASAN:"): mode = "a"
        elif up.startswith("KEKUATAN_SINYAL:"):
            mode = None
            k = l.split(":", 1)[1].strip().upper()
            if "KUAT" in k:   kekuatan = "💪 KUAT"
            elif "LEMAH" in k: kekuatan = "⚠️ LEMAH"
        elif up.startswith("CATATAN:"): mode = "c"
        elif mode == "k" and l: kondisi += l + " "
        elif mode == "a" and l: alasan  += l + "\n"
        elif mode == "c" and l: catatan += l + " "

    # Simulasi lot & P&L
    lot = int(modal / (d["price"] * 100)) if d["price"] else 0
    modal_used = lot * 100 * d["price"]

    def pl(tp):
        gross = lot * 100 * (tp - d["price"])
        fee   = lot * 100 * tp * 0.002
        return gross - fee

    lines = [
        f"{'📈' if change >= 0 else '📉'} *{d['symbol']}* — {d['name']}",
        f"💵 Harga: *Rp {fmt(d['price'])}* ({change:+.2f}%)",
        f"Vol: {fmt_vol(d['volume'])}  |  52W: {fmt(d['low52'])}–{fmt(d['high52'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        sinyal_line,
        f"Kekuatan Sinyal: {kekuatan}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🎯 *ENTRY*  : Rp {fmt(tpsl['entry'])}",
        "",
        "✅ *TAKE PROFIT:*",
        f"  🥇 TP1 : Rp {fmt(tpsl['tp1'])}  ({pct(tpsl['tp1'], tpsl['entry']):+.1f}%)",
        f"  🥈 TP2 : Rp {fmt(tpsl['tp2'])}  ({pct(tpsl['tp2'], tpsl['entry']):+.1f}%)",
        f"  🥉 TP3 : Rp {fmt(tpsl['tp3'])}  ({pct(tpsl['tp3'], tpsl['entry']):+.1f}%)",
        "",
        f"🛑 *STOP LOSS* : Rp {fmt(tpsl['sl'])}  ({pct(tpsl['sl'], tpsl['entry']):+.1f}%)",
        "",
        "📊 *SUPPORT & RESISTANCE:*",
        f"  🔴 R1: {fmt(tech['r1'])}  R2: {fmt(tech['r2'])}  R3: {fmt(tech['r3'])}",
        f"  🟢 S1: {fmt(tech['s1'])}  S2: {fmt(tech['s2'])}  S3: {fmt(tech['s3'])}",
        "",
        f"📉 MA5: {fmt(tech['ma5'])}  MA10: {fmt(tech['ma10'])}  MA20: {fmt(tech['ma20'])}",
        f"📐 Trend: *{tech['trend']}*  |  ATR: Rp {fmt(tech['atr'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🤖 *ANALISIS AI:*",
    ]

    if kondisi: lines.append(kondisi.strip())
    if alasan:
        lines.append("")
        lines.append(alasan.strip())
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"💰 *SIMULASI MODAL Rp {fmt(modal)}:*")

    if lot > 0:
        lines += [
            f"  Bisa beli  : *{lot} lot* ({lot*100:,} lbr)",
            f"  Modal pakai: Rp {fmt(modal_used)}",
            f"  Sisa cash  : Rp {fmt(modal - modal_used)}",
            "",
            f"  💵 Profit TP1: *+Rp {fmt(pl(tpsl['tp1']))}*",
            f"  💵 Profit TP2: *+Rp {fmt(pl(tpsl['tp2']))}*",
            f"  💵 Profit TP3: *+Rp {fmt(pl(tpsl['tp3']))}*",
            f"  💸 Rugi SL   : *-Rp {fmt(abs(pl(tpsl['sl'])))}*",
        ]
    else:
        lines += [
            f"  ⚠️ Modal tidak cukup beli 1 lot",
            f"  (Butuh min Rp {fmt(d['price']*100)} untuk 1 lot)",
        ]

    if catatan:
        lines += ["", f"💡 {catatan.strip()}"]

    lines += [
        "",
        "📌 *Cara pakai TP:*",
        "  Di TP1 → jual 50% | TP2 → jual 30% | TP3 → jual 20%",
        "",
        "⚠️ _Disclaimer: Bukan saran investasi profesional. DYOR._",
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *BEI TRADING SIGNAL BOT*\n\n"
        "Analisis saham Indonesia dengan sinyal *TP1 · TP2 · TP3 · Stop Loss*\n\n"
        "*PERINTAH:*\n"
        "📊 `/sinyal BBCA` — sinyal lengkap TP & SL\n"
        "📊 `/sinyal BBCA 2000000` — dengan modal kustom\n"
        "💰 `/average BBCA 2 1000 3 900` — hitung average\n"
        "✂️ `/cutloss BBCA 1200 2` — analisis cut loss\n"
        "📋 `/portofolio` — tips kelola modal kecil\n"
        "❓ `/help` — panduan lengkap\n\n"
        "_Kirim kode saham langsung: *TLKM*_\n\n"
        "⚠️ _Bukan saran investasi profesional._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *PANDUAN BEI SIGNAL BOT*\n\n"
        "*1. Sinyal TP & SL*\n"
        "`/sinyal BBCA`\n"
        "`/sinyal BBCA 2000000` _(modal kustom)_\n\n"
        "*2. Average Down/Up*\n"
        "`/average BBCA 2 9500 3 9000`\n"
        "→ kode lot1 harga1 lot2 harga2 ...\n\n"
        "*3. Cut Loss Analyzer*\n"
        "`/cutloss BBCA 1200` _(harga beli)_\n"
        "`/cutloss BBCA 1200 3` _(+ jumlah lot)_\n\n"
        "*4. Tips Portofolio*\n"
        "`/portofolio`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 *Cara Baca TP:*\n"
        "• TP1 → Jual 50% posisi (ambil profit awal)\n"
        "• TP2 → Jual 30% posisi (profit lebih besar)\n"
        "• TP3 → Jual 20% sisa (profit maksimal)\n"
        "• SL  → Jual semua jika harga menyentuh ini\n\n"
        "💡 Selalu pasang SL dulu sebelum beli!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_sinyal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Format: `/sinyal BBCA` atau `/sinyal BBCA 2000000`", parse_mode="Markdown")
        return

    ticker = ctx.args[0].upper()
    modal  = 1_000_000
    if len(ctx.args) >= 2:
        try: modal = int(ctx.args[1])
        except: pass

    msg = await update.message.reply_text(f"⏳ Mengambil data *{ticker}*...", parse_mode="Markdown")
    try:
        d    = get_stock_data(ticker)
        tech = calc_technicals(d)
        await msg.edit_text(f"⏳ AI menganalisis *{ticker}*...", parse_mode="Markdown")
        ai_text = get_ai_signal(d, tech)
        sinyal  = parse_sinyal(ai_text)
        tpsl    = calc_tp_sl(d, tech, sinyal)
        full    = build_signal_message(d, tech, ai_text, tpsl, modal)
        await msg.edit_text(full, parse_mode="Markdown")
    except Exception as e:
        logger.exception(e)
        await msg.edit_text(f"❌ Error: {str(e)}\nContoh kode saham: BBCA TLKM GOTO BBRI")

async def cmd_average(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "❌ Format: `/average BBCA 2 1000 3 900`", parse_mode="Markdown")
        return

    ticker = ctx.args[0].upper()
    nums   = ctx.args[1:]
    if len(nums) % 2 != 0:
        await update.message.reply_text("❌ Pasangan lot-harga harus genap.", parse_mode="Markdown")
        return

    try:
        lp = [(int(nums[i]), float(nums[i+1])) for i in range(0, len(nums), 2)]
    except:
        await update.message.reply_text("❌ Format angka salah.", parse_mode="Markdown")
        return

    msg = await update.message.reply_text(f"⏳ Mengambil harga *{ticker}*...", parse_mode="Markdown")
    try:
        d           = get_stock_data(ticker)
        current     = d["price"]
        total_lot   = sum(l for l, _ in lp)
        total_modal = sum(l * 100 * p for l, p in lp)
        avg         = total_modal / (total_lot * 100)
        pl_val      = (current - avg) * total_lot * 100
        pl_p        = pct(current, avg)
        status      = "🟢 UNTUNG" if pl_val >= 0 else "🔴 RUGI"
        sign        = "+" if pl_val >= 0 else "-"

        transaksi = "\n".join([f"  Tx{i+1}: {l} lot @ Rp {fmt(p)}" for i, (l,p) in enumerate(lp)])

        tp1 = avg * 1.05
        tp2 = avg * 1.10
        tp3 = avg * 1.15
        sl  = avg * 0.95

        text = (
            f"📊 *AVERAGE — {ticker}*\n\n"
            f"*Transaksi:*\n{transaksi}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Avg Price   : *Rp {fmt(avg)}*\n"
            f"📌 Total Lot   : *{total_lot} lot* ({total_lot*100:,} lbr)\n"
            f"📌 Total Modal : Rp {fmt(total_modal)}\n"
            f"📌 Harga Skrg  : Rp {fmt(current)}\n"
            f"📌 P&L         : *{sign}Rp {fmt(abs(pl_val))} ({pl_p:+.2f}%)*\n"
            f"📌 Status      : {status}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *TARGET DARI HARGA AVG:*\n"
            f"  🥇 TP1: Rp {fmt(tp1)} (+5%)\n"
            f"  🥈 TP2: Rp {fmt(tp2)} (+10%)\n"
            f"  🥉 TP3: Rp {fmt(tp3)} (+15%)\n"
            f"  🛑 SL : Rp {fmt(sl)}  (-5%)\n"
        )
        if pl_val < 0:
            avg2 = (avg + current) / 2
            text += (
                f"\n💡 *Simulasi average down 1x lagi di harga skrg:*\n"
                f"  Avg baru ≈ Rp {fmt(avg2)}\n"
                f"  Perlu naik {pct(avg, avg2):.1f}% untuk BEP\n"
            )
        text += "\n⚠️ _Average down hanya jika yakin fundamental/teknikal masih bagus._"
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def cmd_cutloss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "❌ Format: `/cutloss BBCA 1200` atau `/cutloss BBCA 1200 3` _(+ lot)_",
            parse_mode="Markdown")
        return

    ticker = ctx.args[0].upper()
    try: buy = float(ctx.args[1])
    except:
        await update.message.reply_text("❌ Harga beli tidak valid.")
        return

    lot = 1
    if len(ctx.args) >= 3:
        try: lot = int(ctx.args[2])
        except: pass

    msg = await update.message.reply_text(f"⏳ Analisis cut loss *{ticker}*...", parse_mode="Markdown")
    try:
        d       = get_stock_data(ticker)
        tech    = calc_technicals(d)
        current = d["price"]
        loss_p  = pct(current, buy)
        status  = "🟢 UNTUNG" if current >= buy else "🔴 RUGI"
        pl_val  = (current - buy) * lot * 100

        cl_5  = buy * 0.95
        cl_7  = buy * 0.93
        cl_10 = buy * 0.90

        s1 = tech["s1"]
        if current < s1:
            rek = f"⚠️ Harga sudah *di bawah S1 (Rp {fmt(s1)})*. Pertimbangkan cut loss sekarang."
        elif current < buy * 0.93:
            rek = f"⚠️ Sudah turun lebih dari 7%. Sangat disarankan cut loss segera."
        elif current < buy * 0.97:
            rek = f"💡 Turun 3-7%. Pantau ketat. Cut jika tembus Rp {fmt(cl_7)}."
        else:
            rek = f"✅ Masih aman. Monitor support S1 di Rp {fmt(s1)}."

        sign = "+" if pl_val >= 0 else "-"
        text = (
            f"✂️ *CUT LOSS ANALYZER — {ticker}*\n\n"
            f"📌 Harga Beli : Rp {fmt(buy)}\n"
            f"📌 Harga Skrg : Rp {fmt(current)}\n"
            f"📌 Posisi     : {lot} lot ({lot*100} lbr)\n"
            f"📌 Status     : {status} ({loss_p:+.2f}%)\n"
            f"📌 P&L        : *{sign}Rp {fmt(abs(pl_val))}*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 *LEVEL STOP LOSS:*\n"
            f"  Ketat  (-5%) : Rp {fmt(cl_5)}\n"
            f"  Normal (-7%) : Rp {fmt(cl_7)}\n"
            f"  Longgar(-10%): Rp {fmt(cl_10)}\n\n"
            f"📊 *SUPPORT TERDEKAT:*\n"
            f"  S1: Rp {fmt(tech['s1'])}  S2: Rp {fmt(tech['s2'])}  S3: Rp {fmt(tech['s3'])}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{rek}\n\n"
            f"💡 Untuk modal kecil, max cut loss -7% agar modal tersisa.\n\n"
            f"⚠️ _Bukan saran investasi profesional._"
        )
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def cmd_portofolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *STRATEGI PORTOFOLIO MODAL Rp 1 JUTA*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *Alokasi:*\n"
        "  60% → 1 saham utama (Rp 600rb)\n"
        "  30% → 1 saham cadangan (Rp 300rb)\n"
        "  10% → Cash darurat (Rp 100rb)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *Strategi TP Bertahap:*\n"
        "  TP1 → Jual 50% (amankan profit)\n"
        "  TP2 → Jual 30% (profit lebih)\n"
        "  TP3 → Jual 20% (maksimalkan)\n\n"
        "🛑 *Aturan Stop Loss:*\n"
        "  • Pasang SL sebelum beli\n"
        "  • Max loss per trade: -5% sd -7%\n"
        "  • Jangan average down sembarangan\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 *Checklist Sebelum Beli:*\n"
        "  ☐ Sinyal teknikal jelas\n"
        "  ☐ Sudah tentukan TP1, TP2, TP3\n"
        "  ☐ Sudah tentukan Stop Loss\n"
        "  ☐ Volume di atas normal\n"
        "  ☐ Tidak FOMO\n\n"
        "📌 *Saham Cocok Modal Kecil:*\n"
        "  Harga Rp 50–500/lembar\n"
        "  agar dapat lebih banyak lot\n\n"
        "💪 *Konsistensi > Profit besar sekali!*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper().split()[0]
    if text.isalpha() and 2 <= len(text) <= 6:
        ctx.args = [text]
        await cmd_sinyal(update, ctx)
    else:
        await update.message.reply_text(
            "❓ Tidak mengerti. Ketik `/help` untuk panduan.\n"
            "Atau kirim kode saham langsung, contoh: *BBCA*",
            parse_mode="Markdown"
        )

# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("sinyal",     cmd_sinyal))
    app.add_handler(CommandHandler("analisis",   cmd_sinyal))
    app.add_handler(CommandHandler("average",    cmd_average))
    app.add_handler(CommandHandler("cutloss",    cmd_cutloss))
    app.add_handler(CommandHandler("portofolio", cmd_portofolio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("BEI Signal Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()

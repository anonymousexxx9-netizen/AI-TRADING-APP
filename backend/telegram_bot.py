"""
telegram_bot.py — Handler & Application untuk sisi Telegram.
Semua logic berat (indikator, harga, kalender, AI, dsb) ada di core.py.
"""

import os
import io
import asyncio
from datetime import datetime
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import core
from core import (
    header,
    FOOTER,
    format_symbol,
    get_forex_price,
    _ohlcv_or_error,
    calculate_indicators,
    calculate_confidence,
    get_regime,
    get_support_resistance,
    detect_trap,
    extract_pair,
    is_price_question,
    is_calendar_question,
    is_regime_question,
    is_news_question,
    full_analysis,
    get_calendar,
    format_calendar_for_prompt,
    get_calendar_actual_fallback,
    search_news,
    generate_macro_briefing,
    generate_daily_debrief,
    analyze_with_groq,
    analyze_image_with_groq,
    get_history,
    add_to_history,
    get_confluence,
    calculate_lot_size,
    get_correlation_matrix,
    VALID_OPERATORS,
)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]



TELEGRAM_MAX_LEN = 4000  # buffer di bawah limit resmi Telegram (4096 karakter)


async def safe_reply(update: Update, text: str, parse_mode: str = "Markdown"):
    """Reply with Markdown; fall back to plain text if parsing fails.
    Otomatis dipecah kalau > TELEGRAM_MAX_LEN karakter (limit resmi Telegram
    4096) — beberapa command sekarang bisa menghasilkan teks panjang
    (Technical Score breakdown, S/R dengan strength, event prediction)."""
    chunks = [text]
    if len(text) > TELEGRAM_MAX_LEN:
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > TELEGRAM_MAX_LEN:
                if current:
                    chunks.append(current)
                while len(line) > TELEGRAM_MAX_LEN:
                    chunks.append(line[:TELEGRAM_MAX_LEN])
                    line = line[TELEGRAM_MAX_LEN:]
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)

    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            await update.message.reply_text(chunk, parse_mode=parse_mode)
        except BadRequest:
            await update.message.reply_text(chunk)


async def build_prompt_with_context(update: Update, message: str) -> str:
    """Detect keywords, inject realtime data, and return the final Groq prompt."""
    detected_pair = extract_pair(message) if is_price_question(message) else None

    if detected_pair:
        await safe_reply(update, "⏳ _Mengambil harga terkini..._")
        price_result = get_forex_price(detected_pair)
        if "error" not in price_result:
            now = datetime.now().strftime("%d %b %Y, %H:%M:%S")
            return (
                f"Data harga realtime saat ini: {detected_pair} = {price_result['price']} "
                f"(update: {now}).\n\nPertanyaan user: {message}"
            )
        return message

    if is_calendar_question(message):
        await safe_reply(update, "⏳ _Mengambil data kalender..._")
        events = get_calendar(filter_country="USD")
        calendar_text = format_calendar_for_prompt(events)

        wants_actual = any(kw in message.lower() for kw in ("actual", "hasil", "yang telah", "yang sudah"))
        if wants_actual and events:
            await safe_reply(update, "🔍 _Actual kosong di feed, mencari cadangan dari web..._")
            fallback_text = get_calendar_actual_fallback(events)
            if fallback_text:
                calendar_text += f"\n\n{fallback_text}"

        return (
            f"Berikut data kalender ekonomi USD minggu ini dari Forex Factory, termasuk nilai Actual "
            f"yang sudah dirilis kalau ada (data real, bukan dari memori kamu):\n{calendar_text}\n\n"
            f"Pertanyaan user: {message}"
        )

    if is_news_question(message):
        await safe_reply(update, "🔍 _Mencari berita terbaru..._")
        news_text = search_news(message)
        return (
            f"Berikut berita/pernyataan terbaru yang relevan (dari pencarian web real-time):\n\n"
            f"{news_text}\n\nBerdasarkan informasi di atas, analisis dampaknya terhadap USD dan pasar forex. "
            f"Pertanyaan user: {message}"
        )


    if is_regime_question(message):
        symbol = extract_pair(message) or "XAU/USD"
        await safe_reply(update, f"⏳ _Menjalankan analisis teknikal {symbol}..._")
        analysis = full_analysis(symbol)
        if "error" in analysis:
            return message
        ind, conf, sr = analysis["indicators"], analysis["confidence"], analysis["sr"]
        return (
            f"Data analisis teknikal realtime {symbol} (1h):\n"
            f"Regime: {analysis['regime']}\n"
            f"Bias: {conf['bias_direction']} | Technical Score: {conf['confidence']}% (BUKAN probabilitas) | Edge: {conf['edge']}/100\n"
            f"ADX: {ind['adx']:.2f} | RSI: {ind['rsi']:.2f} | ATR: {ind['atr']:.5f}\n"
            f"EMA: {ind['ema20']:.5f}/{ind['ema50']:.5f}/{ind['ema200']:.5f}\n"
            f"MACD: {ind['macd']:.5f} vs Signal: {ind['macd_signal']:.5f}\n"
            f"Support: {sr['supports']} | Resistance: {sr['resistances']}\n"
            f"Trap: {analysis['trap']}\n\nPertanyaan user: {message}"
        )


    await safe_reply(update, "⏳ _Menganalisis..._")
    return message


# ── Handlers ─────────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"{header('AI Agent')}"
        "Selamat datang, Trader! 👋\n\n"
        "Saya siap membantu analisis forex berbasis data ekonomi & teknikal real-time.\n\n"
        "*📌 Kalender & Berita:*\n"
        "├ /calendar — Event USD hari ini\n"
        "├ /high — High Impact minggu ini\n"
        "├ /bias [event] — Analisis bias fundamental\n"
        "├ /eventpreview [event opsional] — Kumpulkan berita + prediksi hasil "
        "NFP/CPI/FOMC/PPI terdekat\n"
        "└ /news [query] — Cari berita forex terkini\n\n"
        "*🌍 Insight Makro & Bias:*\n"
        "├ /macro — Insight makro/mikro + bias Gold, USD & forex saat ini\n"
        "├ /macroon — Langganan briefing otomatis 3x/hari\n"
        "└ /macrooff — Berhenti langganan\n\n"
        "*💹 Data & Analisis Teknikal:*\n"
        "├ /harga [pair] — Harga terkini, cth: `/harga EURUSD`\n"
        "├ /chart [pair] [tf] — Analisis teknikal singkat, cth: `/chart XAUUSD 4h`\n"
        "├ /regime [pair] [tf] — Analisis regime lengkap\n"
        "├ /confidence [pair] [tf] — Breakdown 10 faktor scoring\n"
        "└ /sr [pair] [tf] — Level support & resistance\n\n"
        "*🔔 Watchlist & Alert Otomatis:*\n"
        "├ /watch [pair] — Tambah pair ke watchlist, cth: `/watch XAUUSD`\n"
        "├ /unwatch [pair] — Hapus pair dari watchlist\n"
        "├ /watchlist — Lihat watchlist kamu\n"
        "├ /alert [pair] [op] [harga] — Custom price alert, cth: `/alert XAUUSD > 4100`\n"
        "├ /alerts — Lihat alert aktif | /unalert [id] — Hapus alert\n"
        "├ /sessionon — Notif setiap sesi trading dibuka\n"
        "└ /sessionoff — Berhenti notif sesi\n\n"
        "*🧭 Sinyal & Analisis Lanjutan:*\n"
        "├ /signal — Satu keputusan entry XAUUSD dari M15 + H4 + fundamental\n"
        "├ /confluence [pair] — Cek keselarasan bias di 4 timeframe\n"
        "├ /pattern [pair] [tf] — Deteksi candlestick pattern + cek lokasi S/R\n"
        "├ /backtest [pair] [tf] [n_candle] — Backtest auto-signal (win rate, expectancy, dst)\n"
        "├ /lot [balance] [risk%] [SL pips] [pair] — Hitung position size\n"
        "└ /correlation [pair1] [pair2] ... — Matriks korelasi antar pair\n"
        "_(Auto-signal: watchlist kamu otomatis di-scan, alert dikirim kalau ada setup kuat)_\n"
        "_(High Impact NFP/CPI/FOMC/PPI: alert otomatis + berita terkait + prediksi hasil)_\n\n"
        "*🤖 AI & Lainnya:*\n"
        "├ /ai [pertanyaan] — Tanya AI (wajib di grup)\n"
        "├ /reset — Reset percakapan\n"
        "├ /debrief — Cek update harian sekarang juga (manual, tanpa subscribe)\n"
        "├ /debriefon — Rangkuman pasar harian otomatis\n"
        "└ /debriefoff — Berhenti debrief harian\n\n"
        "*🖼 Analisis Chart Gambar:*\n"
        "Kirim screenshot chart + caption pertanyaan.\n\n"
        "💬 _Chat pribadi: langsung tanya atau kirim chart.\n"
        "👥 Di grup: gunakan `/ai` atau kirim chart dengan caption._"
        f"{FOOTER}"
    )
    await safe_reply(update, text)


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, "⏳ _Mengambil data kalender..._")
    events = get_calendar(filter_country="USD")
    if not events:
        note = "⚠️ Gagal mengambil data (coba lagi nanti)." if events is None else "Tidak ada event USD minggu ini."
        await safe_reply(update, f"{header('Kalender USD')}{note}{FOOTER}")
        return

    today = datetime.now().strftime("%m-%d-%Y")
    today_events = [e for e in events if today in e["date"]]

    if not today_events:
        text = f"{header('Kalender USD')}📅 Tidak ada event USD hari ini.{FOOTER}"
    else:
        lines = [f"{header('Event USD Hari Ini')}"]
        for i, e in enumerate(today_events):
            if e["impact"] == "High":
                impact_badge = "🔴 *HIGH*"
            elif e["impact"] == "Medium":
                impact_badge = "🟡 *MED*"
            else:
                impact_badge = "⚪ *LOW*"
            connector = "└" if i == len(today_events) - 1 else "├"
            actual = e.get("actual", "N/A")
            actual_line = f"  |  ✅ Actual: `{actual}`" if actual and actual != "N/A" else ""
            lines.append(
                f"{connector} {impact_badge} — *{e['title']}*\n"
                f"    🕐 {e['time']}  |  📈 Forecast: `{e['forecast']}`  |  📉 Prev: `{e['previous']}`{actual_line}\n"
            )
        lines.append(FOOTER)
        text = "\n".join(lines)

    await safe_reply(update, text)


async def high_impact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, "⏳ _Mengambil event High Impact..._")
    events = get_calendar(filter_country="USD", filter_impact="High")
    if not events:
        note = "⚠️ Gagal mengambil data (coba lagi nanti)." if events is None else "✅ Tidak ada event High Impact minggu ini."
        text = f"{header('High Impact USD')}{note}{FOOTER}"
    else:
        lines = [f"{header('🔴 High Impact USD — Minggu Ini')}"]
        for i, e in enumerate(events):
            connector = "└" if i == len(events) - 1 else "├"
            actual = e.get("actual", "N/A")
            actual_line = f"  📊 Actual: `{actual}`" if actual and actual != "N/A" else ""
            lines.append(
                f"{connector} 🔴 *{e['title']}*\n"
                f"    📅 {e['date']}  🕐 {e['time']}\n"
                f"    📈 Forecast: `{e['forecast']}`  📉 Prev: `{e['previous']}`{actual_line}\n"
            )
        lines.append(FOOTER)
        text = "\n".join(lines)

    await safe_reply(update, text)


async def bias_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    event_name = " ".join(context.args) if context.args else None

    if not event_name:
        events = get_calendar(filter_country="USD", filter_impact="High")
        if not events:
            note = "⚠️ Gagal mengambil data kalender (coba lagi nanti)." if events is None else "Tidak ada event High Impact minggu ini."
            await safe_reply(update, f"{header('Analisis Bias')}{note}{FOOTER}")
            return
        e = events[0]
        prompt = (
            f"Berikan analisis bias trading untuk event: {e['title']} | "
            f"Waktu: {e['date']} {e['time']} | Forecast: {e['forecast']} | Previous: {e['previous']}"
        )
    else:
        prompt = f"Berikan analisis bias trading untuk event ekonomi USD: {event_name}"

    await safe_reply(update, "⏳ _Menganalisis dengan AI..._")
    result = analyze_with_groq(prompt, get_history(user_id))
    add_to_history(user_id, "user", prompt)
    add_to_history(user_id, "assistant", result)

    await safe_reply(update, f"{header('Analisis Bias')}{result}{FOOTER}")


async def event_preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kumpulkan berita + bikin analisa prediksi untuk event NFP/CPI/FOMC/PPI
    terdekat (atau nama event yang diketik manual)."""
    query = " ".join(context.args) if context.args else None

    if query:
        event = {"title": query, "date": "-", "time": "-", "forecast": "N/A", "previous": "N/A"}
    else:
        events = get_calendar(filter_country="USD", filter_impact="High")
        if events is None:
            await safe_reply(
                update,
                f"{header('Event Preview & Prediction')}"
                "⚠️ Gagal mengambil data kalender (coba lagi nanti)."
                f"{FOOTER}",
            )
            return
        major_events = [e for e in events if core.is_major_event(e["title"])]
        if not major_events:
            await safe_reply(
                update,
                f"{header('Event Preview & Prediction')}"
                "Tidak ada event NFP/CPI/FOMC/PPI di kalender minggu ini.\n"
                "Bisa juga cari manual: `/eventpreview [nama event]`"
                f"{FOOTER}",
            )
            return
        event = major_events[0]

    await safe_reply(update, f"🔍 _Mengumpulkan berita & menyusun prediksi untuk {event['title']}..._")
    result = core.generate_event_prediction(event)

    title_text = f"Event Preview: {event['title']}"
    await safe_reply(
        update,
        f"{header(title_text)}"
        f"📅 {event['date']} {event['time']} | Forecast: `{event['forecast']}` | Previous: `{event['previous']}`\n\n"
        f"{result}"
        f"{FOOTER}",
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    core.conversation_histories[("telegram", user_id)] = []
    await safe_reply(update, "✅ *History percakapan direset.*\nKita mulai dari awal lagi!")


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    question = " ".join(context.args) if context.args else None

    if not question:
        await safe_reply(
            update,
            f"{header('Cara Pakai /ai')}"
            "Ketik pertanyaanmu setelah command, contoh:\n\n"
            "`/ai Bagaimana dampak NFP terhadap EURUSD?`"
            f"{FOOTER}",
        )
        return

    prompt = await build_prompt_with_context(update, question)
    result = core.analyze_with_groq_agentic(
        prompt, user_id, update.effective_chat.id, platform="telegram",
        conversation_history=get_history(user_id),
    )
    add_to_history(user_id, "user", question)
    add_to_history(user_id, "assistant", result)

    await safe_reply(update, f"{header('Analisis AI')}{result}{FOOTER}")


async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Satu keputusan entry XAUUSD berbasis M15 + H4 + fundamental."""
    await safe_reply(update, "⏳ _Menganalisis XAUUSD dari chart M15/H4 dan data fundamental..._")
    result = core.generate_xau_entry_signal()
    if result.get("error"):
        await safe_reply(update, f"❌ {result['error']}")
        return
    await safe_reply(update, result["text"], parse_mode=None)


async def harga_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Harga Realtime')}"
            "Contoh penggunaan:\n"
            "`/harga EURUSD`\n"
            "`/harga XAUUSD`"
            f"{FOOTER}",
        )
        return

    raw = context.args[0]
    symbol = format_symbol(raw)
    await safe_reply(update, "⏳ _Mengambil harga..._")

    result = get_forex_price(symbol)
    if "error" in result:
        await safe_reply(update, f"{header('Harga Realtime')}❌ *Error:* {result['error']}{FOOTER}")
        return

    now = datetime.now().strftime("%d %b %Y, %H:%M:%S")
    text = (
        f"{header('Harga Realtime')}"
        f"💱 *{result['symbol']}*\n\n"
        f"💰 Harga: `{result['price']}`\n"
        f"🕐 Update: {now}"
        f"{FOOTER}"
    )
    await safe_reply(update, text)


def _format_structure_summary(structure: dict) -> str:
    """Ringkasan singkat market structure (trend + CHoCH) buat ditampilkan
    di teks & dikasih ke prompt AI."""
    swings = structure.get("swings", [])
    labels = [s["label"] for s in swings if s["label"]]
    trend = structure.get("trend")
    choch = structure.get("choch")

    if not labels and not choch:
        return "belum ada swing terkonfirmasi (data kurang/terlalu sideways)"

    recent_labels = "-".join(labels[-4:]) if labels else "-"
    trend_text = {"up": "Uptrend (HH/HL)", "down": "Downtrend (LH/LL)"}.get(trend, "Netral")

    parts = [f"{trend_text}, urutan terakhir: {recent_labels}"]
    if choch:
        parts.append(
            f"⚠️ CHoCH {choch['direction'].upper()} terdeteksi (break level {choch['broken_level']:.5f}) "
            f"— potensi reversal, struktur lama sudah tidak valid"
        )
    return " | ".join(parts)


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Analisis Teknikal')}"
            "Contoh penggunaan:\n"
            "`/chart EURUSD 1h`\n"
            "`/chart XAUUSD 4h`\n\n"
            "Timeframe tersedia: `1min 5min 15min 1h 4h 1day`"
            f"{FOOTER}",
        )
        return

    raw_symbol = context.args[0]
    interval = context.args[1] if len(context.args) > 1 else "1h"
    symbol = format_symbol(raw_symbol)

    await safe_reply(update, f"⏳ _Mengambil data {symbol} {interval}..._")

    df, err = _ohlcv_or_error(symbol, interval)
    if err:
        await safe_reply(update, f"{header('Analisis Teknikal')}❌ *Error:* {err}{FOOTER}")
        return

    indicators = calculate_indicators(df)
    conf = calculate_confidence(indicators)
    regime = get_regime(indicators)
    sr = get_support_resistance(df)
    structure = core.detect_market_structure(df)
    structure_summary = _format_structure_summary(structure)

    prompt = (
        f"Data teknikal {symbol} {interval}: Regime {regime}, Bias {conf['bias_direction']} "
        f"(confidence {conf['confidence']}%), RSI {indicators['rsi']:.2f}, ADX {indicators['adx']:.2f}, "
        f"Close {indicators['close']}, EMA20 {indicators['ema20']:.5f}, EMA50 {indicators['ema50']:.5f}.\n"
        f"Market structure: {structure_summary}\n\n"
        f"Berikan analisis teknikal SINGKAT (maks 5 kalimat) meliputi tren, bias, struktur "
        f"(HH/HL/LH/LL/CHoCH kalau ada), dan satu skenario entry konkret (entry zone, SL, TP)."
    )

    await safe_reply(update, "🧠 _Menganalisis dengan AI..._")
    analysis = analyze_with_groq(prompt)

    text = (
        f"{header(f'Analisis {symbol} {interval}')}"
        f"💰 Harga: `{indicators['close']}`  |  🏛 Regime: `{regime}`\n"
        f"📐 Struktur: {structure_summary}\n\n"
        f"{analysis}"
        f"{FOOTER}"
    )

    try:
        chart_bytes = core.generate_chart_image(df, symbol, interval, sr, market_structure=structure)
        await update.message.reply_photo(photo=io.BytesIO(chart_bytes))
    except Exception as ex:
        print(f"Gagal generate/kirim chart image: {ex}")
        # Gambar gagal dibuat/dikirim bukan alasan buat gagalin seluruh command —
        # tetap lanjut kirim analisa teks di bawah.

    await safe_reply(update, text)


async def regime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Regime')}"
            "Contoh penggunaan:\n"
            "`/regime EURUSD 1h`\n"
            "`/regime XAUUSD 4h`"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    interval = context.args[1] if len(context.args) > 1 else "1h"

    await safe_reply(update, f"⏳ _Menganalisis regime {symbol} {interval}..._")

    df, err = _ohlcv_or_error(symbol, interval)
    if err:
        await safe_reply(update, f"{header('Regime')}❌ *Error:* {err}{FOOTER}")
        return

    indicators = calculate_indicators(df)
    conf = calculate_confidence(indicators)
    regime = get_regime(indicators)
    sr = get_support_resistance(df)
    trap = detect_trap(df, indicators)

    prompt = (
        f"Analisis teknikal lengkap {symbol} {interval}:\n"
        f"Regime: {regime}\n"
        f"Bias: {conf['bias_direction']} | Technical Score: {conf['confidence']}% (BUKAN probabilitas) | Edge: {conf['edge']}/100\n"
        f"Bullish factors: {conf['bullish_score']}/{conf['max_score']} | Bearish factors: {conf['bearish_score']}/{conf['max_score']}\n"
        f"ADX: {indicators['adx']:.2f} | RSI: {indicators['rsi']:.2f} | ATR: {indicators['atr']:.5f}\n"
        f"EMA: {indicators['ema20']:.5f}/{indicators['ema50']:.5f}/{indicators['ema200']:.5f}\n"
        f"MACD: {indicators['macd']:.5f} vs Signal: {indicators['macd_signal']:.5f}\n"
        f"Support: {sr['supports']}\n"
        f"Resistance: {sr['resistances']}\n"
        f"Trap: {trap}\n\n"
        f"Buat analisis mengikuti format berikut (isi bagian dalam kurung siku dengan analisis konkret, "
        f"hapus tanda kurung siku, JANGAN gunakan tanda bintang):\n"
        f"🏛 REGIME: {regime} — bias {conf['bias_direction']} confidence {conf['confidence']}% Edge {conf['edge']}/100\n\n"
        f"📊 STRUKTUR TEKNIKAL:\n"
        f"- Trend: [analisis EMA stack]\n"
        f"- Momentum: [analisis MACD + RSI]\n"
        f"- Volatilitas: [analisis ATR]\n\n"
        f"🎯 LEVEL KUNCI:\n"
        f"- Support: [3 level]\n"
        f"- Resistance: [3 level]\n\n"
        f"⚡ BIAS: [Bullish/Bearish/Netral]\n"
        f"📝 Alasan: [2-3 kalimat konkret]\n"
        f"⚠️ Risk & Trap: [potensi trap + skenario batal]\n"
        f"👁 Yang Dipantau: [level harga kunci]"
    )

    result = analyze_with_groq(prompt)
    text = f"{header(f'Regime {symbol} {interval}')}{result}{FOOTER}"
    await safe_reply(update, text)


async def confidence_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Confidence')}"
            "Contoh penggunaan:\n"
            "`/confidence EURUSD 1h`\n"
            "`/confidence XAUUSD 4h`"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    interval = context.args[1] if len(context.args) > 1 else "1h"

    await safe_reply(update, f"⏳ _Menghitung confidence {symbol} {interval}..._")

    df, err = _ohlcv_or_error(symbol, interval)
    if err:
        await safe_reply(update, f"{header('Confidence')}❌ *Error:* {err}{FOOTER}")
        return

    indicators = calculate_indicators(df)
    conf = calculate_confidence(indicators)

    lines = [header(f"Technical Score {symbol} {interval}")]
    lines.append(f"Technical Score: *{conf['confidence']}%*  |  Edge: *{conf['edge']}/100*")
    lines.append(f"Bullish ✅: *{conf['bullish_score']}/{conf['max_score']}*  |  Bearish ❌: *{conf['bearish_score']}/{conf['max_score']}*")
    lines.append(f"_⚠️ Ini skor kesearahan indikator teknikal, BUKAN probabilitas kemenangan trade._\n")
    for f in conf["detail_factors"]:
        emoji = "✅" if f["direction"] == "bullish" else "❌" if f["direction"] == "bearish" else "⚪"
        lines.append(f"{emoji} *{f['name']}*: {f['note']}")
    lines.append(FOOTER)

    await safe_reply(update, "\n".join(lines))


async def sr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Support & Resistance')}"
            "Contoh penggunaan:\n"
            "`/sr EURUSD 1h`\n"
            "`/sr XAUUSD 4h`"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    interval = context.args[1] if len(context.args) > 1 else "1h"

    await safe_reply(update, f"⏳ _Mencari level S/R {symbol} {interval}..._")

    df, err = _ohlcv_or_error(symbol, interval)
    if err:
        await safe_reply(update, f"{header('Support & Resistance')}❌ *Error:* {err}{FOOTER}")
        return

    sr = get_support_resistance(df)
    current = sr["current_price"]

    def pip_size():
        if "JPY" in symbol:
            return 0.01
        if "XAU" in symbol:
            return 0.1
        return 0.0001

    pip = pip_size()

    lines = [header(f"Support & Resistance {symbol} {interval}")]
    lines.append(f"💰 Harga sekarang: `{current}`\n")
    lines.append("*🔺 Resistance:*")
    if sr["resistance_zones"]:
        for i, z in enumerate(sr["resistance_zones"], 1):
            r = round(z["level"], 5)
            star = "⭐" * min(z["touches"], 3)
            lines.append(f"R{i}: `{r}`  ({abs(r - current) / pip:.1f} pips)  {star} _{z['touches']}x touch_")
    else:
        lines.append("_Tidak ditemukan_")
    lines.append("\n*🔻 Support:*")
    if sr["support_zones"]:
        for i, z in enumerate(sr["support_zones"], 1):
            s = round(z["level"], 5)
            star = "⭐" * min(z["touches"], 3)
            lines.append(f"S{i}: `{s}`  ({abs(current - s) / pip:.1f} pips)  {star} _{z['touches']}x touch_")
    else:
        lines.append("_Tidak ditemukan_")
    lines.append("\n_⭐ = jumlah kali price pernah menyentuh level ini (semakin banyak = semakin signifikan)_")
    lines.append(FOOTER)

    await safe_reply(update, "\n".join(lines))


async def track_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catat user_id + chat_id setiap kali ada interaksi, untuk broadcast alert."""
    if update.effective_user and update.effective_chat:
        core.register_user(update.effective_user.id, update.effective_chat.id, platform="telegram")


async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Watchlist')}"
            "Contoh penggunaan:\n"
            "`/watch XAUUSD`\n"
            "`/watch EURUSD`"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    is_group = update.effective_chat.type in ("group", "supergroup")

    # Selalu simpan private chat (user_id == private chat_id di Telegram)
    core.add_watch(user_id, user_id, symbol, platform="telegram")
    # Kalau dipanggil dari group, simpan juga group chat_id agar alert masuk ke grup
    if is_group:
        core.add_watch(user_id, chat_id, symbol, platform="telegram")

    dest = "pesan pribadi + grup ini" if is_group else "pesan pribadi kamu"
    await safe_reply(
        update,
        f"{header('Watchlist')}✅ *{symbol}* ditambahkan.\n"
        f"Alert trap otomatis (1H) akan dikirim ke {dest}.{FOOTER}",
    )


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Watchlist')}"
            "Contoh penggunaan:\n"
            "`/unwatch XAUUSD`"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    user_id = update.effective_user.id

    core.remove_watch(user_id, symbol, platform="telegram")

    await safe_reply(update, f"{header('Watchlist')}🗑 *{symbol}* dihapus dari watchlist kamu.{FOOTER}")


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    rows = core.list_watch(user_id, platform="telegram")

    if not rows:
        await safe_reply(
            update,
            f"{header('Watchlist')}Watchlist kamu masih kosong.\nTambah dengan `/watch [pair]`{FOOTER}",
        )
        return

    lines = [header("Watchlist Kamu")]
    for r in rows:
        lines.append(f"• {r['symbol']}")
    lines.append(FOOTER)
    await safe_reply(update, "\n".join(lines))


# ── Alerts ───────────────────────────────────────────────────
# Catatan: broadcast alert (high-impact calendar, trap, macro briefing)
# sekarang dijadwalkan TERPUSAT di scheduler.py supaya user Telegram
# maupun Discord sama-sama kebagian, bukan per-bot lagi.


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args) if context.args else "forex market news today"
    await safe_reply(update, "🔍 _Mencari berita terbaru..._")
    result = search_news(query)
    await safe_reply(update, f"{header('Berita Forex')}{result}{FOOTER}")


async def macro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """On-demand: kirim laporan insight makro/mikro + bias saat ini."""
    await safe_reply(update, "⏳ _Mengambil berita makro & menyusun analisis..._")
    result = generate_macro_briefing()
    await safe_reply(update, f"{header('Insight Makro & Bias')}{result}{FOOTER}")


async def macro_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    core.add_macro_sub(user_id, chat_id, platform="telegram")
    await safe_reply(
        update,
        f"{header('Macro Briefing Subscription')}"
        "✅ Kamu akan menerima insight makro & bias otomatis 3x/hari "
        "(sesi Asia 07:00, London 14:00, New York 19:30 WIB).\n\n"
        "Ketik `/macrooff` untuk berhenti."
        f"{FOOTER}",
    )


async def macro_unsub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    core.remove_macro_sub(user_id, platform="telegram")
    await safe_reply(update, "🔕 Berhenti berlangganan macro briefing otomatis.")


# ── Kategori 1: Sinyal & Analisis ───────────────────────────────


async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Backtest Auto-Signal')}"
            "Contoh penggunaan:\n"
            "`/backtest XAUUSD`\n"
            "`/backtest EURUSD 1h 500`\n\n"
            "Format: `/backtest [pair] [timeframe] [jumlah_candle]`\n"
            "Default: timeframe 1h, 500 candle terakhir.\n\n"
            "⚠️ Ini backtest sederhana IN-SAMPLE (bukan out-of-sample/walk-forward), "
            "cuma langkah pertama buat cek apakah rule auto-signal sekarang punya "
            "edge dasar atau enggak. Butuh waktu ~10-30 detik."
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    interval = context.args[1] if len(context.args) > 1 else "1h"
    try:
        outputsize = int(context.args[2]) if len(context.args) > 2 else 500
    except ValueError:
        await safe_reply(update, f"{header('Backtest Auto-Signal')}❌ Jumlah candle harus angka.{FOOTER}")
        return
    outputsize = max(150, min(outputsize, 2000))  # jaga-jaga biar gak keterusan lama/hitung data yg kurang

    await safe_reply(update, f"⏳ _Menjalankan backtest {symbol} {interval} ({outputsize} candle)... bisa makan waktu 10-30 detik._")

    # Backtest itu CPU-bound (loop ratusan-ribuan iterasi ngitung indikator),
    # dijalankan di thread terpisah supaya gak nge-block event loop bot buat
    # user lain selama proses berjalan.
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, core.backtest_scan_signal, symbol, interval, outputsize)

    if "error" in result:
        await safe_reply(update, f"{header('Backtest Auto-Signal')}❌ {result['error']}{FOOTER}")
        return

    if result.get("total_signals", 0) == 0:
        await safe_reply(
            update,
            f"{header(f'Backtest {symbol} {interval}')}"
            f"{result.get('note', 'Tidak ada sinyal.')}\n\n"
            f"_Ini BUKAN error — artinya kriteria should_alert (trending + confidence tinggi + "
            f"RR memadai + tanpa trap confirmed) memang jarang/tidak pernah terpenuhi di "
            f"{result.get('candles_used', outputsize)} candle terakhir._"
            f"{FOOTER}",
        )
        return

    pf_display = result["profit_factor"] if result["profit_factor"] != "inf" else "∞ (tidak ada loss)"
    verdict = (
        "🔴 Expectancy negatif — rule ini secara statistik MERUGI di data ini, jangan dipakai live apa adanya."
        if result["expectancy_r"] < 0
        else "🟡 Expectancy positif tapi sample kecil — belum cukup buat disimpulkan, butuh lebih banyak data/sampel."
        if result["total_signals"] < 30
        else "🟢 Expectancy positif dengan sample cukup — tanda awal yang baik, TAPI ini masih in-sample, belum out-of-sample test."
    )

    text = (
        f"{header(f'Backtest {symbol} {interval}')}"
        f"📊 Data: {result['candles_used']} candle | Window scan: 100 candle (samain live)\n\n"
        f"Total sinyal: *{result['total_signals']}* (Win: {result['wins']} | Loss: {result['losses']})\n"
        f"Win rate: *{result['win_rate']}%*\n"
        f"Profit factor: *{pf_display}*\n"
        f"Expectancy: *{result['expectancy_r']} R/trade*\n"
        f"Max drawdown: *{result['max_drawdown_r']} R*\n"
        f"Avg RR target: {result['avg_rr_target']}\n\n"
        f"{verdict}\n\n"
        f"_Catatan: RR pakai target saat sinyal (bukan realized), belum ada slippage/spread, "
        f"ini backtest in-sample sederhana — bukan jaminan performa live._"
        f"{FOOTER}"
    )
    await safe_reply(update, text)


async def pattern_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Candlestick Pattern')}"
            "Contoh penggunaan:\n"
            "`/pattern XAUUSD`\n"
            "`/pattern EURUSD 4h`"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    interval = context.args[1] if len(context.args) > 1 else "1h"
    await safe_reply(update, f"⏳ _Mengecek pola candlestick {symbol} {interval}..._")

    df, err = _ohlcv_or_error(symbol, interval)
    if err:
        await safe_reply(update, f"{header('Candlestick Pattern')}❌ *Error:* {err}{FOOTER}")
        return

    indicators = calculate_indicators(df)
    conf = calculate_confidence(indicators)
    regime = get_regime(indicators)
    sr = get_support_resistance(df)
    close = indicators["close"]

    pattern_info = core.detect_candlestick_pattern(df)

    if not pattern_info["pattern"]:
        text = (
            f"{header(f'Candlestick Pattern {symbol} {interval}')}"
            f"Regime: {regime} | Technical Score: {conf['confidence']}% (BUKAN probabilitas)\n\n"
            f"⚠️ Tidak ada pola candlestick jelas (Engulfing/Hammer/Shooting Star/Doji) "
            f"di candle terakhir yang closed."
            f"{FOOTER}"
        )
        await safe_reply(update, text)
        return

    lines = [header(f"Candlestick Pattern {symbol} {interval}")]
    lines.append(f"Regime: {regime} | Technical Score: {conf['confidence']}% (BUKAN probabilitas)\n")
    lines.append(f"🕯 Pola terdeteksi: *{pattern_info['pattern']}*")

    if pattern_info["direction"]:
        matching_zone = core.find_matching_sr_zone(close, sr, pattern_info["direction"])
        if matching_zone:
            side = "support" if pattern_info["direction"] == "bullish" else "resistance"
            lines.append(
                f"✅ Ada di level {side} kuat: `{round(matching_zone['level'], 5)}` "
                f"({matching_zone['touches']}x touch) — konfirmasi kuat"
            )
        else:
            lines.append("ℹ️ Tidak di lokasi S/R kuat yang searah — konfirmasi lemah")

        if pattern_info["direction"] == conf["bias_direction"].lower():
            lines.append(f"✅ Searah dengan bias Technical Score ({conf['bias_direction']})")
        else:
            lines.append(f"⚠️ Berlawanan dengan bias Technical Score ({conf['bias_direction']}) — hati-hati")
    else:
        lines.append("_Doji = indecision, tidak directional._")

    lines.append(FOOTER)
    await safe_reply(update, "\n".join(lines))


async def confluence_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(
            update,
            f"{header('Multi-Timeframe Confluence')}"
            "Contoh penggunaan:\n"
            "`/confluence XAUUSD`\n"
            "`/confluence EURUSD`"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    await safe_reply(update, f"⏳ _Mengecek confluence {symbol} di 4 timeframe..._")

    result = get_confluence(symbol)
    if "error" in result:
        await safe_reply(update, f"{header('Multi-Timeframe Confluence')}❌ *Error:* {result['error']}{FOOTER}")
        return

    lines = [header(f"Confluence {symbol}")]
    lines.append(f"🧭 *Overall:* {result['overall']}\n")
    for tf in result["per_tf"]:
        if "error" in tf:
            lines.append(f"• `{tf['interval']}`: ❌ {tf['error']}")
            continue
        emoji = "🟢" if tf["bias"] == "Bullish" else "🔴" if tf["bias"] == "Bearish" else "⚪"
        htf_tag = " 👑HTF" if tf["interval"] in ("1day", "4h") else ""
        lines.append(f"• `{tf['interval']}`{htf_tag}: {emoji} {tf['bias']} ({tf['confidence']}%) — {tf['regime']}")
    lines.append(f"\n_👑HTF (1day/4h) = otoritas directional bias lebih tinggi daripada 1h/15min._")
    lines.append(FOOTER)
    await safe_reply(update, "\n".join(lines))


async def lot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await safe_reply(
            update,
            f"{header('Position Size Calculator')}"
            "Contoh penggunaan:\n"
            "`/lot [balance] [risk%] [SL_pips] [pair(opsional, default XAUUSD)]`\n"
            "`/lot 1000 1 20 XAUUSD`"
            f"{FOOTER}",
        )
        return

    try:
        balance = float(context.args[0])
        risk_percent = float(context.args[1])
        sl_pips = float(context.args[2])
    except ValueError:
        await safe_reply(update, f"{header('Position Size Calculator')}❌ Balance/risk%/SL pips harus angka.{FOOTER}")
        return

    symbol = format_symbol(context.args[3]) if len(context.args) > 3 else "XAU/USD"

    result = calculate_lot_size(balance, risk_percent, sl_pips, symbol)
    if "error" in result:
        await safe_reply(update, f"{header('Position Size Calculator')}❌ {result['error']}{FOOTER}")
        return

    text = (
        f"{header('Position Size Calculator')}"
        f"💱 Pair: *{result['symbol']}*\n"
        f"💰 Balance: `{result['balance']}` | Risk: `{result['risk_percent']}%` (`{result['risk_amount']}`)\n"
        f"📏 SL: `{result['sl_pips']} pips` | Est. nilai pip/lot: `{result['pip_value_per_lot']}`\n\n"
        f"📐 *Lot Size: {result['lot_size']}*\n\n"
        f"_Catatan: estimasi umum, cek spesifikasi kontrak broker kamu untuk angka pasti._"
        f"{FOOTER}"
    )
    await safe_reply(update, text)


async def correlation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    default_symbols = ["XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY"]
    symbols = [format_symbol(s) for s in context.args] if context.args else default_symbols

    if len(symbols) < 2:
        await safe_reply(update, f"{header('Correlation Matrix')}Minimal 2 pair, contoh:\n`/correlation XAUUSD EURUSD GBPUSD`{FOOTER}")
        return

    await safe_reply(update, f"⏳ _Menghitung korelasi {', '.join(symbols)}..._")

    result = get_correlation_matrix(symbols)
    if "error" in result:
        detail = "\n".join(f"• {sym}: {err}" for sym, err in result.get("errors", {}).items())
        await safe_reply(
            update,
            f"{header('Correlation Matrix')}❌ {result['error']}\n\n{detail}{FOOTER}",
        )
        return

    syms = result["symbols"]
    lines = [header("Correlation Matrix (1h, berbasis return)")]
    lines.append("```")
    header_row = "        " + "  ".join(f"{s.split('/')[0]:>6}" for s in syms)
    lines.append(header_row)
    for s1 in syms:
        row = f"{s1.split('/')[0]:>6}  " + "  ".join(f"{result['matrix'][s1][s2]:>6.2f}" for s2 in syms)
        lines.append(row)
    lines.append("```")
    if result["errors"]:
        lines.append(f"⚠️ Gagal ambil data: {', '.join(result['errors'].keys())}")
    lines.append(FOOTER)
    await safe_reply(update, "\n".join(lines))


# ── Kategori 3: Alert Otomatis ───────────────────────────────────


async def alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await safe_reply(
            update,
            f"{header('Custom Price Alert')}"
            "Contoh penggunaan:\n"
            "`/alert XAUUSD > 4100`\n"
            "`/alert EURUSD < 1.05`\n\n"
            f"Operator yang tersedia: {', '.join(sorted(VALID_OPERATORS))}"
            f"{FOOTER}",
        )
        return

    symbol = format_symbol(context.args[0])
    operator = context.args[1]
    try:
        target_price = float(context.args[2])
    except ValueError:
        await safe_reply(update, f"{header('Custom Price Alert')}❌ Target price harus angka.{FOOTER}")
        return

    result = core.add_price_alert(
        update.effective_user.id, update.effective_chat.id, symbol, operator, target_price, platform="telegram"
    )
    if "error" in result:
        await safe_reply(update, f"{header('Custom Price Alert')}❌ {result['error']}{FOOTER}")
        return

    await safe_reply(
        update,
        f"{header('Custom Price Alert')}"
        f"✅ Alert #{result['id']} dibuat: *{symbol} {operator} {target_price}*\n"
        f"Kamu akan diberi tahu sekali saat target tercapai.\n"
        f"Lihat semua alert: `/alerts` | Hapus: `/unalert [id]`"
        f"{FOOTER}",
    )


async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = core.list_price_alerts(update.effective_user.id, platform="telegram")
    if not rows:
        await safe_reply(update, f"{header('Alert Kamu')}Belum ada alert aktif.\nBuat dengan `/alert [pair] [operator] [harga]`{FOOTER}")
        return

    lines = [header("Alert Kamu")]
    for r in rows:
        lines.append(f"#{r['id']} — {r['symbol']} {r['operator']} {r['target_price']}")
    lines.append(FOOTER)
    await safe_reply(update, "\n".join(lines))


async def unalert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update, f"{header('Hapus Alert')}Contoh: `/unalert 3`{FOOTER}")
        return
    try:
        alert_id = int(context.args[0])
    except ValueError:
        await safe_reply(update, f"{header('Hapus Alert')}❌ ID alert harus angka.{FOOTER}")
        return

    deleted = core.remove_price_alert(update.effective_user.id, alert_id, platform="telegram")
    if deleted:
        await safe_reply(update, f"{header('Hapus Alert')}🗑 Alert #{alert_id} dihapus.{FOOTER}")
    else:
        await safe_reply(update, f"{header('Hapus Alert')}❌ Alert #{alert_id} tidak ditemukan.{FOOTER}")


async def session_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    core.add_session_sub(update.effective_user.id, update.effective_chat.id, platform="telegram")
    await safe_reply(
        update,
        f"{header('Session Reminder')}"
        "✅ Kamu akan dapat notifikasi setiap sesi (Asia/London/New York) baru dibuka.\n\n"
        "Ketik `/sessionoff` untuk berhenti."
        f"{FOOTER}",
    )


async def session_unsub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    core.remove_session_sub(update.effective_user.id, platform="telegram")
    await safe_reply(update, "🔕 Berhenti berlangganan session reminder.")


# ── Kategori 4: AI & Interaksi ───────────────────────────────────


async def debrief_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """On-demand: kirim daily market debrief saat ini juga, tanpa perlu
    nunggu jadwal otomatis (05:00 WIB) atau subscribe dulu."""
    await safe_reply(update, "⏳ _Menyusun update harian (multi-pair + kalender hari ini)..._")
    result = generate_daily_debrief()
    await safe_reply(update, f"{header('📋 Daily Market Debrief')}{result}{FOOTER}")


async def debrief_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    core.add_debrief_sub(update.effective_user.id, update.effective_chat.id, platform="telegram")
    await safe_reply(
        update,
        f"{header('Daily Market Debrief')}"
        "✅ Kamu akan menerima rangkuman pasar harian otomatis (akhir sesi New York).\n\n"
        "Ketik `/debriefoff` untuk berhenti."
        f"{FOOTER}",
    )


async def debrief_unsub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    core.remove_debrief_sub(update.effective_user.id, platform="telegram")
    await safe_reply(update, "🔕 Berhenti berlangganan daily debrief.")


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyze chart images only when caption starts with /analize (private and group)."""
    caption_raw = update.message.caption or ""

    # Hanya proses kalau caption diawali /analize (case-insensitive)
    if not caption_raw.strip().lower().startswith("/analize"):
        return

    # Ambil teks setelah /analize sebagai konteks analisis
    user_context = caption_raw.strip()[len("/analize"):].strip() or None

    await safe_reply(update, "🔍 _Membaca chart, harap tunggu..._")

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    image_bytes = await tg_file.download_as_bytearray()

    result = analyze_image_with_groq(bytes(image_bytes), user_context)

    title = "Analisis Chart"
    if user_context:
        short = user_context[:40] + "…" if len(user_context) > 40 else user_context
        title = f"Analisis Chart — {short}"

    await safe_reply(update, f"{header(title)}{result}{FOOTER}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Only responds to free text in private chats, not in groups."""
    chat_type = update.message.chat.type
    if chat_type in ("group", "supergroup", "channel"):
        return

    user_id = update.effective_user.id
    user_message = update.message.text

    prompt = await build_prompt_with_context(update, user_message)
    result = core.analyze_with_groq_agentic(
        prompt, user_id, update.effective_chat.id, platform="telegram",
        conversation_history=get_history(user_id),
    )
    add_to_history(user_id, "user", user_message)
    add_to_history(user_id, "assistant", result)

    await safe_reply(update, f"{header('Analisis AI')}{result}{FOOTER}")


# ── Main ─────────────────────────────────────────────────────


def build_application() -> Application:
    """Bangun Telegram Application (tanpa menjalankan polling — itu dilakukan
    oleh main.py supaya bisa jalan bersamaan dengan bot Discord)."""
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # group=-1: jalan lebih dulu dari semua handler lain, tidak menghentikan propagasi,
    # supaya setiap interaksi (command apapun / chat biasa) tercatat sebagai known_user.
    app.add_handler(MessageHandler(filters.ALL, track_user), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("high", high_impact_command))
    app.add_handler(CommandHandler("bias", bias_command))
    app.add_handler(CommandHandler("eventpreview", event_preview_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("harga", harga_command))
    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("regime", regime_command))
    app.add_handler(CommandHandler("confidence", confidence_command))
    app.add_handler(CommandHandler("sr", sr_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("macro", macro_command))
    app.add_handler(CommandHandler("macroon", macro_sub_command))
    app.add_handler(CommandHandler("macrooff", macro_unsub_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("unwatch", unwatch_command))
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("confluence", confluence_command))
    app.add_handler(CommandHandler("pattern", pattern_command))
    app.add_handler(CommandHandler("backtest", backtest_command))
    app.add_handler(CommandHandler("lot", lot_command))
    app.add_handler(CommandHandler("correlation", correlation_command))
    app.add_handler(CommandHandler("alert", alert_command))
    app.add_handler(CommandHandler("alerts", alerts_command))
    app.add_handler(CommandHandler("unalert", unalert_command))
    app.add_handler(CommandHandler("sessionon", session_sub_command))
    app.add_handler(CommandHandler("sessionoff", session_unsub_command))
    app.add_handler(CommandHandler("debrief", debrief_command))
    app.add_handler(CommandHandler("debriefon", debrief_sub_command))
    app.add_handler(CommandHandler("debriefoff", debrief_unsub_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app

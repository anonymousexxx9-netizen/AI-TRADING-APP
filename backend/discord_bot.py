"""
discord_bot.py — Handler untuk sisi Discord (prefix command "!").
Semua logic berat (indikator, harga, kalender, AI, dsb) ada di core.py —
supaya user Telegram & Discord dapat pengalaman yang sama persis.
"""

import os
import io
import asyncio
from datetime import datetime

import discord
from discord.ext import commands

import core
from core import (
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
    VALID_INTERVALS,
)

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
PLATFORM = "discord"
DISCORD_MAX_LEN = 1900  # buffer di bawah limit resmi Discord (2000 karakter)


async def safe_send(destination, text: str):
    """Kirim pesan ke destination (ctx, channel, atau interaction.followup —
    semuanya punya method .send(text) dengan signature sama), otomatis
    dipecah jadi beberapa pesan kalau > DISCORD_MAX_LEN karakter.

    Sebelumnya semua ctx.send()/channel.send() kirim teks mentah tanpa cek
    panjang — Discord API menolak (400 Bad Request, 'Must be 2000 or fewer
    in length') begitu lewat, dan beberapa command sekarang (Technical Score
    breakdown, S/R dengan strength/zone, event prediction terstruktur)
    rutin menghasilkan teks lebih panjang dari itu."""
    if len(text) <= DISCORD_MAX_LEN:
        await destination.send(text)
        return

    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > DISCORD_MAX_LEN:
            if current:
                chunks.append(current)
            while len(line) > DISCORD_MAX_LEN:
                chunks.append(line[:DISCORD_MAX_LEN])
                line = line[DISCORD_MAX_LEN:]
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        if chunk.strip():
            await destination.send(chunk)

# ── Channel restriction ───────────────────────────────────────
# Kalau sudah di-set via !setchannel, bot hanya akan merespons di channel itu.
# DM (private message) selalu diproses tanpa batasan channel.
SETTINGS_KEY_CHANNEL = "discord_allowed_channel_id"


def _get_allowed_channel_id():
    val = core.get_setting(SETTINGS_KEY_CHANNEL)
    return int(val) if val else None


def header(title: str) -> str:
    """Header versi Discord (Markdown: **bold**)."""
    return f"**Bayproject.fx** — {title}\n{'─' * 24}\n"


FOOTER = "\n\n*⚠️ Not Financial Advice. DYOR.*"


def build_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guild_messages = True
    intents.dm_messages = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    # ── Log saat bot siap ────────────────────────────────────────
    @bot.event
    async def on_ready():
        ch_id = _get_allowed_channel_id()
        ch_info = f" | channel terbatas: {ch_id}" if ch_id else " | semua channel aktif (belum di-set)"
        try:
            synced = await bot.tree.sync()
            print(f"✅ Discord bot terhubung sebagai {bot.user}{ch_info} | {len(synced)} slash command tersinkron")
        except Exception as ex:
            print(f"✅ Discord bot terhubung sebagai {bot.user}{ch_info} | ⚠️ gagal sync slash command: {ex}")

    # ── Log error command ke console ─────────────────────────────
    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            print(f"[Discord debug] CommandNotFound: {ctx.message.content!r}")
            return
        # Buka CommandInvokeError untuk lihat penyebab aslinya
        original = getattr(error, "original", error)
        print(f"[Discord error] {type(error).__name__}: {error}")
        print(f"[Discord error] original: {type(original).__name__}: {original}", flush=True)

    # ── Registrasi user & channel restriction ────────────────────
    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        allowed_id = _get_allowed_channel_id()

        # Kalau bukan DM dan allowed channel sudah di-set, tolak channel lain.
        # Pengecualian: command !setchannel tetap bisa dijalankan dari mana saja.
        if not is_dm and allowed_id and message.channel.id != allowed_id:
            if message.content.strip().lower().startswith("!setchannel"):
                pass  # izinkan !setchannel dari channel manapun
            else:
                return  # abaikan pesan dari channel lain

        core.register_user(message.author.id, message.channel.id, platform=PLATFORM)
        await bot.process_commands(message)

        # Kirim chart image dengan caption diawali "!analize"
        if message.attachments and message.content.strip().lower().startswith("!analize"):
            await handle_image(message)
        # Chat bebas di DM (bukan command) → jawab pakai AI
        elif is_dm and not message.content.startswith("!"):
            await handle_message(message)

    async def build_prompt_with_context(message: discord.Message, text: str) -> str:
        detected_pair = extract_pair(text) if is_price_question(text) else None

        if detected_pair:
            await safe_send(message.channel, "⏳ *Mengambil harga terkini...*")
            price_result = get_forex_price(detected_pair)
            if "error" not in price_result:
                now = datetime.now().strftime("%d %b %Y, %H:%M:%S")
                return (
                    f"Data harga realtime saat ini: {detected_pair} = {price_result['price']} "
                    f"(update: {now}).\n\nPertanyaan user: {text}"
                )
            return text

        if is_calendar_question(text):
            await safe_send(message.channel, "⏳ *Mengambil data kalender...*")
            events = get_calendar(filter_country="USD")
            calendar_text = format_calendar_for_prompt(events)

            wants_actual = any(kw in text.lower() for kw in ("actual", "hasil", "yang telah", "yang sudah"))
            if wants_actual and events:
                await safe_send(message.channel, "🔍 *Actual kosong di feed, mencari cadangan dari web...*")
                fallback_text = get_calendar_actual_fallback(events)
                if fallback_text:
                    calendar_text += f"\n\n{fallback_text}"

            return (
                f"Berikut data kalender ekonomi USD minggu ini dari Forex Factory, termasuk nilai Actual "
                f"yang sudah dirilis kalau ada (data real, bukan dari memori kamu):\n{calendar_text}\n\n"
                f"Pertanyaan user: {text}"
            )

        if is_news_question(text):
            await safe_send(message.channel, "🔍 *Mencari berita terbaru...*")
            news_text = search_news(text)
            return (
                f"Berikut berita/pernyataan terbaru yang relevan (dari pencarian web real-time):\n\n"
                f"{news_text}\n\nBerdasarkan informasi di atas, analisis dampaknya terhadap USD dan pasar forex. "
                f"Pertanyaan user: {text}"
            )

        if is_regime_question(text):
            symbol = extract_pair(text) or "XAU/USD"
            await safe_send(message.channel, f"⏳ *Menjalankan analisis teknikal {symbol}...*")
            analysis = full_analysis(symbol)
            if "error" in analysis:
                return text
            ind, conf, sr = analysis["indicators"], analysis["confidence"], analysis["sr"]
            return (
                f"Data analisis teknikal realtime {symbol} (1h):\n"
                f"Regime: {analysis['regime']}\n"
                f"Bias: {conf['bias_direction']} | Technical Score: {conf['confidence']}% (BUKAN probabilitas) | Edge: {conf['edge']}/100\n"
                f"ADX: {ind['adx']:.2f} | RSI: {ind['rsi']:.2f} | ATR: {ind['atr']:.5f}\n"
                f"EMA: {ind['ema20']:.5f}/{ind['ema50']:.5f}/{ind['ema200']:.5f}\n"
                f"MACD: {ind['macd']:.5f} vs Signal: {ind['macd_signal']:.5f}\n"
                f"Support: {sr['supports']} | Resistance: {sr['resistances']}\n"
                f"Trap: {analysis['trap']}\n\nPertanyaan user: {text}"
            )

        await safe_send(message.channel, "⏳ *Menganalisis...*")
        return text

    # ── Channel setup ──────────────────────────────────────────
    @bot.command(name="setchannel")
    async def setchannel_cmd(ctx):
        """Tetapkan channel ini sebagai satu-satunya channel tempat bot merespons."""
        core.set_setting(SETTINGS_KEY_CHANNEL, str(ctx.channel.id))
        await safe_send(ctx, 
            f"{header('Channel Setup')}"
            f"✅ Bot sekarang hanya akan merespons di channel ini.\n"
            f"Channel ID: `{ctx.channel.id}`\n\n"
            f"Untuk menghapus batasan (aktif di semua channel), jalankan `!clearchannel`."
            f"{FOOTER}"
        )

    @bot.command(name="clearchannel")
    async def clearchannel_cmd(ctx):
        """Hapus batasan channel — bot aktif di semua channel server."""
        core.set_setting(SETTINGS_KEY_CHANNEL, "")
        await safe_send(ctx, 
            f"{header('Channel Setup')}"
            f"✅ Batasan channel dihapus. Bot sekarang aktif di semua channel."
            f"{FOOTER}"
        )

    # ── Help / start ───────────────────────────────────────────
    @bot.command(name="start", aliases=["help"])
    async def start_cmd(ctx):
        text = (
            f"{header('AI Agent')}"
            "Selamat datang, Trader! 👋\n\n"
            "Saya siap membantu analisis forex berbasis data ekonomi & teknikal real-time.\n\n"
            "**📌 Kalender & Berita:**\n"
            "├ !calendar — Event USD hari ini\n"
            "├ !high — High Impact minggu ini\n"
            "├ !bias [event] — Analisis bias fundamental\n"
            "├ !eventpreview [event opsional] — Kumpulkan berita + prediksi hasil "
            "NFP/CPI/FOMC/PPI terdekat\n"
            "└ !news [query] — Cari berita forex terkini\n\n"
            "**🌍 Insight Makro & Bias:**\n"
            "├ !macro — Insight makro/mikro + bias Gold, USD & forex saat ini\n"
            "├ !macroon — Langganan briefing otomatis 3x/hari\n"
            "└ !macrooff — Berhenti langganan\n\n"
            "**💹 Data & Analisis Teknikal:**\n"
            "├ !harga [pair] — Harga terkini, cth: `!harga EURUSD`\n"
            "├ !chart [pair] [tf] — Analisis teknikal singkat, cth: `!chart XAUUSD 4h`\n"
            "├ !regime [pair] [tf] — Analisis regime lengkap\n"
            "├ !confidence [pair] [tf] — Breakdown 10 faktor scoring\n"
            "└ !sr [pair] [tf] — Level support & resistance\n\n"
            "**🔔 Watchlist & Alert Otomatis:**\n"
            "├ !watch [pair] — Tambah pair ke watchlist, cth: `!watch XAUUSD`\n"
            "├ !unwatch [pair] — Hapus pair dari watchlist\n"
            "├ !watchlist — Lihat watchlist kamu\n"
            "├ !alert [pair] [op] [harga] — Custom price alert, cth: `!alert XAUUSD > 4100`\n"
            "├ !alerts — Lihat alert aktif | !unalert [id] — Hapus alert\n"
            "├ !sessionon — Notif setiap sesi trading dibuka\n"
            "└ !sessionoff — Berhenti notif sesi\n\n"
            "**🧭 Sinyal & Analisis Lanjutan:**\n"
            "├ !signal — Satu keputusan entry XAUUSD dari M15 + H4 + fundamental\n"
            "├ !confluence [pair] — Cek keselarasan bias di 4 timeframe\n"
            "├ !pattern [pair] [tf] — Deteksi candlestick pattern + cek lokasi S/R\n"
            "├ !backtest [pair] [tf] [n_candle] — Backtest auto-signal (win rate, expectancy, dst)\n"
            "├ !lot [balance] [risk%] [SL pips] [pair] — Hitung position size\n"
            "└ !correlation [pair1] [pair2] ... — Matriks korelasi antar pair\n"
            "*(Auto-signal: watchlist kamu otomatis di-scan, alert dikirim kalau ada setup kuat)*\n"
            "*(High Impact NFP/CPI/FOMC/PPI: alert otomatis + berita terkait + prediksi hasil)*\n\n"
            "**🤖 AI & Lainnya:**\n"
            "├ !ai [pertanyaan] — Tanya AI\n"
            "├ !reset — Reset percakapan\n"
            "├ !debrief — Cek update harian sekarang juga (manual, tanpa subscribe)\n"
            "├ !debriefon — Rangkuman pasar harian otomatis\n"
            "└ !debriefoff — Berhenti debrief harian\n\n"
            "**🖼 Analisis Chart Gambar:**\n"
            "Kirim screenshot chart + caption `!analize [pertanyaan]`.\n\n"
            "💬 *Chat pribadi (DM): langsung tanya atau kirim chart.*\n"
            "⚡ *Slash command juga tersedia:* `/chart`, `/confluence`, `/lot`"
            f"{FOOTER}"
        )
        await safe_send(ctx, text)

    # ── Kalender & berita ──────────────────────────────────────
    @bot.command(name="calendar")
    async def calendar_cmd(ctx):
        await safe_send(ctx, "⏳ *Mengambil data kalender...*")
        events = get_calendar(filter_country="USD")
        if not events:
            note = "⚠️ Gagal mengambil data (coba lagi nanti)." if events is None else "Tidak ada event USD minggu ini."
            await safe_send(ctx, f"{header('Kalender USD')}{note}{FOOTER}")
            return

        today = datetime.now().strftime("%m-%d-%Y")
        today_events = [e for e in events if today in e["date"]]

        if not today_events:
            text = f"{header('Kalender USD')}📅 Tidak ada event USD hari ini.{FOOTER}"
        else:
            lines = [f"{header('Event USD Hari Ini')}"]
            for i, e in enumerate(today_events):
                if e["impact"] == "High":
                    impact_badge = "🔴 **HIGH**"
                elif e["impact"] == "Medium":
                    impact_badge = "🟡 **MED**"
                else:
                    impact_badge = "⚪ **LOW**"
                connector = "└" if i == len(today_events) - 1 else "├"
                actual = e.get("actual", "N/A")
                actual_line = f"  |  ✅ Actual: `{actual}`" if actual and actual != "N/A" else ""
                lines.append(
                    f"{connector} {impact_badge} — **{e['title']}**\n"
                    f"    🕐 {e['time']}  |  📈 Forecast: `{e['forecast']}`  |  📉 Prev: `{e['previous']}`{actual_line}\n"
                )
            lines.append(FOOTER)
            text = "\n".join(lines)

        await safe_send(ctx, text)

    @bot.command(name="high")
    async def high_impact_cmd(ctx):
        await safe_send(ctx, "⏳ *Mengambil event High Impact...*")
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
                    f"{connector} 🔴 **{e['title']}**\n"
                    f"    📅 {e['date']}  🕐 {e['time']}\n"
                    f"    📈 Forecast: `{e['forecast']}`  📉 Prev: `{e['previous']}`{actual_line}\n"
                )
            lines.append(FOOTER)
            text = "\n".join(lines)

        await safe_send(ctx, text)

    @bot.command(name="bias")
    async def bias_cmd(ctx, *, event_name: str = None):
        user_id = ctx.author.id

        if not event_name:
            events = get_calendar(filter_country="USD", filter_impact="High")
            if not events:
                note = "⚠️ Gagal mengambil data kalender (coba lagi nanti)." if events is None else "Tidak ada event High Impact minggu ini."
                await safe_send(ctx, f"{header('Analisis Bias')}{note}{FOOTER}")
                return
            e = events[0]
            prompt = (
                f"Berikan analisis bias trading untuk event: {e['title']} | "
                f"Waktu: {e['date']} {e['time']} | Forecast: {e['forecast']} | Previous: {e['previous']}"
            )
        else:
            prompt = f"Berikan analisis bias trading untuk event ekonomi USD: {event_name}"

        await safe_send(ctx, "⏳ *Menganalisis dengan AI...*")
        result = analyze_with_groq(prompt, get_history(user_id, platform=PLATFORM))
        add_to_history(user_id, "user", prompt, platform=PLATFORM)
        add_to_history(user_id, "assistant", result, platform=PLATFORM)

        await safe_send(ctx, f"{header('Analisis Bias')}{result}{FOOTER}")

    @bot.command(name="eventpreview")
    async def event_preview_cmd(ctx, *, query: str = None):
        """Kumpulkan berita + bikin analisa prediksi untuk event NFP/CPI/FOMC/PPI
        terdekat (atau nama event yang diketik manual)."""
        if query:
            event = {"title": query, "date": "-", "time": "-", "forecast": "N/A", "previous": "N/A"}
        else:
            events = get_calendar(filter_country="USD", filter_impact="High")
            if events is None:
                await safe_send(ctx, 
                    f"{header('Event Preview & Prediction')}"
                    "⚠️ Gagal mengambil data kalender (coba lagi nanti)."
                    f"{FOOTER}"
                )
                return
            major_events = [e for e in events if core.is_major_event(e["title"])]
            if not major_events:
                await safe_send(ctx, 
                    f"{header('Event Preview & Prediction')}"
                    "Tidak ada event NFP/CPI/FOMC/PPI di kalender minggu ini.\n"
                    "Bisa juga cari manual: `!eventpreview [nama event]`"
                    f"{FOOTER}"
                )
                return
            event = major_events[0]

        await safe_send(ctx, f"🔍 *Mengumpulkan berita & menyusun prediksi untuk {event['title']}...*")
        result = core.generate_event_prediction(event)

        title_text = f"Event Preview: {event['title']}"
        await safe_send(ctx, 
            f"{header(title_text)}"
            f"📅 {event['date']} {event['time']} | Forecast: `{event['forecast']}` | Previous: `{event['previous']}`\n\n"
            f"{result}"
            f"{FOOTER}"
        )

    @bot.command(name="news")
    async def news_cmd(ctx, *, query: str = None):
        query = query or "forex market news today"
        await safe_send(ctx, "🔍 *Mencari berita terbaru...*")
        result = search_news(query)
        await safe_send(ctx, f"{header('Berita Forex')}{result}{FOOTER}")

    @bot.command(name="macro")
    async def macro_cmd(ctx):
        await safe_send(ctx, "⏳ *Mengambil berita makro & menyusun analisis...*")
        result = generate_macro_briefing()
        await safe_send(ctx, f"{header('Insight Makro & Bias')}{result}{FOOTER}")

    @bot.command(name="macroon")
    async def macro_sub_cmd(ctx):
        core.add_macro_sub(ctx.author.id, ctx.channel.id, platform=PLATFORM)
        await safe_send(ctx, 
            f"{header('Macro Briefing Subscription')}"
            "✅ Kamu akan menerima insight makro & bias otomatis 3x/hari "
            "(sesi Asia 07:00, London 14:00, New York 19:30 WIB).\n\n"
            "Ketik `!macrooff` untuk berhenti."
            f"{FOOTER}"
        )

    @bot.command(name="macrooff")
    async def macro_unsub_cmd(ctx):
        core.remove_macro_sub(ctx.author.id, platform=PLATFORM)
        await safe_send(ctx, "🔕 Berhenti berlangganan macro briefing otomatis.")

    # ── Harga & analisis teknikal ──────────────────────────────
    @bot.command(name="harga")
    async def harga_cmd(ctx, raw: str = None):
        if not raw:
            await safe_send(ctx, 
                f"{header('Harga Realtime')}"
                "Contoh penggunaan:\n"
                "`!harga EURUSD`\n"
                "`!harga XAUUSD`"
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        await safe_send(ctx, "⏳ *Mengambil harga...*")

        result = get_forex_price(symbol)
        if "error" in result:
            await safe_send(ctx, f"{header('Harga Realtime')}❌ **Error:** {result['error']}{FOOTER}")
            return

        now = datetime.now().strftime("%d %b %Y, %H:%M:%S")
        text = (
            f"{header('Harga Realtime')}"
            f"💱 **{result['symbol']}**\n\n"
            f"💰 Harga: `{result['price']}`\n"
            f"🕐 Update: {now}"
            f"{FOOTER}"
        )
        await safe_send(ctx, text)

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

    async def _generate_chart_data(symbol: str, interval: str):
        """Helper reusable oleh !chart (prefix) dan /chart (slash + dropdown).
        Return (text, chart_image_bytes_or_None)."""
        df, err = _ohlcv_or_error(symbol, interval)
        if err:
            return f"{header('Analisis Teknikal')}❌ **Error:** {err}{FOOTER}", None

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
        analysis = analyze_with_groq(prompt)

        text = (
            f"{header(f'Analisis {symbol} {interval}')}"
            f"💰 Harga: `{indicators['close']}`  |  🏛 Regime: `{regime}`\n"
            f"📐 Struktur: {structure_summary}\n\n"
            f"{analysis}"
            f"{FOOTER}"
        )

        chart_bytes = None
        try:
            chart_bytes = core.generate_chart_image(df, symbol, interval, sr, market_structure=structure)
        except Exception as ex:
            print(f"Gagal generate chart image: {ex}")
            # Gambar gagal dibuat bukan alasan buat gagalin seluruh command —
            # teks analisa tetap dikirim di pemanggil.

        return text, chart_bytes

    @bot.command(name="chart")
    async def chart_cmd(ctx, raw: str = None, interval: str = "1h"):
        if not raw:
            await safe_send(ctx, 
                f"{header('Analisis Teknikal')}"
                "Contoh penggunaan:\n"
                "`!chart EURUSD 1h`\n"
                "`!chart XAUUSD 4h`\n\n"
                "Timeframe tersedia: `1min 5min 15min 1h 4h 1day`"
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        await safe_send(ctx, f"⏳ *Mengambil data {symbol} {interval}...*")
        await safe_send(ctx, "🧠 *Menganalisis dengan AI...*")
        text, chart_bytes = await _generate_chart_data(symbol, interval)
        if chart_bytes:
            await ctx.send(file=discord.File(io.BytesIO(chart_bytes), filename="chart.png"))
        await safe_send(ctx, text)

    @bot.command(name="regime")
    async def regime_cmd(ctx, raw: str = None, interval: str = "1h"):
        if not raw:
            await safe_send(ctx, 
                f"{header('Regime')}"
                "Contoh penggunaan:\n"
                "`!regime EURUSD 1h`\n"
                "`!regime XAUUSD 4h`"
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        await safe_send(ctx, f"⏳ *Menganalisis regime {symbol} {interval}...*")

        df, err = _ohlcv_or_error(symbol, interval)
        if err:
            await safe_send(ctx, f"{header('Regime')}❌ **Error:** {err}{FOOTER}")
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
        await safe_send(ctx, text)

    @bot.command(name="confidence")
    async def confidence_cmd(ctx, raw: str = None, interval: str = "1h"):
        if not raw:
            await safe_send(ctx, 
                f"{header('Confidence')}"
                "Contoh penggunaan:\n"
                "`!confidence EURUSD 1h`\n"
                "`!confidence XAUUSD 4h`"
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        await safe_send(ctx, f"⏳ *Menghitung confidence {symbol} {interval}...*")

        df, err = _ohlcv_or_error(symbol, interval)
        if err:
            await safe_send(ctx, f"{header('Confidence')}❌ **Error:** {err}{FOOTER}")
            return

        indicators = calculate_indicators(df)
        conf = calculate_confidence(indicators)

        lines = [header(f"Technical Score {symbol} {interval}")]
        lines.append(f"Technical Score: **{conf['confidence']}%**  |  Edge: **{conf['edge']}/100**")
        lines.append(f"Bullish ✅: **{conf['bullish_score']}/{conf['max_score']}**  |  Bearish ❌: **{conf['bearish_score']}/{conf['max_score']}**")
        lines.append(f"*⚠️ Ini skor kesearahan indikator teknikal, BUKAN probabilitas kemenangan trade.*\n")
        for f in conf["detail_factors"]:
            emoji = "✅" if f["direction"] == "bullish" else "❌" if f["direction"] == "bearish" else "⚪"
            lines.append(f"{emoji} **{f['name']}**: {f['note']}")
        lines.append(FOOTER)

        await safe_send(ctx, "\n".join(lines))

    @bot.command(name="sr")
    async def sr_cmd(ctx, raw: str = None, interval: str = "1h"):
        if not raw:
            await safe_send(ctx, 
                f"{header('Support & Resistance')}"
                "Contoh penggunaan:\n"
                "`!sr EURUSD 1h`\n"
                "`!sr XAUUSD 4h`"
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        await safe_send(ctx, f"⏳ *Mencari level S/R {symbol} {interval}...*")

        df, err = _ohlcv_or_error(symbol, interval)
        if err:
            await safe_send(ctx, f"{header('Support & Resistance')}❌ **Error:** {err}{FOOTER}")
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
        lines.append("**🔺 Resistance:**")
        if sr["resistance_zones"]:
            for i, z in enumerate(sr["resistance_zones"], 1):
                r = round(z["level"], 5)
                star = "⭐" * min(z["touches"], 3)
                lines.append(f"R{i}: `{r}`  ({abs(r - current) / pip:.1f} pips)  {star} *{z['touches']}x touch*")
        else:
            lines.append("*Tidak ditemukan*")
        lines.append("\n**🔻 Support:**")
        if sr["support_zones"]:
            for i, z in enumerate(sr["support_zones"], 1):
                s = round(z["level"], 5)
                star = "⭐" * min(z["touches"], 3)
                lines.append(f"S{i}: `{s}`  ({abs(current - s) / pip:.1f} pips)  {star} *{z['touches']}x touch*")
        else:
            lines.append("*Tidak ditemukan*")
        lines.append("\n*⭐ = jumlah kali price pernah menyentuh level ini (semakin banyak = semakin signifikan)*")
        lines.append(FOOTER)

        await safe_send(ctx, "\n".join(lines))

    # ── Watchlist ──────────────────────────────────────────────
    @bot.command(name="watch")
    async def watch_cmd(ctx, raw: str = None):
        if not raw:
            await safe_send(ctx, 
                f"{header('Watchlist')}Contoh penggunaan:\n`!watch XAUUSD`\n`!watch EURUSD`{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        user_id = ctx.author.id
        chat_id = ctx.channel.id
        is_guild_channel = ctx.guild is not None

        # Selalu simpan channel/DM tempat command dijalankan.
        core.add_watch(user_id, chat_id, symbol, platform=PLATFORM)

        dest = "channel ini" if is_guild_channel else "DM kamu"
        await safe_send(ctx, 
            f"{header('Watchlist')}✅ **{symbol}** ditambahkan.\n"
            f"Alert trap otomatis (1H) akan dikirim ke {dest}.{FOOTER}"
        )

    @bot.command(name="unwatch")
    async def unwatch_cmd(ctx, raw: str = None):
        if not raw:
            await safe_send(ctx, f"{header('Watchlist')}Contoh penggunaan:\n`!unwatch XAUUSD`{FOOTER}")
            return

        symbol = format_symbol(raw)
        core.remove_watch(ctx.author.id, symbol, platform=PLATFORM)
        await safe_send(ctx, f"{header('Watchlist')}🗑 **{symbol}** dihapus dari watchlist kamu.{FOOTER}")

    @bot.command(name="watchlist")
    async def watchlist_cmd(ctx):
        rows = core.list_watch(ctx.author.id, platform=PLATFORM)
        if not rows:
            await safe_send(ctx, 
                f"{header('Watchlist')}Watchlist kamu masih kosong.\nTambah dengan `!watch [pair]`{FOOTER}"
            )
            return

        lines = [header("Watchlist Kamu")]
        for r in rows:
            lines.append(f"• {r['symbol']}")
        lines.append(FOOTER)
        await safe_send(ctx, "\n".join(lines))

    # ── Kategori 1: Sinyal & Analisis ───────────────────────────
    @bot.command(name="backtest")
    async def backtest_cmd(ctx, raw: str = None, interval: str = "1h", outputsize: str = "500"):
        if not raw:
            await safe_send(ctx,
                f"{header('Backtest Auto-Signal')}"
                "Contoh penggunaan:\n"
                "`!backtest XAUUSD`\n"
                "`!backtest EURUSD 1h 500`\n\n"
                "Format: `!backtest [pair] [timeframe] [jumlah_candle]`\n"
                "Default: timeframe 1h, 500 candle terakhir.\n\n"
                "⚠️ Ini backtest sederhana IN-SAMPLE (bukan out-of-sample/walk-forward), "
                "cuma langkah pertama buat cek apakah rule auto-signal sekarang punya "
                "edge dasar atau enggak. Butuh waktu ~10-30 detik."
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        try:
            outputsize_int = int(outputsize)
        except ValueError:
            await safe_send(ctx, f"{header('Backtest Auto-Signal')}❌ Jumlah candle harus angka.{FOOTER}")
            return
        outputsize_int = max(150, min(outputsize_int, 2000))

        await safe_send(ctx, f"⏳ *Menjalankan backtest {symbol} {interval} ({outputsize_int} candle)... bisa makan waktu 10-30 detik.*")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, core.backtest_scan_signal, symbol, interval, outputsize_int)

        if "error" in result:
            await safe_send(ctx, f"{header('Backtest Auto-Signal')}❌ {result['error']}{FOOTER}")
            return

        if result.get("total_signals", 0) == 0:
            await safe_send(ctx,
                f"{header(f'Backtest {symbol} {interval}')}"
                f"{result.get('note', 'Tidak ada sinyal.')}\n\n"
                f"*Ini BUKAN error — artinya kriteria should_alert (trending + confidence tinggi + "
                f"RR memadai + tanpa trap confirmed) memang jarang/tidak pernah terpenuhi di "
                f"{result.get('candles_used', outputsize_int)} candle terakhir.*"
                f"{FOOTER}"
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
            f"Total sinyal: **{result['total_signals']}** (Win: {result['wins']} | Loss: {result['losses']})\n"
            f"Win rate: **{result['win_rate']}%**\n"
            f"Profit factor: **{pf_display}**\n"
            f"Expectancy: **{result['expectancy_r']} R/trade**\n"
            f"Max drawdown: **{result['max_drawdown_r']} R**\n"
            f"Avg RR target: {result['avg_rr_target']}\n\n"
            f"{verdict}\n\n"
            f"*Catatan: RR pakai target saat sinyal (bukan realized), belum ada slippage/spread, "
            f"ini backtest in-sample sederhana — bukan jaminan performa live.*"
            f"{FOOTER}"
        )
        await safe_send(ctx, text)

    @bot.command(name="pattern")
    async def pattern_cmd(ctx, raw: str = None, interval: str = "1h"):
        if not raw:
            await safe_send(ctx,
                f"{header('Candlestick Pattern')}"
                "Contoh penggunaan:\n"
                "`!pattern XAUUSD`\n"
                "`!pattern EURUSD 4h`"
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        await safe_send(ctx, f"⏳ *Mengecek pola candlestick {symbol} {interval}...*")

        df, err = _ohlcv_or_error(symbol, interval)
        if err:
            await safe_send(ctx, f"{header('Candlestick Pattern')}❌ **Error:** {err}{FOOTER}")
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
            await safe_send(ctx, text)
            return

        lines = [header(f"Candlestick Pattern {symbol} {interval}")]
        lines.append(f"Regime: {regime} | Technical Score: {conf['confidence']}% (BUKAN probabilitas)\n")
        lines.append(f"🕯 Pola terdeteksi: **{pattern_info['pattern']}**")

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
            lines.append("*Doji = indecision, tidak directional.*")

        lines.append(FOOTER)
        await safe_send(ctx, "\n".join(lines))

    @bot.command(name="confluence")
    async def confluence_cmd(ctx, raw: str = None):
        if not raw:
            await safe_send(ctx, 
                f"{header('Multi-Timeframe Confluence')}Contoh: `!confluence XAUUSD`{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        await safe_send(ctx, f"⏳ *Mengecek confluence {symbol} di 4 timeframe...*")

        result = get_confluence(symbol)
        if "error" in result:
            await safe_send(ctx, f"{header('Multi-Timeframe Confluence')}❌ **Error:** {result['error']}{FOOTER}")
            return

        lines = [header(f"Confluence {symbol}")]
        lines.append(f"🧭 **Overall:** {result['overall']}\n")
        for tf in result["per_tf"]:
            if "error" in tf:
                lines.append(f"• `{tf['interval']}`: ❌ {tf['error']}")
                continue
            emoji = "🟢" if tf["bias"] == "Bullish" else "🔴" if tf["bias"] == "Bearish" else "⚪"
            htf_tag = " 👑HTF" if tf["interval"] in ("1day", "4h") else ""
            lines.append(f"• `{tf['interval']}`{htf_tag}: {emoji} {tf['bias']} ({tf['confidence']}%) — {tf['regime']}")
        lines.append(f"\n*👑HTF (1day/4h) = otoritas directional bias lebih tinggi daripada 1h/15min.*")
        lines.append(FOOTER)
        await safe_send(ctx, "\n".join(lines))

    @bot.command(name="lot")
    async def lot_cmd(ctx, balance: str = None, risk_percent: str = None, sl_pips: str = None, raw: str = None):
        if balance is None or risk_percent is None or sl_pips is None:
            await safe_send(ctx, 
                f"{header('Position Size Calculator')}"
                "Contoh penggunaan:\n"
                "`!lot [balance] [risk%] [SL_pips] [pair(opsional, default XAUUSD)]`\n"
                "`!lot 1000 1 20 XAUUSD`"
                f"{FOOTER}"
            )
            return

        try:
            balance_f = float(balance)
            risk_f = float(risk_percent)
            sl_f = float(sl_pips)
        except ValueError:
            await safe_send(ctx, f"{header('Position Size Calculator')}❌ Balance/risk%/SL pips harus angka.{FOOTER}")
            return

        symbol = format_symbol(raw) if raw else "XAU/USD"
        result = calculate_lot_size(balance_f, risk_f, sl_f, symbol)
        if "error" in result:
            await safe_send(ctx, f"{header('Position Size Calculator')}❌ {result['error']}{FOOTER}")
            return

        text = (
            f"{header('Position Size Calculator')}"
            f"💱 Pair: **{result['symbol']}**\n"
            f"💰 Balance: `{result['balance']}` | Risk: `{result['risk_percent']}%` (`{result['risk_amount']}`)\n"
            f"📏 SL: `{result['sl_pips']} pips` | Est. nilai pip/lot: `{result['pip_value_per_lot']}`\n\n"
            f"📐 **Lot Size: {result['lot_size']}**\n\n"
            f"*Catatan: estimasi umum, cek spesifikasi kontrak broker kamu untuk angka pasti.*"
            f"{FOOTER}"
        )
        await safe_send(ctx, text)

    @bot.command(name="correlation")
    async def correlation_cmd(ctx, *symbols):
        default_symbols = ["XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY"]
        syms = [format_symbol(s) for s in symbols] if symbols else default_symbols

        if len(syms) < 2:
            await safe_send(ctx, f"{header('Correlation Matrix')}Minimal 2 pair, contoh:\n`!correlation XAUUSD EURUSD GBPUSD`{FOOTER}")
            return

        await safe_send(ctx, f"⏳ *Menghitung korelasi {', '.join(syms)}...*")

        result = get_correlation_matrix(syms)
        if "error" in result:
            detail = "\n".join(f"• {sym}: {err}" for sym, err in result.get("errors", {}).items())
            await safe_send(ctx, f"{header('Correlation Matrix')}❌ {result['error']}\n\n{detail}{FOOTER}")
            return

        matrix_syms = result["symbols"]
        lines = [header("Correlation Matrix (1h, berbasis return)")]
        lines.append("```")
        header_row = "        " + "  ".join(f"{s.split('/')[0]:>6}" for s in matrix_syms)
        lines.append(header_row)
        for s1 in matrix_syms:
            row = f"{s1.split('/')[0]:>6}  " + "  ".join(f"{result['matrix'][s1][s2]:>6.2f}" for s2 in matrix_syms)
            lines.append(row)
        lines.append("```")
        if result["errors"]:
            lines.append(f"⚠️ Gagal ambil data: {', '.join(result['errors'].keys())}")
        lines.append(FOOTER)
        await safe_send(ctx, "\n".join(lines))

    # ── Kategori 3: Alert Otomatis ───────────────────────────────
    @bot.command(name="alert")
    async def alert_cmd(ctx, raw: str = None, operator: str = None, target: str = None):
        if raw is None or operator is None or target is None:
            await safe_send(ctx, 
                f"{header('Custom Price Alert')}"
                "Contoh penggunaan:\n"
                "`!alert XAUUSD > 4100`\n"
                "`!alert EURUSD < 1.05`\n\n"
                f"Operator yang tersedia: {', '.join(sorted(VALID_OPERATORS))}"
                f"{FOOTER}"
            )
            return

        symbol = format_symbol(raw)
        try:
            target_price = float(target)
        except ValueError:
            await safe_send(ctx, f"{header('Custom Price Alert')}❌ Target price harus angka.{FOOTER}")
            return

        result = core.add_price_alert(ctx.author.id, ctx.channel.id, symbol, operator, target_price, platform=PLATFORM)
        if "error" in result:
            await safe_send(ctx, f"{header('Custom Price Alert')}❌ {result['error']}{FOOTER}")
            return

        await safe_send(ctx, 
            f"{header('Custom Price Alert')}"
            f"✅ Alert #{result['id']} dibuat: **{symbol} {operator} {target_price}**\n"
            f"Kamu akan diberi tahu sekali saat target tercapai.\n"
            f"Lihat semua alert: `!alerts` | Hapus: `!unalert [id]`"
            f"{FOOTER}"
        )

    @bot.command(name="alerts")
    async def alerts_cmd(ctx):
        rows = core.list_price_alerts(ctx.author.id, platform=PLATFORM)
        if not rows:
            await safe_send(ctx, f"{header('Alert Kamu')}Belum ada alert aktif.\nBuat dengan `!alert [pair] [operator] [harga]`{FOOTER}")
            return

        lines = [header("Alert Kamu")]
        for r in rows:
            lines.append(f"#{r['id']} — {r['symbol']} {r['operator']} {r['target_price']}")
        lines.append(FOOTER)
        await safe_send(ctx, "\n".join(lines))

    @bot.command(name="unalert")
    async def unalert_cmd(ctx, alert_id: str = None):
        if not alert_id:
            await safe_send(ctx, f"{header('Hapus Alert')}Contoh: `!unalert 3`{FOOTER}")
            return
        try:
            aid = int(alert_id)
        except ValueError:
            await safe_send(ctx, f"{header('Hapus Alert')}❌ ID alert harus angka.{FOOTER}")
            return

        deleted = core.remove_price_alert(ctx.author.id, aid, platform=PLATFORM)
        if deleted:
            await safe_send(ctx, f"{header('Hapus Alert')}🗑 Alert #{aid} dihapus.{FOOTER}")
        else:
            await safe_send(ctx, f"{header('Hapus Alert')}❌ Alert #{aid} tidak ditemukan.{FOOTER}")

    @bot.command(name="sessionon")
    async def session_sub_cmd(ctx):
        core.add_session_sub(ctx.author.id, ctx.channel.id, platform=PLATFORM)
        await safe_send(ctx, 
            f"{header('Session Reminder')}"
            "✅ Kamu akan dapat notifikasi setiap sesi (Asia/London/New York) baru dibuka.\n\n"
            "Ketik `!sessionoff` untuk berhenti."
            f"{FOOTER}"
        )

    @bot.command(name="sessionoff")
    async def session_unsub_cmd(ctx):
        core.remove_session_sub(ctx.author.id, platform=PLATFORM)
        await safe_send(ctx, "🔕 Berhenti berlangganan session reminder.")

    # ── Kategori 4: Daily Debrief ────────────────────────────────
    @bot.command(name="debrief")
    async def debrief_cmd(ctx):
        """On-demand: kirim daily market debrief saat ini juga, tanpa perlu
        nunggu jadwal otomatis (05:00 WIB) atau subscribe dulu."""
        await safe_send(ctx, "⏳ *Menyusun update harian (multi-pair + kalender hari ini)...*")
        result = generate_daily_debrief()
        await safe_send(ctx, f"{header('📋 Daily Market Debrief')}{result}{FOOTER}")

    @bot.command(name="debriefon")
    async def debrief_sub_cmd(ctx):
        core.add_debrief_sub(ctx.author.id, ctx.channel.id, platform=PLATFORM)
        await safe_send(ctx, 
            f"{header('Daily Market Debrief')}"
            "✅ Kamu akan menerima rangkuman pasar harian otomatis (akhir sesi New York).\n\n"
            "Ketik `!debriefoff` untuk berhenti."
            f"{FOOTER}"
        )

    @bot.command(name="debriefoff")
    async def debrief_unsub_cmd(ctx):
        core.remove_debrief_sub(ctx.author.id, platform=PLATFORM)
        await safe_send(ctx, "🔕 Berhenti berlangganan daily debrief.")

    # ── AI & reset ─────────────────────────────────────────────
    @bot.command(name="reset")
    async def reset_cmd(ctx):
        core.conversation_histories[(PLATFORM, ctx.author.id)] = []
        await safe_send(ctx, "✅ **History percakapan direset.**\nKita mulai dari awal lagi!")

    @bot.command(name="ai")
    async def ai_cmd(ctx, *, question: str = None):
        user_id = ctx.author.id
        if not question:
            await safe_send(ctx, 
                f"{header('Cara Pakai !ai')}Ketik pertanyaanmu setelah command, contoh:\n\n"
                f"`!ai Bagaimana dampak NFP terhadap EURUSD?`{FOOTER}"
            )
            return

        prompt = await build_prompt_with_context(ctx.message, question)
        result = core.analyze_with_groq_agentic(
            prompt, user_id, ctx.channel.id, platform=PLATFORM,
            conversation_history=get_history(user_id, platform=PLATFORM),
        )
        add_to_history(user_id, "user", question, platform=PLATFORM)
        add_to_history(user_id, "assistant", result, platform=PLATFORM)

        await safe_send(ctx, f"{header('Analisis AI')}{result}{FOOTER}")

    @bot.command(name="signal", aliases=["entry"])
    async def signal_cmd(ctx):
        """Satu keputusan entry XAUUSD berbasis M15 + H4 + fundamental."""
        await safe_send(ctx, "⏳ Menganalisis XAUUSD dari chart M15/H4 dan data fundamental...")
        result = core.generate_xau_entry_signal()
        if result.get("error"):
            await safe_send(ctx, f"❌ {result['error']}")
            return
        await safe_send(ctx, result["text"])

    # ── Chat bebas (DM) & gambar chart ─────────────────────────
    async def handle_message(message: discord.Message):
        user_id = message.author.id
        text = message.content.strip()
        if not text:
            return

        prompt = await build_prompt_with_context(message, text)
        result = core.analyze_with_groq_agentic(
            prompt, user_id, message.channel.id, platform=PLATFORM,
            conversation_history=get_history(user_id, platform=PLATFORM),
        )
        add_to_history(user_id, "user", text, platform=PLATFORM)
        add_to_history(user_id, "assistant", result, platform=PLATFORM)

        await safe_send(message.channel, f"{header('Analisis AI')}{result}{FOOTER}")

    async def handle_image(message: discord.Message):
        caption_raw = message.content or ""
        user_context = caption_raw.strip()[len("!analize"):].strip() or None

        await safe_send(message.channel, "🔍 *Membaca chart, harap tunggu...*")

        attachment = message.attachments[0]
        image_bytes = await attachment.read()

        result = analyze_image_with_groq(bytes(image_bytes), user_context)
        await safe_send(message.channel, f"{header('Analisis Chart')}{result}{FOOTER}")

    # ── Slash Commands Native (dropdown/button) ─────────────────
    # Selain prefix "!", command paling sering dipakai juga tersedia sebagai
    # slash command asli Discord dengan pilihan timeframe via dropdown.

    class TimeframeSelect(discord.ui.Select):
        def __init__(self, symbol: str):
            self.symbol = symbol
            options = [
                discord.SelectOption(label=tf, value=tf)
                for tf in sorted(VALID_INTERVALS, key=lambda x: ["1min", "5min", "15min", "1h", "4h", "1day"].index(x))
            ]
            super().__init__(placeholder="Pilih timeframe...", options=options)

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.edit_message(
                content=f"⏳ *Menganalisis {self.symbol} ({self.values[0]})...*", view=None
            )
            text, chart_bytes = await _generate_chart_data(self.symbol, self.values[0])
            if chart_bytes:
                await interaction.followup.send(file=discord.File(io.BytesIO(chart_bytes), filename="chart.png"))
            await safe_send(interaction.followup, text)

    class TimeframeView(discord.ui.View):
        def __init__(self, symbol: str):
            super().__init__(timeout=60)
            self.add_item(TimeframeSelect(symbol))

    @bot.tree.command(name="chart", description="Analisis teknikal + AI untuk satu pair (pilih timeframe via dropdown)")
    @discord.app_commands.describe(pair="Contoh: XAUUSD, EURUSD")
    async def chart_slash(interaction: discord.Interaction, pair: str):
        symbol = format_symbol(pair)
        core.register_user(interaction.user.id, interaction.channel_id, platform=PLATFORM)
        await interaction.response.send_message(
            f"Pilih timeframe untuk **{symbol}**:", view=TimeframeView(symbol)
        )

    @bot.tree.command(name="confluence", description="Cek keselarasan bias di 4 timeframe sekaligus")
    @discord.app_commands.describe(pair="Contoh: XAUUSD, EURUSD")
    async def confluence_slash(interaction: discord.Interaction, pair: str):
        symbol = format_symbol(pair)
        core.register_user(interaction.user.id, interaction.channel_id, platform=PLATFORM)
        await interaction.response.defer()

        result = get_confluence(symbol)
        if "error" in result:
            await safe_send(interaction.followup, f"{header('Multi-Timeframe Confluence')}❌ **Error:** {result['error']}{FOOTER}")
            return

        lines = [header(f"Confluence {symbol}")]
        lines.append(f"🧭 **Overall:** {result['overall']}\n")
        for tf in result["per_tf"]:
            if "error" in tf:
                lines.append(f"• `{tf['interval']}`: ❌ {tf['error']}")
                continue
            emoji = "🟢" if tf["bias"] == "Bullish" else "🔴" if tf["bias"] == "Bearish" else "⚪"
            htf_tag = " 👑HTF" if tf["interval"] in ("1day", "4h") else ""
            lines.append(f"• `{tf['interval']}`{htf_tag}: {emoji} {tf['bias']} ({tf['confidence']}%) — {tf['regime']}")
        lines.append(f"\n*👑HTF (1day/4h) = otoritas directional bias lebih tinggi daripada 1h/15min.*")
        lines.append(FOOTER)
        await safe_send(interaction.followup, "\n".join(lines))

    @bot.tree.command(name="lot", description="Hitung position size berdasarkan balance, risk%, dan jarak SL")
    @discord.app_commands.describe(
        balance="Balance akun (mis. 1000)",
        risk_percent="Risiko per trade dalam persen (mis. 1)",
        sl_pips="Jarak Stop Loss dalam pips (mis. 20)",
        pair="Opsional, default XAUUSD",
    )
    async def lot_slash(interaction: discord.Interaction, balance: float, risk_percent: float, sl_pips: float, pair: str = "XAUUSD"):
        symbol = format_symbol(pair)
        core.register_user(interaction.user.id, interaction.channel_id, platform=PLATFORM)

        result = calculate_lot_size(balance, risk_percent, sl_pips, symbol)
        if "error" in result:
            await interaction.response.send_message(f"{header('Position Size Calculator')}❌ {result['error']}{FOOTER}"[:DISCORD_MAX_LEN])
            return

        text = (
            f"{header('Position Size Calculator')}"
            f"💱 Pair: **{result['symbol']}**\n"
            f"💰 Balance: `{result['balance']}` | Risk: `{result['risk_percent']}%` (`{result['risk_amount']}`)\n"
            f"📏 SL: `{result['sl_pips']} pips` | Est. nilai pip/lot: `{result['pip_value_per_lot']}`\n\n"
            f"📐 **Lot Size: {result['lot_size']}**\n\n"
            f"*Catatan: estimasi umum, cek spesifikasi kontrak broker kamu untuk angka pasti.*"
            f"{FOOTER}"
        )
        await interaction.response.send_message(text[:DISCORD_MAX_LEN])

    return bot

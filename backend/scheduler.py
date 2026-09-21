"""
scheduler.py — Semua alert terjadwal (kalender high-impact, trap watchlist,
macro briefing, auto-signal, price alert, volatility spike, session
reminder, daily debrief) yang dikirim ke SEMUA user, baik dari Telegram
maupun Discord, dari satu tempat.
"""

import asyncio
import datetime as _dt
from zoneinfo import ZoneInfo

import core

JAKARTA = ZoneInfo("Asia/Jakarta")


async def _send(telegram_app, discord_bot, platform: str, chat_id: int, text: str):
    try:
        if platform == "mobile":
            import storage
            storage.enqueue(text)
        elif platform == "telegram":
            await telegram_app.bot.send_message(chat_id, text, parse_mode="Markdown")
        elif platform == "discord":
            channel = discord_bot.get_channel(chat_id)
            if channel is None:
                channel = await discord_bot.fetch_channel(chat_id)
            await channel.send(text)
    except Exception as ex:
        print(f"Gagal kirim alert ke {platform}:{chat_id}: {ex}")
        raise


def _wrap(title: str, body: str, platform: str) -> str:
    """Bungkus body dengan header/footer sesuai gaya Markdown platform."""
    if platform == "discord":
        header = f"**Bayproject.fx** — {title}\n{'─' * 24}\n"
        footer = "\n\n*⚠️ Not Financial Advice. DYOR.*"
    else:
        header = f"*Bayproject.fx* — {title}\n{'─' * 24}\n"
        footer = "\n\n_⚠️ Not Financial Advice. DYOR._"
    return f"{header}{body}{footer}"


def _bold(text: str, platform: str) -> str:
    b = "**" if platform == "discord" else "*"
    return f"{b}{text}{b}"


# ── Alert yang sudah ada ─────────────────────────────────────────


async def check_high_impact_alerts(telegram_app, discord_bot):
    due_events = core.get_pending_high_impact_alerts()
    if not due_events:
        return
    targets = core.get_known_users()
    for e in due_events:
        is_major = core.is_major_event(e["title"])
        prediction = None
        if is_major:
            try:
                prediction = core.generate_event_prediction(e)
            except Exception as ex:
                print(f"[scheduler] gagal generate_event_prediction untuk {e['title']}: {ex}")

        for t in targets:
            body = (
                f"{_bold(e['title'], t['platform'])}\n"
                f"🕐 Dalam ~{e['minutes_away']} menit\n"
                f"📈 Forecast: `{e['forecast']}`  📉 Prev: `{e['previous']}`"
            )
            title = "🔴 High Impact Segera"
            if prediction:
                body += f"\n\n📋 {_bold('Konteks & Prediksi:', t['platform'])}\n{prediction}"
                title = "🎯 High Impact + Analisa Prediksi"
            text = _wrap(title, body, t["platform"])
            await _send(telegram_app, discord_bot, t["platform"], t["chat_id"], text)

        # Ditandai 'sent' SETELAH broadcast dicoba ke semua target — kalau
        # proses di atas crash duluan (exception tak terduga), baris ini
        # tidak akan tereksekusi dan event akan dicoba lagi di siklus
        # berikutnya (bukan hilang permanen).
        core.mark_calendar_alert_sent(e["_dedup_key"])


async def check_watchlist_traps(telegram_app, discord_bot):
    due = core.get_pending_trap_alerts()
    for item in due:
        text = _wrap(f"⚠️ Trap Alert {item['symbol']} (1H)", item["trap"], item["platform"])
        await _send(telegram_app, discord_bot, item["platform"], item["chat_id"], text)
        core.mark_trap_alert_sent(item["_dedup_key"])


async def send_macro_briefing(telegram_app, discord_bot):
    subs = core.get_macro_subs()
    result = core.generate_macro_briefing()
    if not result or result.startswith(("Error ", "❌", "⚠️ AI", "Error:")):
        raise RuntimeError("Macro briefing unavailable; retry on next worker cycle")
    core.set_cached_report('macro', result)
    core.mark_refresh_done('macro')
    if subs:
        for s in subs:
            text = _wrap("Insight Makro & Bias (Terjadwal)", result, s["platform"])
            await _send(telegram_app, discord_bot, s["platform"], s["chat_id"], text)


# ── Kategori 1: Auto-Signal dari watchlist ──────────────────────


async def check_scan_signals(telegram_app, discord_bot):
    due = core.get_pending_scan_signals()
    for item in due:
        sig = item["signal"]
        if sig.get("entry_format_v2"):
            # Format entry XAUUSD bersifat kontrak output: jangan tambahkan
            # header, footer, RR, disclaimer, atau penjelasan lain.
            await _send(telegram_app, discord_bot, item["platform"], item["chat_id"], sig["text"])
            core.mark_scan_signal_sent(item["_dedup_key"])
            continue

        trap_line = f"\n\n⚠️ {sig['trap_note']}" if sig.get("trap_note") else ""
        pattern_line = f"\n\n{sig['candlestick_confirmation']}" if sig.get("candlestick_confirmation") else ""
        body = (
            f"{_bold(sig['symbol'], item['platform'])} ({sig['interval']}) — {sig['direction']}\n"
            f"Regime: {sig['regime']} | Technical Score: {sig['confidence']}% (BUKAN probabilitas)\n\n"
            f"🎯 Entry: `{sig['entry']}`\n"
            f"🛑 SL: `{sig['sl']}`\n"
            f"✅ TP: `{sig['tp']}`\n"
            f"📐 RR: `{sig['rr']}`"
            f"{pattern_line}"
            f"{trap_line}\n\n"
            f"_Sinyal internal berbasis regime+confidence+RR (nearest S/R), bukan structural "
            f"entry/SL/TP dan bukan dari scanner.py eksternal. Belum divalidasi backtest._"
        )
        text = _wrap("🎯 Auto-Signal Terdeteksi", body, item["platform"])
        await _send(telegram_app, discord_bot, item["platform"], item["chat_id"], text)
        core.mark_scan_signal_sent(item["_dedup_key"])


# ── Kategori 3: Custom price alert & volatility spike ───────────


async def check_price_alerts(telegram_app, discord_bot):
    due = core.get_pending_price_alerts()
    for item in due:
        body = (
            f"{_bold(item['symbol'], item['platform'])} {item['operator']} {item['target_price']} tercapai!\n"
            f"Harga sekarang: `{item['current_price']}`"
        )
        text = _wrap("🔔 Price Alert Tercapai", body, item["platform"])
        await _send(telegram_app, discord_bot, item["platform"], item["chat_id"], text)
        core.mark_price_alert_sent(item["_dedup_key"])


async def check_volatility_spikes(telegram_app, discord_bot):
    due = core.get_pending_volatility_spikes()
    for item in due:
        body = (
            f"{_bold(item['symbol'], item['platform'])} — volatilitas melonjak {item['ratio']}x\n"
            f"ATR sekarang: `{item['atr_now']}` (sebelumnya: `{item['atr_before']}`)\n\n"
            f"Kemungkinan ada news/whale move — perhatikan spread & slippage."
        )
        text = _wrap("⚡ Volatility Spike", body, item["platform"])
        await _send(telegram_app, discord_bot, item["platform"], item["chat_id"], text)
        core.mark_volatility_alert_sent(item["_dedup_key"])


# ── Kategori 3: Session reminder ────────────────────────────────

SESSION_SLOTS = [
    (7, 0, "🌏 Sesi Asia (Tokyo) baru saja dibuka"),
    (14, 0, "🇬🇧 Sesi London baru saja dibuka"),
    (19, 30, "🇺🇸 Sesi New York baru saja dibuka"),
]


async def send_session_reminder(telegram_app, discord_bot, message: str):
    subs = core.get_session_subs()
    if not subs:
        return
    for s in subs:
        text = _wrap("Session Reminder", message, s["platform"])
        await _send(telegram_app, discord_bot, s["platform"], s["chat_id"], text)


# ── Kategori 4: Daily Market Debrief ────────────────────────────
# Dikirim sekali sehari, kira-kira saat sesi New York tutup (~05:00 WIB).

DEBRIEF_SLOT = (5, 0)


async def send_daily_debrief(telegram_app, discord_bot):
    subs = core.get_debrief_subs()
    result = core.generate_daily_debrief()
    if not result or result.startswith(("Error ", "❌", "⚠️ AI", "Error:")):
        raise RuntimeError("Daily debrief unavailable; retry on next worker cycle")
    core.set_cached_report('debrief', result)
    core.mark_refresh_done('debrief')
    if subs:
        for s in subs:
            text = _wrap("📋 Daily Market Debrief", result, s["platform"])
            await _send(telegram_app, discord_bot, s["platform"], s["chat_id"], text)


# ── Loop utama ───────────────────────────────────────────────────


def _slot_matches(now_jkt, h, m):
    """True kalau waktu sekarang ada di sekitar (h,m), toleransi ±1.5 menit
    (siklus scheduler jalan tiap 15 menit)."""
    target = now_jkt.replace(hour=h, minute=m, second=0, microsecond=0)
    diff = abs((now_jkt - target).total_seconds())
    return diff <= 90


async def run_scheduler(telegram_app, discord_bot):
    """Loop utama: cek alert kalender/trap/scan/price/volatility tiap 15
    menit, session reminder di 3 slot waktu, macro briefing 3x/hari, dan
    daily debrief 1x/hari."""
    last_macro_slot_sent = None
    last_session_slot_sent = None
    last_debrief_date_sent = None

    # beri waktu bot connect dulu
    await asyncio.sleep(10)

    while True:
        checks = [
            ("check_high_impact_alerts", check_high_impact_alerts),
            ("check_watchlist_traps", check_watchlist_traps),
            ("check_scan_signals", check_scan_signals),
            ("check_price_alerts", check_price_alerts),
            ("check_volatility_spikes", check_volatility_spikes),
        ]
        for name, fn in checks:
            try:
                await fn(telegram_app, discord_bot)
            except Exception as ex:
                print(f"[scheduler] error {name}: {ex}")

        now_jkt = _dt.datetime.now(JAKARTA)

        # Macro briefing & session reminder pakai slot waktu yang sama.
        try:
            for h, m, msg in SESSION_SLOTS:
                if _slot_matches(now_jkt, h, m):
                    slot = (now_jkt.date(), h, m)

                    if slot != last_macro_slot_sent:
                        await send_macro_briefing(telegram_app, discord_bot)
                        last_macro_slot_sent = slot

                    if slot != last_session_slot_sent:
                        await send_session_reminder(telegram_app, discord_bot, msg)
                        last_session_slot_sent = slot
                    break
        except Exception as ex:
            print(f"[scheduler] error macro/session slot: {ex}")

        # Daily debrief 1x/hari.
        try:
            if _slot_matches(now_jkt, *DEBRIEF_SLOT) and now_jkt.date() != last_debrief_date_sent:
                await send_daily_debrief(telegram_app, discord_bot)
                last_debrief_date_sent = now_jkt.date()
        except Exception as ex:
            print(f"[scheduler] error send_daily_debrief: {ex}")

        await asyncio.sleep(900)  # 15 menit

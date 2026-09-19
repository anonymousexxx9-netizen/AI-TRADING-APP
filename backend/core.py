"""
core.py — Business logic murni (Telegram/Discord-agnostic).

Semua fungsi di sini TIDAK bergantung pada library telegram atau discord.
Dipakai bersama oleh telegram_bot.py dan discord_bot.py.
"""

import os
import base64
import io
import json
import re
import math
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import requests
import numpy as np
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from tavily import TavilyClient
from duckduckgo_search import DDGS

from ai_gateway import chat_completion
GROQ_API_KEY = os.environ.get("AI_API_KEY") or os.environ.get("GROQ_API_KEY", "")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
GROQ_MODEL = "openai/gpt-oss-120b"  # pengganti resmi llama-3.3-70b-versatile
                                     # (shutdown 16 Agu 2026, lihat console.groq.com/docs/deprecations)
GROQ_VISION_MODEL = "qwen/qwen3.6-27b"  # pengganti resmi llama-4-scout-17b-16e-instruct
                                        # (shutdown 17 Jul 2026). Catatan: qwen3.6-27b masih
                                        # berstatus PREVIEW di Groq (bukan production) -- ini
                                        # satu-satunya opsi vision-capable yang direkomendasikan
                                        # Groq saat ini, openai/gpt-oss-120b TIDAK menerima input
                                        # gambar. Kalau nanti Groq keluarin vision model production
                                        # baru, pertimbangkan pindah lagi.
FF_RSS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
GROQ_MODEL = os.environ.get("AI_TEXT_MODEL", GROQ_MODEL)
GROQ_VISION_MODEL = os.environ.get("AI_VISION_MODEL", GROQ_VISION_MODEL)
VISION_BASE_URL = os.environ.get("AI_VISION_BASE_URL", "https://openrouter.ai/api/v1")
VISION_API_KEY = os.environ.get("AI_VISION_API_KEY", "")
TWELVE_DATA_BASE = "https://api.twelvedata.com"
DB_PATH = os.environ.get("DB_PATH", "bayproject.db")


# ── Persistent Storage (SQLite) ───────────────────────────────
# Semua tabel diberi kolom `platform` ('telegram' / 'discord') supaya
# satu database bisa dipakai bersama tanpa user_id dari 2 platform bentrok.


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            UNIQUE(user_id, symbol, platform)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS known_users (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            PRIMARY KEY (user_id, platform)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_calendar_alerts (
            event_key TEXT PRIMARY KEY,
            sent_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_trap_alerts (
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            signal_hash TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            UNIQUE(user_id, symbol, alert_date, signal_hash, platform)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_subs (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            PRIMARY KEY (user_id, platform)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            operator TEXT NOT NULL,
            target_price REAL NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_subs (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            PRIMARY KEY (user_id, platform)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS debrief_subs (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            PRIMARY KEY (user_id, platform)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_scan_signals (
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            signal_hash TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            UNIQUE(user_id, symbol, alert_date, signal_hash, platform)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_volatility_alerts (
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'telegram',
            UNIQUE(user_id, symbol, alert_date, platform)
        )
        """
    )
    conn.commit()

    # Migrasi ringan: tambah kolom platform ke tabel lama jika belum ada.
    for table in ("watchlist", "known_users", "sent_trap_alerts", "macro_subs"):
        if not _column_exists(conn, table, "platform"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN platform TEXT NOT NULL DEFAULT 'telegram'")
    conn.commit()

    # Migrasi known_users: pastikan PRIMARY KEY adalah (user_id, platform).
    # DB lama hanya punya PRIMARY KEY (user_id) — ON CONFLICT(user_id, platform) akan crash.
    pk_cols = [
        r[1] for r in conn.execute("PRAGMA table_info(known_users)").fetchall()
        if r[5] > 0  # kolom pk > 0 artinya bagian dari PRIMARY KEY
    ]
    if sorted(pk_cols) != sorted(["user_id", "platform"]):
        conn.execute("ALTER TABLE known_users RENAME TO _known_users_old")
        conn.execute(
            """
            CREATE TABLE known_users (
                user_id  INTEGER NOT NULL,
                chat_id  INTEGER NOT NULL,
                platform TEXT    NOT NULL DEFAULT 'telegram',
                PRIMARY KEY (user_id, platform)
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO known_users (user_id, chat_id, platform) "
            "SELECT user_id, chat_id, platform FROM _known_users_old"
        )
        conn.execute("DROP TABLE _known_users_old")
        conn.commit()
        print("[DB migration] known_users direkonstruksi dengan composite PRIMARY KEY (user_id, platform)")

    conn.commit()
    conn.close()


def get_setting(key: str, default=None):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def register_user(user_id: int, chat_id: int, platform: str = "telegram"):
    conn = get_db()
    conn.execute(
        "INSERT INTO known_users (user_id, chat_id, platform) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, platform) DO UPDATE SET chat_id=excluded.chat_id",
        (user_id, chat_id, platform),
    )
    conn.commit()
    conn.close()


def add_watch(user_id: int, chat_id: int, symbol: str, platform: str = "telegram"):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (user_id, chat_id, symbol, platform) VALUES (?, ?, ?, ?)",
        (user_id, chat_id, symbol, platform),
    )
    conn.commit()
    conn.close()


def remove_watch(user_id: int, symbol: str, platform: str = "telegram"):
    conn = get_db()
    conn.execute(
        "DELETE FROM watchlist WHERE user_id=? AND symbol=? AND platform=?",
        (user_id, symbol, platform),
    )
    conn.commit()
    conn.close()


def list_watch(user_id: int, platform: str = "telegram"):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT symbol FROM watchlist WHERE user_id=? AND platform=? ORDER BY symbol",
            (user_id, platform),
        ).fetchall()
    finally:
        conn.close()
    return rows


def get_known_users(platform: str = None):
    conn = get_db()
    try:
        if platform:
            rows = conn.execute(
                "SELECT user_id, chat_id, platform FROM known_users WHERE platform=?", (platform,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT user_id, chat_id, platform FROM known_users").fetchall()
    finally:
        conn.close()
    return rows


def get_all_watchlist_rows():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT user_id, chat_id, symbol, platform FROM watchlist"
        ).fetchall()
    finally:
        conn.close()
    return rows


def add_macro_sub(user_id: int, chat_id: int, platform: str = "telegram"):
    conn = get_db()
    conn.execute(
        "INSERT INTO macro_subs (user_id, chat_id, platform) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, platform) DO UPDATE SET chat_id=excluded.chat_id",
        (user_id, chat_id, platform),
    )
    conn.commit()
    conn.close()


def remove_macro_sub(user_id: int, platform: str = "telegram"):
    conn = get_db()
    conn.execute("DELETE FROM macro_subs WHERE user_id=? AND platform=?", (user_id, platform))
    conn.commit()
    conn.close()


def get_macro_subs():
    conn = get_db()
    try:
        rows = conn.execute("SELECT user_id, chat_id, platform FROM macro_subs").fetchall()
    finally:
        conn.close()
    return rows


def get_pending_high_impact_alerts():
    """Cek kalender untuk event High Impact USD dalam 0-30 menit yang belum
    pernah ditandai terkirim. TIDAK menandai apapun di sini — pemanggil
    WAJIB memanggil mark_calendar_alert_sent() setelah broadcast betul-betul
    selesai dicoba, supaya kalau proses gagal di tengah jalan (exception,
    crash) event tidak keburu ditandai 'sent' dan alert tidak hilang
    permanen. Timezone dihitung eksplisit di America/New_York (timezone FF)
    dan dibandingkan ke waktu New York juga — sebelumnya pakai
    datetime.now() (waktu lokal server) dibandingkan ke waktu event yang
    sebenarnya NY, bisa selisih beberapa jam tergantung timezone server.

    Dedup key SEKARANG cuma title|date (bukan title|date|time) — sebelumnya
    kalau field 'time' dari FF berubah antar-fetch (data provider bisa
    revisi waktu rilis, atau feed export gratis mereka kadang tidak
    konsisten), event yang SAMA bisa dianggap 'event baru' dan alert dua
    kali dengan waktu yang beda (salah satu kemungkinan pasti bikin alert
    telat/salah — pernah kejadian, CPI y/y muncul ~3.5 jam setelah rilis
    aslinya dengan 'Dalam ~27 menit' yang jelas salah).

    Diagnostic logging ditambahkan supaya kalau ini kejadian lagi, ada bukti
    konkret (raw time dari FF vs waktu sekarang) buat mastiin apakah itu
    data FF yang tidak konsisten atau bug di parsing kita."""
    events = get_calendar(filter_country="USD", filter_impact="High")
    if not events:
        return []

    now_ny = datetime.now(ZoneInfo("America/New_York"))
    due = []
    conn = get_db()
    try:
        for e in events:
            event_dt = _parse_ff_datetime(e["date"], e["time"])
            if event_dt is None:
                continue

            minutes_away = (event_dt - now_ny).total_seconds() / 60
            if not (0 <= minutes_away <= 30):
                continue

            print(
                f"[calendar-alert-diagnostic] title={e['title']!r} raw_date={e['date']!r} "
                f"raw_time={e['time']!r} -> event_dt={event_dt.isoformat()} "
                f"now_ny={now_ny.isoformat()} minutes_away={minutes_away:.1f}"
            )

            event_key = f"{e['title']}|{e['date']}"
            already_sent = conn.execute(
                "SELECT 1 FROM sent_calendar_alerts WHERE event_key=?", (event_key,)
            ).fetchone()
            if already_sent:
                continue

            due.append({**e, "minutes_away": int(minutes_away), "_dedup_key": event_key})
    finally:
        conn.close()
    return due


def mark_calendar_alert_sent(event_key: str):
    """Panggil SETELAH broadcast event kalender selesai dicoba ke semua
    target (bukan sebelum) — lihat get_pending_high_impact_alerts()."""
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO sent_calendar_alerts (event_key, sent_at) VALUES (?, ?)",
        (event_key, datetime.now(ZoneInfo("America/New_York")).isoformat()),
    )
    conn.commit()
    conn.close()


def get_pending_trap_alerts():
    """Cek trap 1H untuk setiap (user, symbol, platform) di watchlist.
    TIDAK menandai terkirim di sini — panggil mark_trap_alert_sent() setelah
    broadcast selesai dicoba (lihat get_pending_high_impact_alerts)."""
    rows = get_all_watchlist_rows()
    if not rows:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    trap_cache = {}
    due = []
    conn = get_db()
    try:
        for row in rows:
            symbol = row["symbol"]
            if symbol not in trap_cache:
                df, err = _ohlcv_or_error(symbol, "1h")
                if err:
                    trap_cache[symbol] = None
                    continue
                indicators = calculate_indicators(df)
                trap_cache[symbol] = detect_trap(df, indicators)

            trap = trap_cache[symbol]
            if not trap or trap == "Tidak ada sinyal trap terdeteksi":
                continue

            signal_hash = str(hash(trap))
            already_sent = conn.execute(
                "SELECT 1 FROM sent_trap_alerts WHERE user_id=? AND symbol=? "
                "AND alert_date=? AND signal_hash=? AND platform=?",
                (row["user_id"], symbol, today, signal_hash, row["platform"]),
            ).fetchone()
            if already_sent:
                continue

            due.append({
                "platform": row["platform"],
                "user_id": row["user_id"],
                "chat_id": row["chat_id"],
                "symbol": symbol,
                "trap": trap,
                "_dedup_key": (row["user_id"], symbol, today, signal_hash, row["platform"]),
            })
    finally:
        conn.close()

    return due


def mark_trap_alert_sent(dedup_key):
    """Panggil SETELAH trap alert selesai dicoba dikirim (bukan sebelum)."""
    user_id, symbol, alert_date, signal_hash, platform = dedup_key
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO sent_trap_alerts (user_id, symbol, alert_date, signal_hash, platform) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, symbol, alert_date, signal_hash, platform),
    )
    conn.commit()
    conn.close()


# ── Fitur Baru: Multi-Timeframe Confluence ─────────────────────

CONFLUENCE_TIMEFRAMES = ["15min", "1h", "4h", "1day"]

# Bobot otoritas per timeframe — TF lebih tinggi = otoritas directional bias
# lebih besar, TF lebih rendah = lebih ke level setup/eksekusi (audit Level 2
# finding: "semua timeframe diperlakukan hampir setara... HTF authority dapat
# kalah oleh jumlah timeframe LTF"). 1day+4h dianggap "HTF" (higher timeframe,
# penentu bias utama), 1h+15min dianggap "LTF" (struktur/setup jangka pendek).
CONFLUENCE_TF_WEIGHT = {"1day": 4, "4h": 3, "1h": 2, "15min": 1}
CONFLUENCE_HTF = {"1day", "4h"}


def get_confluence(symbol: str) -> dict:
    """Cek regime & bias di beberapa timeframe sekaligus, kembalikan
    ringkasan per-TF + skor keselarasan (confluence) yang BERBOBOT —
    1day/4h (HTF, directional bias) punya otoritas lebih besar daripada
    1h/15min (LTF, setup/eksekusi), bukan sekadar "3 dari 4 TF bullish"
    yang menyamaratakan semua timeframe."""
    per_tf = []
    bullish_count = 0
    bearish_count = 0
    weighted_bullish = 0
    weighted_bearish = 0
    htf_bias = {}

    for tf in CONFLUENCE_TIMEFRAMES:
        df, err = _ohlcv_or_error(symbol, tf)
        if err:
            per_tf.append({"interval": tf, "error": err})
            continue
        indicators = calculate_indicators(df)
        conf = calculate_confidence(indicators)
        regime = get_regime(indicators)
        per_tf.append({
            "interval": tf,
            "regime": regime,
            "bias": conf["bias_direction"],
            "confidence": conf["confidence"],
            "weight": CONFLUENCE_TF_WEIGHT.get(tf, 1),
        })
        weight = CONFLUENCE_TF_WEIGHT.get(tf, 1)
        if conf["bias_direction"] == "Bullish":
            bullish_count += 1
            weighted_bullish += weight
        elif conf["bias_direction"] == "Bearish":
            bearish_count += 1
            weighted_bearish += weight

        if tf in CONFLUENCE_HTF:
            htf_bias[tf] = conf["bias_direction"]

    valid_tf = [t for t in per_tf if "error" not in t]
    total = len(valid_tf)
    if total == 0:
        return {"error": "Gagal mengambil data di semua timeframe.", "per_tf": per_tf}

    total_weight = weighted_bullish + weighted_bearish
    htf_values = set(htf_bias.values())
    htf_conflict = len(htf_values) > 1 and "Netral" not in htf_values

    if htf_conflict:
        # 1day dan 4h saling berlawanan arah — ini paling berbahaya buat
        # dipakai entry, HTF authority-nya sendiri belum sepakat.
        overall = f"⚠️ HTF Konflik (1day: {htf_bias.get('1day', '?')}, 4h: {htf_bias.get('4h', '?')}) — hindari entry"
    elif weighted_bullish > weighted_bearish and weighted_bullish >= total_weight * 0.6:
        overall = f"Bullish Confluence (weighted {weighted_bullish}/{total_weight}, HTF searah)" if not htf_conflict else "Bullish (HTF belum sepakat)"
    elif weighted_bearish > weighted_bullish and weighted_bearish >= total_weight * 0.6:
        overall = f"Bearish Confluence (weighted {weighted_bearish}/{total_weight}, HTF searah)"
    elif bullish_count == total:
        overall = "Full Bullish Confluence 🟢 (tapi cek bobot HTF di detail)"
    elif bearish_count == total:
        overall = "Full Bearish Confluence 🔴 (tapi cek bobot HTF di detail)"
    else:
        overall = f"Campuran / Tidak Ada Konsensus Kuat (weighted {weighted_bullish} bullish vs {weighted_bearish} bearish dari {total_weight})"

    return {
        "symbol": symbol,
        "per_tf": per_tf,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "weighted_bullish": weighted_bullish,
        "weighted_bearish": weighted_bearish,
        "total_weight": total_weight,
        "htf_conflict": htf_conflict,
        "total_tf": total,
        "overall": overall,
    }


# ── Fitur Baru: Position Size Calculator ───────────────────────


def _pip_size(symbol: str) -> float:
    if "JPY" in symbol:
        return 0.01
    if "XAU" in symbol:
        return 0.1
    return 0.0001


def _pip_value_per_lot(symbol: str, current_price: float) -> float:
    """Estimasi nilai 1 pip untuk 1 lot standar (100.000 unit / 100oz emas).
    NOTE: ini estimasi umum (asumsi akun USD) — nilai riil bisa beda tipis
    tergantung spesifikasi kontrak/broker kamu."""
    symbol = symbol.upper()
    pip = _pip_size(symbol)

    if "XAU" in symbol:
        # 1 lot standar = 100 oz. Pip 0.1 → nilai per pip = 100 * 0.1 = 10 USD
        return 100 * pip

    quote = symbol.split("/")[-1] if "/" in symbol else symbol[3:]
    lot_units = 100_000

    if quote == "USD":
        return lot_units * pip
    # Base/USD atau cross lain: konversi kasar pakai harga saat ini
    if current_price and current_price > 0:
        return (lot_units * pip) / current_price
    return lot_units * pip  # fallback kasar kalau harga tidak diketahui


def calculate_lot_size(balance: float, risk_percent: float, sl_pips: float, symbol: str) -> dict:
    """Hitung lot size berdasarkan balance, %risk, dan jarak SL (dalam pips)."""
    if sl_pips <= 0:
        return {"error": "Jarak SL (pips) harus lebih dari 0."}
    if risk_percent <= 0 or risk_percent > 100:
        return {"error": "Risk % harus antara 0-100."}

    price_result = get_forex_price(symbol)
    current_price = float(price_result["price"]) if "price" in price_result else 0.0

    risk_amount = balance * (risk_percent / 100)
    pip_value = _pip_value_per_lot(symbol, current_price)
    if pip_value <= 0:
        return {"error": "Gagal menghitung nilai pip untuk simbol ini."}

    lot_size = risk_amount / (sl_pips * pip_value)

    return {
        "symbol": symbol,
        "balance": balance,
        "risk_percent": risk_percent,
        "risk_amount": round(risk_amount, 2),
        "sl_pips": sl_pips,
        "pip_value_per_lot": round(pip_value, 4),
        "lot_size": round(lot_size, 2),
    }


# ── Fitur Baru: Correlation Matrix ──────────────────────────────


def get_correlation_matrix(symbols: list, interval: str = "1h", outputsize: int = 100) -> dict:
    """Hitung matriks korelasi (Pearson) antar beberapa pair berdasarkan
    RETURN (percentage change), bukan harga mentah — closing price adalah
    series non-stationary (trending naik/turun terus), jadi korelasi di
    atas harga langsung bisa keliru tinggi/rendah cuma karena dua pair
    sama-sama sedang trending, bukan karena benar-benar bergerak bersamaan.
    Return/pct_change jauh lebih representatif untuk "apakah dua pair
    bergerak searah hari ini" — ini yang dipakai trader buat cek exposure."""
    closes = {}
    errors = {}

    for sym in symbols:
        df, err = _ohlcv_or_error(sym, interval, outputsize)
        if err:
            errors[sym] = err
            continue
        closes[sym] = df["close"].reset_index(drop=True)

    valid_symbols = list(closes.keys())
    if len(valid_symbols) < 2:
        return {"error": "Minimal 2 simbol valid diperlukan untuk hitung korelasi.", "errors": errors}

    min_len = min(len(s) for s in closes.values())
    price_df = pd.DataFrame({sym: closes[sym].tail(min_len).reset_index(drop=True) for sym in valid_symbols})
    returns_df = price_df.pct_change().dropna()
    corr = returns_df.corr(method="pearson")

    return {
        "symbols": valid_symbols,
        "matrix": corr.round(2).to_dict(),
        "errors": errors,
        "basis": "return (pct_change), bukan harga mentah",
    }


# ── Fitur Baru: Auto-Signal Scan (internal, bukan scanner.py eksternal) ─
# Heuristik: regime harus TRENDING, confidence >= threshold, tidak ada trap
# aktif, dan RR minimal terhadap S/R terdekat >= MIN_RR.

SCAN_MIN_CONFIDENCE = 65
SCAN_MIN_RR = 1.5


# ── Fitur Baru: Candlestick Pattern Confirmation ────────────────
# Deteksi 4 pola yang punya definisi paling jelas/objektif (bukan puluhan
# pola klasik yang edge-nya lemah dan sangat konteks-dependent). Dipakai
# sebagai KONFIRMASI TAMBAHAN buat auto-signal, BUKAN alert berdiri
# sendiri — pola candlestick doang (tanpa konteks lokasi) gampang jadi
# noise. Baru dianggap berarti kalau muncul DI lokasi S/R yang sudah
# terbukti kuat (touch count tinggi) DAN searah bias yang sedang berjalan.


def detect_candlestick_pattern(df: pd.DataFrame) -> dict:
    """Deteksi pola candlestick di CANDLE TERAKHIR yang sudah closed (df
    sudah lewat _drop_unclosed_candle sebelum sampai sini). Return
    {'pattern': str|None, 'direction': 'bullish'|'bearish'|None}."""
    if len(df) < 2:
        return {"pattern": None, "direction": None}

    cur = df.iloc[-1]
    prev = df.iloc[-2]
    o, h, l, c = float(cur["open"]), float(cur["high"]), float(cur["low"]), float(cur["close"])
    po, pc = float(prev["open"]), float(prev["close"])

    body = abs(c - o)
    candle_range = h - l
    if candle_range <= 0:
        return {"pattern": None, "direction": None}

    upper_wick = h - max(c, o)
    lower_wick = min(c, o) - l

    # Engulfing: body candle sekarang "menelan" body candle sebelumnya.
    if c > o and pc < po and o <= pc and c >= po:
        return {"pattern": "Bullish Engulfing", "direction": "bullish"}
    if c < o and pc > po and o >= pc and c <= po:
        return {"pattern": "Bearish Engulfing", "direction": "bearish"}

    # Hammer (bullish): body kecil di atas range, lower wick >= 2x body, upper wick kecil.
    # Dicek SEBELUM Doji karena hammer/shooting star "textbook" justru sering
    # punya body sangat kecil (itu yang bikin sinyalnya kuat) — kalau Doji
    # dicek duluan berdasarkan body/range doang, hammer asli malah ketangkep
    # sebagai Doji dan gak pernah nyampe ke pengecekan ini. Bedanya sama Doji:
    # hammer/shooting star punya wick yang SANGAT asimetris (satu sisi panjang,
    # satu sisi pendek), Doji wick-nya relatif seimbang di kedua sisi.
    if body > 0 and lower_wick >= body * 2 and upper_wick <= body * 0.5:
        return {"pattern": "Hammer/Pin Bar", "direction": "bullish"}

    # Shooting Star (bearish): body kecil di bawah range, upper wick >= 2x body, lower wick kecil.
    if body > 0 and upper_wick >= body * 2 and lower_wick <= body * 0.5:
        return {"pattern": "Shooting Star/Pin Bar", "direction": "bearish"}

    # Doji: body sangat kecil DAN wick relatif seimbang (bukan hammer/shooting
    # star yang sudah ditangkap di atas) — indecision, tidak directional.
    if body / candle_range <= 0.1:
        return {"pattern": "Doji", "direction": None}

    return {"pattern": None, "direction": None}


def find_matching_sr_zone(price: float, sr: dict, direction: str, min_touches: int = 2, tolerance_pct: float = 0.15):
    """Cek apakah `price` sedang berada di dalam/dekat zone S/R yang cukup
    kuat (touches >= min_touches) DAN di sisi yang relevan untuk `direction`
    (bullish -> cek support zones, bearish -> cek resistance zones).
    Return zone dict kalau match, None kalau tidak."""
    zones = sr.get("support_zones", []) if direction == "bullish" else sr.get("resistance_zones", [])
    tolerance = price * (tolerance_pct / 100)
    for z in zones:
        if z["touches"] < min_touches:
            continue
        if (z["zone_low"] - tolerance) <= price <= (z["zone_high"] + tolerance):
            return z
    return None


def generate_scan_signal(symbol: str, interval: str = "1h") -> dict:
    """Wrapper: fetch data live lalu jalankan decision logic. Dipakai buat
    scan real-time (scheduler, /confluence-style commands). Buat backtest,
    pakai _scan_signal_from_df() langsung supaya gak perlu network call
    per iterasi (dan supaya window data yang dipakai persis kepilih oleh
    si pemanggil, bukan selalu 'data paling baru')."""
    df, err = _ohlcv_or_error(symbol, interval)
    if err:
        return {"should_alert": False, "error": err}
    return _scan_signal_from_df(df, symbol, interval)


# ── XAUUSD Entry Signal v2 ───────────────────────────────────────
# Untuk kontrak XAUUSD yang dipakai bot, 1 pip = 0.10 perubahan harga.
# Jadi contoh entry 3400.00 dengan SL maksimal 30 pips berarti SL paling
# jauh adalah 3397.00 untuk BUY atau 3403.00 untuk SELL.
XAU_ENTRY_PIP = Decimal("0.10")
XAU_ENTRY_MAX_SL_PIPS = Decimal("30")
XAU_ENTRY_MAX_SL_DISTANCE = XAU_ENTRY_PIP * XAU_ENTRY_MAX_SL_PIPS
XAU_ENTRY_SL_BUFFER = XAU_ENTRY_PIP
XAU_ENTRY_MIN_SL_DISTANCE = Decimal("0.50")
XAU_ENTRY_MIN_TP_R = Decimal("1.20")
XAU_ENTRY_MIN_TARGET_DISTANCE = Decimal("0.30")


def _entry_session_bucket(timestamp):
    """Klasifikasikan candle ke sesi WIB yang konsisten dengan scheduler."""
    if pd.isna(timestamp):
        return None, None
    ts = pd.Timestamp(timestamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ts = ts.tz_convert("Asia/Jakarta")
    minutes = ts.hour * 60 + ts.minute
    if 7 * 60 <= minutes < 14 * 60:
        return "Asia", ts.date()
    if 14 * 60 <= minutes < 19 * 60 + 30:
        return "London", ts.date()
    # New York berjalan melewati tengah malam WIB. Gunakan tanggal saat sesi
    # dibuka agar candle 01:00 WIB tetap berada di sesi NY sebelumnya.
    session_date = ts.date() if minutes >= 19 * 60 + 30 else (ts - pd.Timedelta(days=1)).date()
    return "New York", session_date


def _entry_session_levels(df: pd.DataFrame) -> dict:
    """Ambil high/low sesi dan hanya expose sesi yang sudah selesai.

    Level sesi yang sedang berjalan tidak dijadikan target agar target tidak
    berpindah-pindah setiap candle. Semua waktu ditampilkan dalam WIB.
    """
    if df is None or df.empty or "datetime" not in df.columns:
        return {"timezone": "Asia/Jakarta", "current_session": None, "completed": {}}

    working = df.copy()
    working["_session"] = working["datetime"].apply(_entry_session_bucket)
    working = working.dropna(subset=["_session"])
    if working.empty:
        return {"timezone": "Asia/Jakarta", "current_session": None, "completed": {}}

    working["_session_name"] = working["_session"].map(lambda value: value[0])
    working["_session_date"] = working["_session"].map(lambda value: value[1])
    last_name, last_date = working["_session"].iloc[-1]
    completed = {}
    for name in ("Asia", "London", "New York"):
        rows = working[
            (working["_session_name"] == name)
            & (
                (working["_session_date"] < last_date)
                | (
                    (working["_session_date"] == last_date)
                    & (name != last_name)
                )
            )
        ]
        if rows.empty:
            continue
        grouped = (
            rows.groupby("_session_date", sort=True)
            .agg(high=("high", "max"), low=("low", "min"))
            .tail(2)
        )
        completed[name] = [
            {
                "date": str(index),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
            }
            for index, row in grouped.iterrows()
        ]

    return {
        "timezone": "Asia/Jakarta",
        "current_session": last_name,
        "completed": completed,
    }


def _entry_price_levels(m15: dict, h4: dict, position: str, entry: Decimal) -> list:
    """Susun kandidat level target/invalidasi dari swing dan sesi."""
    levels = []
    for timeframe, snapshot in (("M15", m15), ("H4", h4)):
        structure = snapshot.get("market_structure") or {}
        for swing in structure.get("recent_swings") or []:
            try:
                price = Decimal(str(swing["price"]))
            except (KeyError, InvalidOperation, TypeError, ValueError):
                continue
            level_type = "buyside_liquidity" if swing.get("type") == "high" else "sellside_liquidity"
            levels.append({
                "price": price,
                "basis": f"{level_type}_{timeframe}",
                "label": swing.get("label") or swing.get("type"),
            })

        for session_name, session_rows in (snapshot.get("session_levels", {}).get("completed", {}) or {}).items():
            for row in session_rows[-1:]:
                try:
                    high = Decimal(str(row["high"]))
                    low = Decimal(str(row["low"]))
                except (KeyError, InvalidOperation, TypeError, ValueError):
                    continue
                levels.extend(
                    [
                        {
                            "price": high,
                            "basis": f"buyside_{session_name}_high",
                            "label": f"{session_name} high",
                        },
                        {
                            "price": low,
                            "basis": f"sellside_{session_name}_low",
                            "label": f"{session_name} low",
                        },
                    ]
                )

    unique = {}
    for level in levels:
        price = level["price"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if (position == "BUY" and price <= entry) or (position == "SELL" and price >= entry):
            continue
        unique[(price, level["basis"])] = {**level, "price": price}
    return sorted(
        unique.values(),
        key=lambda item: abs(item["price"] - entry),
    )


def _entry_target_candidates(m15: dict, h4: dict, position: str, entry: Decimal, risk: Decimal) -> list:
    """Filter target level agar berada di sisi profit dan tidak terlalu dekat."""
    minimum = max(XAU_ENTRY_MIN_TARGET_DISTANCE, risk * XAU_ENTRY_MIN_TP_R)
    candidates = []
    for level in _entry_price_levels(m15, h4, position, entry):
        distance = abs(level["price"] - entry)
        if distance >= minimum:
            candidates.append({**level, "distance": distance})
    return candidates


def _entry_reason_text(value) -> list:
    """Normalisasi alasan AI agar aman dimasukkan ke pesan Telegram/Discord."""
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = re.split(r"[\n•]+", value)
    else:
        raw_items = []

    reasons = []
    for item in raw_items:
        text = re.sub(r"[*_`]+", "", str(item)).strip()
        text = " ".join(text.split())
        if text and text not in reasons:
            reasons.append(text[:220])
    return reasons[:3]


def _entry_direction_label(trend) -> str:
    return {
        "up": "bullish",
        "down": "bearish",
        None: "netral/tidak jelas",
    }.get(trend, str(trend))


def _entry_verified_reason_lines(
    position: str,
    m15: dict,
    h4: dict,
    fundamental: dict,
    target_basis: str,
) -> list:
    """Bangun alasan dari fakta snapshot, bukan angka yang diimprovisasi AI."""
    m15_structure = m15.get("market_structure") or {}
    h4_structure = h4.get("market_structure") or {}
    lines = [
        (
            f"Struktur timeframe: H4 {_entry_direction_label(h4_structure.get('trend'))}, "
            f"M15 {_entry_direction_label(m15_structure.get('trend'))}; "
            f"keputusan akhir {position}."
        )
    ]

    choch = m15_structure.get("choch") or h4_structure.get("choch") or {}
    if choch.get("direction"):
        lines.append(
            f"CHoCH {choch['direction']} terdeteksi di level "
            f"{float(choch.get('broken_level', 0)):.2f}."
        )

    trap = m15.get("liquidity_and_trap") or {}
    if trap.get("state") and trap.get("state") not in ("NONE", "NO_TRAP"):
        trap_text = re.sub(r"[*_`]+", "", str(trap.get("text") or trap["state"]))
        lines.append(f"Konteks liquidity/trap M15: {' '.join(trap_text.split())[:190]}.")

    narrative = fundamental.get("higher_timeframe_narrative_bias") or {}
    narrative_parts = []
    for label in ("daily", "weekly", "monthly"):
        report = narrative.get(label) or {}
        bias = report.get("bias")
        if bias:
            narrative_parts.append(f"{label.capitalize()} {bias}")
    events = fundamental.get("usd_calendar_high_medium") or []
    if events:
        event_names = ", ".join(str(event.get("title", ""))[:45] for event in events[:2])
        lines.append(f"Fundamental: kalender USD berdampak tersedia ({event_names}).")
    elif narrative_parts:
        lines.append(f"Fundamental/narrative: {'; '.join(narrative_parts[:3])}.")
    else:
        lines.append("Fundamental: tidak ada event atau narrative bias yang berhasil diambil.")

    readable_target = target_basis.split(";")[-1].replace("_", " ")
    lines.append(f"Target dipilih dari {readable_target}, bukan jarak TP fixed.")
    return lines[:4]


def _entry_stop_and_target(
    position: str,
    entry: Decimal,
    m15: dict,
    h4: dict,
    ai_target: Decimal | None = None,
) -> tuple[Decimal, Decimal, str]:
    """Pilih SL struktural <= 30 pips dan TP berbasis level likuiditas.

    AI boleh mengusulkan TP, tetapi hanya dipakai bila arahnya benar dan
    cukup dekat dengan target struktural yang benar-benar ada di data.
    """
    invalidation_levels = _entry_price_levels(
        m15,
        h4,
        "SELL" if position == "BUY" else "BUY",
        entry,
    )
    if position == "BUY":
        stop_levels = [
            level["price"] - XAU_ENTRY_SL_BUFFER
            for level in invalidation_levels
            if level["price"] < entry
        ]
        stop_levels = sorted(stop_levels, reverse=True)
    else:
        stop_levels = [
            level["price"] + XAU_ENTRY_SL_BUFFER
            for level in invalidation_levels
            if level["price"] > entry
        ]
        stop_levels = sorted(stop_levels)

    sl = None
    stop_basis = "maximum_30_pips"
    for candidate in stop_levels:
        distance = abs(entry - candidate)
        if XAU_ENTRY_MIN_SL_DISTANCE <= distance <= XAU_ENTRY_MAX_SL_DISTANCE:
            sl = candidate
            stop_basis = "structural_invalidation"
            break
    if sl is None:
        sl = (
            entry - XAU_ENTRY_MAX_SL_DISTANCE
            if position == "BUY"
            else entry + XAU_ENTRY_MAX_SL_DISTANCE
        )

    sl = sl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    risk = abs(entry - sl)
    candidates = _entry_target_candidates(m15, h4, position, entry, risk)

    valid_ai_target = None
    if ai_target is not None:
        direction_ok = ai_target > entry if position == "BUY" else ai_target < entry
        ai_distance = abs(ai_target - entry)
        if direction_ok and ai_distance >= max(
            XAU_ENTRY_MIN_TARGET_DISTANCE,
            risk * XAU_ENTRY_MIN_TP_R,
        ):
            # Model hanya boleh memilih area yang dekat dengan level nyata
            # yang dikirim ke prompt; toleransi 0.50 menjaga target tetap
            # realistis tanpa memaksa harga menyentuh level persis.
            if candidates and min(abs(ai_target - item["price"]) for item in candidates) <= Decimal("0.50"):
                valid_ai_target = ai_target

    if valid_ai_target is not None:
        tp = valid_ai_target
        target_basis = "ai_validated_liquidity_or_session"
    elif candidates:
        # Jika AI tidak memberi target valid, ambil target likuiditas terdekat
        # yang memenuhi minimum RR agar target tetap realistis.
        tp = candidates[0]["price"]
        target_basis = candidates[0]["basis"]
    else:
        fallback_distance = max(risk * Decimal("1.50"), Decimal("1.00"))
        tp = entry + fallback_distance if position == "BUY" else entry - fallback_distance
        target_basis = "fallback_rr_1_5"

    tp = tp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return sl, tp, f"{stop_basis};{target_basis}"

ENTRY_SIGNAL_SYSTEM_PROMPT = (
    "Kamu adalah Bayproject.fx Entry Decision Engine untuk XAUUSD. "
    "Gunakan hanya data yang diberikan di prompt. Prioritaskan struktur harga, "
    "perubahan karakter, sweep likuiditas, lokasi harga, hubungan M15 dengan H4, "
    "berita makro, kalender ekonomi, dan narasi/sentimen pasar. "
    "Jangan menjadikan indikator populer, teori trading klasik, atau pola candlestick "
    "umum sebagai alasan utama. Jangan mengarang data atau angka. "
    "Kamu WAJIB memilih BUY atau SELL, tidak boleh HOLD atau menolak memberi keputusan. "
    "Conviction adalah penilaian keyakinan 0-100 berdasarkan kelengkapan dan keselarasan data, "
    "bukan probabilitas kemenangan yang terjamin. "
    "Balas HANYA JSON valid tanpa markdown dengan bentuk: "
    '{"position":"BUY","entry":1234.56,"tp":1238.20,"conviction":72,'
    '"target_basis":"buyside_liquidity","reason":["alasan faktual 1","alasan faktual 2"]}'
)


def _entry_chart_snapshot(df: pd.DataFrame, interval: str) -> dict:
    """Ringkas data chart untuk prompt entry tanpa menyerahkan keputusan ke indikator."""
    indicators = calculate_indicators(df)
    structure = detect_market_structure(df)
    trap = detect_trap_state(df, indicators)
    session_levels = _entry_session_levels(df)
    recent = []
    for _, row in df.tail(8).iterrows():
        dt = row.get("datetime")
        dt_text = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
        recent.append({
            "time": dt_text,
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        })

    swings = [
        {
            "type": s["type"],
            "label": s.get("label"),
            "price": round(float(s["price"]), 2),
        }
        for s in structure.get("swings", [])[-8:]
    ]
    return {
        "interval": interval,
        "current_close": round(float(df["close"].iloc[-1]), 2),
        "recent_range_high": round(float(df["high"].tail(48).max()), 2),
        "recent_range_low": round(float(df["low"].tail(48).min()), 2),
        "market_structure": {
            "trend": structure.get("trend"),
            "choch": structure.get("choch"),
            "recent_swings": swings,
        },
        "liquidity_and_trap": {
            "state": trap.get("state"),
            "text": trap.get("text"),
        },
        "session_levels": session_levels,
        "atr": round(float(indicators.get("atr", 0.0)), 2),
        # Disediakan sebagai data sekunder saja; prompt melarang menjadikannya
        # dasar utama keputusan.
        "secondary_context": {
            "regime": get_regime(indicators),
            "bias": calculate_confidence(indicators).get("bias_direction"),
        },
        "recent_bars": recent,
    }


def _entry_fundamental_context() -> dict:
    """Ambil konteks fundamental dari jalur macro/news/calendar yang sudah ada."""
    try:
        macro_news = _fetch_macro_news_items()
    except Exception as ex:
        print(f"entry signal macro news gagal: {ex}")
        macro_news = []

    try:
        calendar = get_calendar(filter_country="USD", filter_impact=None, only_today=False)
    except Exception as ex:
        print(f"entry signal calendar gagal: {ex}")
        calendar = None

    # Narrative bias memakai daily/weekly/monthly liquidity context yang sudah
    # digunakan oleh fitur macro. Ia menjadi konteks tambahan; M15/H4 tetap utama.
    try:
        narrative_bias = build_narrative_bias_report("XAUUSD")
    except Exception as ex:
        print(f"entry signal narrative bias gagal: {ex}")
        narrative_bias = {"error": str(ex)}

    news = [
        {
            "title": item.get("title", ""),
            "body": item.get("body", "")[:350],
            "url": item.get("url", ""),
        }
        for item in (macro_news or [])[:6]
    ]
    events = [
        {
            "title": event.get("title", ""),
            "date": event.get("date", ""),
            "time": event.get("time", ""),
            "impact": event.get("impact", ""),
            "forecast": event.get("forecast", ""),
            "previous": event.get("previous", ""),
            "actual": event.get("actual", ""),
        }
        for event in (calendar or [])
        if event.get("impact") in ("High", "Medium")
    ][:10]
    return {
        "macro_news": news,
        "usd_calendar_high_medium": events,
        "higher_timeframe_narrative_bias": narrative_bias,
    }


def _parse_entry_decision(raw: str) -> dict | None:
    """Parse JSON model output secara toleran, tanpa menerima HOLD."""
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    position = str(data.get("position", "")).upper().strip()
    if position not in ("BUY", "SELL"):
        return None
    try:
        entry = Decimal(str(data.get("entry")))
        conviction = int(float(data.get("conviction")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    ai_target = None
    if data.get("tp") is not None:
        try:
            ai_target = Decimal(str(data.get("tp")))
        except (InvalidOperation, TypeError, ValueError):
            ai_target = None
    return {
        "position": position,
        "entry": entry,
        "conviction": max(0, min(100, conviction)),
        "tp": ai_target,
        "target_basis": str(data.get("target_basis", "")).strip()[:80],
        "reason": _entry_reason_text(data.get("reason")),
    }


def _fallback_xau_entry_decision(m15: dict, h4: dict, spot: Decimal) -> dict:
    """Fallback deterministik agar selalu ada satu keputusan saat AI gagal."""
    votes = 0
    for snapshot, weight in ((h4, 2), (m15, 1)):
        trend = snapshot.get("market_structure", {}).get("trend")
        if trend == "up":
            votes += weight
        elif trend == "down":
            votes -= weight
        choch = snapshot.get("market_structure", {}).get("choch") or {}
        if choch.get("direction") == "bullish":
            votes += weight
        elif choch.get("direction") == "bearish":
            votes -= weight
    position = "BUY" if votes >= 0 else "SELL"
    conviction = min(49, 25 + abs(votes) * 8)
    return {
        "position": position,
        "entry": spot,
        "conviction": conviction,
        "tp": None,
        "target_basis": "fallback",
        "reason": [],
    }


def generate_xau_ma200_signal() -> dict:
    symbol = "XAU/USD"
    df, error = _ohlcv_or_error(symbol, "5min", outputsize=260)
    if error:
        return {"should_alert": False, "error": f"Data M5 XAUUSD tidak tersedia: {error}"}
    indicators = calculate_indicators(df)
    if len(df) < 200 or indicators.get("ema200") is None:
        return {"should_alert": False, "error": "Minimal 200 candle M5 diperlukan untuk MA200."}
    candle = df.iloc[-1]
    ma200 = Decimal(str(indicators["ema200"]))
    close = Decimal(str(candle["close"]))
    high = Decimal(str(candle["high"]))
    low = Decimal(str(candle["low"]))
    position = "BUY" if close > ma200 else "SELL" if close < ma200 else None
    touched = low <= ma200 <= high
    if not position or not touched:
        return {"should_alert": False, "symbol": "XAUUSD", "ma200": float(ma200), "close": float(close), "position": position, "touched": touched, "reason": "Menunggu candle M5 menyentuh MA200 dari sisi trend."}
    entry = close.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    risk = Decimal("10.00")
    sl = entry - risk if position == "BUY" else entry + risk
    tp1 = entry + risk if position == "BUY" else entry - risk
    tp2 = entry + risk * 2 if position == "BUY" else entry - risk * 2
    text = (f"🎯 XAUUSD M5 — {position}\n\n"
            f"Entry: {entry:.2f}\nMA200: {ma200:.2f}\n"
            f"SL: {sl:.2f} (100 pips)\nTP1: {tp1:.2f} (RR 1:1)\nTP2: {tp2:.2f} (RR 1:2)\n\n"
            "Sinyal muncul saat candle M5 menyentuh MA200 dari sisi trend.")
    return {"should_alert": True, "entry_format_v2": True, "symbol": "XAUUSD", "direction": position, "position": position, "entry": float(entry), "ma200": float(ma200), "sl": float(sl), "tp": float(tp1), "tp1": float(tp1), "tp2": float(tp2), "risk_pips": 100, "rr1": 1, "rr2": 2, "text": text}


def generate_xau_entry_signal() -> dict:
    return generate_xau_ma200_signal()


def generate_xau_entry_signal_legacy() -> dict:
    """Buat satu keputusan entry XAUUSD berbasis chart M15 + H4 dan fundamental.

    AI memilih arah, entry, conviction, dan kandidat TP. Python memvalidasi
    entry/TP, memilih SL struktural maksimal 30 pips, lalu memakai target
    liquidity/session yang nyata sebagai fallback.
    """
    symbol = "XAU/USD"
    m15, m15_error = _ohlcv_or_error(symbol, "15min", outputsize=150)
    h4, h4_error = _ohlcv_or_error(symbol, "4h", outputsize=150)
    if m15_error or h4_error:
        errors = "; ".join(
            part for part in (
                f"M15: {m15_error}" if m15_error else "",
                f"H4: {h4_error}" if h4_error else "",
            ) if part
        )
        return {"should_alert": False, "error": f"Data chart XAUUSD tidak tersedia: {errors}"}

    m15_snapshot = _entry_chart_snapshot(m15, "M15")
    h4_snapshot = _entry_chart_snapshot(h4, "H4")

    quote = get_forex_price(symbol)
    if "price" in quote:
        try:
            spot = Decimal(str(quote["price"]))
        except (InvalidOperation, TypeError, ValueError):
            spot = Decimal(str(m15_snapshot["current_close"]))
    else:
        spot = Decimal(str(m15_snapshot["current_close"]))

    fundamental_context = _entry_fundamental_context()
    context = {
        "pair": "XAUUSD",
        "spot_quote": str(spot),
        "chart_M15": m15_snapshot,
        "chart_H4": h4_snapshot,
        "fundamental_context": fundamental_context,
    }
    prompt = (
        "Tentukan satu keputusan entry terbaik untuk XAUUSD sekarang.\n\n"
        f"DATA TERSTRUKTUR:\n{json.dumps(context, ensure_ascii=False, separators=(',', ':'), default=str)}\n\n"
        "Aturan keputusan:\n"
        "1. M15 dan H4 adalah sumber utama analisa struktur harga.\n"
        "2. Pertimbangkan fundamental, berita, kalender USD, narasi yang dipercaya pasar, "
        "sentimen mayoritas, serta potensi liquidity trap/manipulasi.\n"
        "3. Jangan menggunakan indikator populer sebagai dasar utama; data secondary_context "
        "hanya boleh menjadi pemeriksaan tambahan.\n"
        "4. Entry harus satu angka pasti dengan maksimal dua desimal dan masuk akal terhadap "
        "harga spot serta rentang chart.\n"
        "5. Wajib memilih BUY atau SELL. Jangan memilih HOLD.\n"
        "6. Usulkan tp yang berada di area buyside liquidity (untuk BUY) atau "
        "sellside liquidity (untuk SELL), termasuk high/low sesi Asia, London, "
        "atau New York bila level itu valid. Jangan mengarang level di luar data.\n"
        "7. Keluarkan HANYA JSON sesuai format system prompt."
    )
    raw = analyze_with_groq(prompt, system_prompt=ENTRY_SIGNAL_SYSTEM_PROMPT)
    decision = _parse_entry_decision(raw)
    if decision is None:
        decision = _fallback_xau_entry_decision(m15_snapshot, h4_snapshot, spot)

    entry = decision["entry"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if entry <= XAU_ENTRY_MAX_SL_DISTANCE:
        entry = spot.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sl, tp, target_basis = _entry_stop_and_target(
        decision["position"],
        entry,
        m15_snapshot,
        h4_snapshot,
        decision.get("tp"),
    )
    emoji = "🟢" if decision["position"] == "BUY" else "🔴"
    verified_reasons = _entry_verified_reason_lines(
        decision["position"],
        m15_snapshot,
        h4_snapshot,
        fundamental_context,
        target_basis,
    )
    ai_reasons = decision.get("reason") or []
    reason_lines = []
    for reason in verified_reasons + ai_reasons:
        if reason not in reason_lines:
            reason_lines.append(reason)
    reason_lines = reason_lines[:5]
    reason_text = "\n".join(f"• {reason}" for reason in reason_lines)

    text = (
        "🚀 PAIR: XAUUSD\n\n"
        f"📊 POSISI: {emoji} {decision['position']}\n\n"
        f"🎯 ENTRY: {entry:.2f}\n\n"
        f"🛑 SL: {sl:.2f}\n\n"
        f"🏆 TP: {tp:.2f}\n\n"
        f"🔥 CONVICTION: {decision['conviction']}%\n\n"
        f"🧠 REASON:\n{reason_text}"
    )
    return {
        "should_alert": True,
        "entry_format_v2": True,
        "symbol": "XAUUSD",
        "direction": decision["position"],
        "position": decision["position"],
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "conviction": decision["conviction"],
        "target_basis": target_basis,
        "text": text,
    }


def _scan_signal_from_df(df: pd.DataFrame, symbol: str, interval: str = "1h") -> dict:
    """Decision logic murni generate_scan_signal, TIDAK fetch apapun dari
    network -- dipakai baik oleh generate_scan_signal() (live) maupun
    backtest_scan_signal() (historis), supaya keduanya menjalankan LOGIC
    YANG SAMA PERSIS. Kalau logic ini diubah, otomatis kepakai kedua jalur
    tanpa perlu disinkronkan manual."""
    indicators = calculate_indicators(df)
    conf = calculate_confidence(indicators)
    regime = get_regime(indicators)
    sr = get_support_resistance(df)
    trap_result = detect_trap_state(df, indicators)
    trap = trap_result["text"]
    close = indicators["close"]

    # Cuma trap yang CONFIRMED yang membatalkan sinyal — POTENTIAL dicatat
    # di output tapi tidak otomatis blok (audit: "tidak adanya trap bukan
    # berarti entry valid", jadi sebaliknya juga: trap yang masih lemah/
    # belum confirmed tidak seharusnya otomatis membatalkan entry yang
    # kriterianya sudah kuat di sisi lain).
    has_confirmed_trap = trap_result["state"] == "CONFIRMED"
    is_trending = "TRENDING" in regime.upper()
    strong_enough = conf["confidence"] >= SCAN_MIN_CONFIDENCE

    if not (is_trending and strong_enough and not has_confirmed_trap):
        return {"should_alert": False, "symbol": symbol, "regime": regime, "confidence": conf["confidence"]}

    direction = conf["bias_direction"]
    if direction == "Bullish":
        resistances_above = sorted([r for r in sr["resistances"] if r > close])
        supports_below = sorted([s for s in sr["supports"] if s < close], reverse=True)
        if not resistances_above or not supports_below:
            return {"should_alert": False, "symbol": symbol, "reason": "S/R tidak cukup untuk hitung RR"}
        tp = resistances_above[0]
        sl = supports_below[0]
        rr = (tp - close) / (close - sl) if (close - sl) != 0 else 0
    elif direction == "Bearish":
        supports_below = sorted([s for s in sr["supports"] if s < close], reverse=True)
        resistances_above = sorted([r for r in sr["resistances"] if r > close])
        if not supports_below or not resistances_above:
            return {"should_alert": False, "symbol": symbol, "reason": "S/R tidak cukup untuk hitung RR"}
        tp = supports_below[0]
        sl = resistances_above[0]
        rr = (close - tp) / (sl - close) if (sl - close) != 0 else 0
    else:
        return {"should_alert": False, "symbol": symbol, "reason": "Bias netral"}

    if rr < SCAN_MIN_RR:
        return {"should_alert": False, "symbol": symbol, "reason": f"RR {rr:.2f} < minimum {SCAN_MIN_RR}"}

    # ── Candlestick pattern confirmation (informational, BUKAN hard gate) ──
    # Auto-signal ini masih pakai entry=current close (bukan structural
    # trigger — lihat audit Level 2), jadi pola candlestick di lokasi S/R
    # kuat dipakai sebagai indikasi KUALITAS timing entry yang bisa dinilai
    # user sendiri, bukan otomatis memblokir/meloloskan sinyal. Belum ada
    # data backtest yang membuktikan gating berdasarkan pola ini
    # memperbaiki win rate, jadi tidak diklaim sebagai itu.
    pattern_info = detect_candlestick_pattern(df)
    direction_lower = "bullish" if direction == "Bullish" else "bearish"
    matching_zone = None
    if pattern_info["direction"] == direction_lower:
        matching_zone = find_matching_sr_zone(close, sr, direction_lower)

    if pattern_info["pattern"] and matching_zone:
        candlestick_confirmation = (
            f"✅ {pattern_info['pattern']} terkonfirmasi di level {'support' if direction_lower == 'bullish' else 'resistance'} "
            f"kuat ({matching_zone['touches']}x touch) — selaras dengan bias {direction}"
        )
    elif pattern_info["pattern"]:
        candlestick_confirmation = (
            f"ℹ️ {pattern_info['pattern']} terdeteksi tapi TIDAK di lokasi S/R kuat yang searah — "
            f"konfirmasi lemah, entry murni dari Technical Score"
        )
    else:
        candlestick_confirmation = "⚠️ Tidak ada pola candlestick jelas di candle terakhir — entry murni dari Technical Score"

    return {
        "should_alert": True,
        "symbol": symbol,
        "interval": interval,
        "direction": direction,
        "regime": regime,
        "confidence": conf["confidence"],
        "entry": round(close, 5),
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "rr": round(rr, 2),
        "trap_state": trap_result["state"],
        "trap_note": trap if trap_result["state"] == "POTENTIAL" else None,
        "candlestick_confirmation": candlestick_confirmation,
    }


def get_pending_scan_signals():
    """Jalankan generate_scan_signal untuk tiap (user, symbol) di watchlist,
    kirim maksimal 1x per hari SELAMA arah sinyalnya (bullish/bearish) sama.
    Kalau arahnya berubah, atau sudah ganti hari, boleh alert lagi — supaya
    tidak spam walau entry/SL/TP bergeser tiap kali di-scan ulang. TIDAK
    menandai terkirim di sini — panggil mark_scan_signal_sent() setelah
    broadcast selesai dicoba."""
    rows = get_all_watchlist_rows()
    if not rows:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    signal_cache = {}
    due = []
    conn = get_db()
    try:
        for row in rows:
            symbol = row["symbol"]
            if symbol not in signal_cache:
                # XAUUSD memakai entry engine baru M15 + H4 + fundamental.
                # Pair lain tetap memakai scanner legacy sampai engine khususnya
                # dibuat, supaya watchlist forex yang sudah ada tidak berubah
                # diam-diam.
                if symbol.replace("/", "").upper() == "XAUUSD":
                    signal_cache[symbol] = generate_xau_entry_signal()
                else:
                    signal_cache[symbol] = generate_scan_signal(symbol)

            sig = signal_cache[symbol]
            if not sig.get("should_alert"):
                continue

            # Dedup HANYA berdasarkan arah (bullish/bearish), bukan entry/SL/TP —
            # supaya pergeseran harga kecil antar-scan tidak dianggap sinyal baru.
            signal_hash = sig["direction"]
            already_sent = conn.execute(
                "SELECT 1 FROM sent_scan_signals WHERE user_id=? AND symbol=? "
                "AND alert_date=? AND signal_hash=? AND platform=?",
                (row["user_id"], symbol, today, signal_hash, row["platform"]),
            ).fetchone()
            if already_sent:
                continue

            due.append({
                "platform": row["platform"],
                "user_id": row["user_id"],
                "chat_id": row["chat_id"],
                "signal": sig,
                "_dedup_key": (row["user_id"], symbol, today, signal_hash, row["platform"]),
            })
    finally:
        conn.close()

    return due


def mark_scan_signal_sent(dedup_key):
    """Panggil SETELAH scan signal selesai dicoba dikirim (bukan sebelum)."""
    user_id, symbol, alert_date, signal_hash, platform = dedup_key
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO sent_scan_signals (user_id, symbol, alert_date, signal_hash, platform) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, symbol, alert_date, signal_hash, platform),
    )
    conn.commit()
    conn.close()


# ── Fitur Baru: Backtest generate_scan_signal ────────────────────
# Langkah pertama menuju validasi statistik (bukan cuma "kelihatan masuk
# akal secara teknikal") — audit sebelumnya berkali-kali menegaskan
# generate_scan_signal() belum pernah divalidasi backtest. Modul ini
# menjalankan LOGIC YANG SAMA PERSIS (_scan_signal_from_df, dipakai juga
# oleh live scan) terhadap data historis, TANPA lookahead bias: di tiap
# titik simulasi cuma dikasih data SAMPAI titik itu, gak ada data masa
# depan yang bocor ke perhitungan indikator/keputusan.


def backtest_scan_signal(symbol: str, interval: str = "1h", outputsize: int = 500,
                          window_size: int = 100, cooldown_bars: int = 5) -> dict:
    """Backtest generate_scan_signal (_scan_signal_from_df) terhadap
    outputsize candle historis terakhir.

    Cara kerja tiap titik simulasi i (mulai dari window_size):
    1. Ambil window = df[i-window_size+1 : i+1] -- ukuran TETAP (sama
       persis dengan default live scan, yang selalu fetch outputsize=100
       candle terakhir, BUKAN seluruh histori sejak awal). Ini penting
       supaya backtest benar-benar meniru kondisi bot waktu jalan live —
       bot live juga cuma "lihat" 100 candle terakhir tiap kali cek, tidak
       pernah punya akses ke histori yang lebih panjang dari itu.
    2. Kalau tidak ada trade terbuka, jalankan _scan_signal_from_df(window).
       Kalau should_alert True, buka trade hipotetis (entry/SL/TP dicatat).
    3. Kalau ada trade terbuka, cek candle SEKARANG (index i) apakah
       high/low-nya udah nyentuh SL atau TP. Candle yang sama kena
       keduanya dianggap SL duluan (asumsi konservatif, karena kita gak
       tau urutan sebenarnya dalam candle itu).
    4. Sesudah trade ditutup (menang/kalah), tunggu cooldown_bars candle
       sebelum boleh buka trade baru lagi (mencegah re-entry beruntun di
       kondisi yang secara teknis masih terus "should_alert" tiap candle).

    Return metrik: total_signals, wins, losses, win_rate, profit_factor,
    expectancy_r (rata-rata R per trade), max_drawdown_r (dalam satuan R,
    bukan uang), avg_rr_target, trades (detail tiap trade).

    CATATAN PENTING (baca sebelum percaya hasilnya):
    - RR yang dipakai buat hitung expectancy adalah RR TARGET saat sinyal
      dibuka (tp/sl waktu itu), bukan RR realized sebenarnya, karena TP
      dianggap tercapai penuh saat high/low menyentuhnya (idealisasi -
      slippage, spread, dan partial fill TIDAK dimodelkan).
    - Data historis dari TwelveData API (limit outputsize tergantung
      paket akun) -- backtest ini HANYA sekuat data yang tersedia, bukan
      multi-tahun kalau akunnya limited.
    - Ini backtest IN-SAMPLE sederhana (bukan walk-forward/out-of-sample
      split) -- hasil bagus di sini BUKAN jaminan performa live, cuma
      langkah pertama buat tau apakah rule sekarang punya edge dasar sama
      sekali atau enggak."""
    df, err = _ohlcv_or_error(symbol, interval, outputsize=outputsize)
    if err:
        return {"error": err}
    if len(df) < window_size + 20:
        return {"error": f"Data historis kurang (dapat {len(df)} candle, butuh minimal {window_size + 20})"}

    df = df.reset_index(drop=True)
    trades = []
    open_trade = None
    cooldown_until = -1

    for i in range(window_size, len(df)):
        window = df.iloc[max(0, i - window_size + 1): i + 1]

        if open_trade is None:
            if i < cooldown_until:
                continue
            try:
                sig = _scan_signal_from_df(window, symbol, interval)
            except Exception as ex:
                # Data window tertentu bisa aja gagal dihitung (mis. NaN
                # di awal-awal indikator) -- skip titik ini, jangan
                # gagalin seluruh backtest.
                continue
            if sig.get("should_alert"):
                open_trade = {
                    "entry_idx": i, "direction": sig["direction"], "entry": sig["entry"],
                    "sl": sig["sl"], "tp": sig["tp"], "rr_target": sig["rr"],
                }
        else:
            row = df.iloc[i]
            if open_trade["direction"] == "Bullish":
                hit_sl = row["low"] <= open_trade["sl"]
                hit_tp = row["high"] >= open_trade["tp"]
            else:
                hit_sl = row["high"] >= open_trade["sl"]
                hit_tp = row["low"] <= open_trade["tp"]

            outcome = None
            if hit_sl:
                outcome = "loss"  # SL diprioritaskan kalau dua2nya kena di candle sama (konservatif)
            elif hit_tp:
                outcome = "win"

            if outcome:
                r_multiple = open_trade["rr_target"] if outcome == "win" else -1.0
                trades.append({
                    **open_trade, "exit_idx": i, "outcome": outcome, "r_multiple": r_multiple,
                })
                open_trade = None
                cooldown_until = i + cooldown_bars

    if not trades:
        return {
            "symbol": symbol, "interval": interval, "total_signals": 0,
            "note": "Tidak ada sinyal yang muncul sama sekali di rentang data ini "
                    "(kriteria should_alert emang ketat -- ini bukan error).",
        }

    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    total = len(trades)
    win_rate = len(wins) / total * 100

    gross_win_r = sum(t["r_multiple"] for t in wins)
    gross_loss_r = abs(sum(t["r_multiple"] for t in losses))
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else float("inf") if gross_win_r > 0 else 0
    expectancy_r = sum(t["r_multiple"] for t in trades) / total

    # Max drawdown dalam satuan R (equity curve kumulatif dari urutan trade)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t["r_multiple"]
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    avg_rr_target = sum(t["rr_target"] for t in trades) / total

    return {
        "symbol": symbol,
        "interval": interval,
        "candles_used": len(df),
        "total_signals": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "expectancy_r": round(expectancy_r, 3),
        "max_drawdown_r": round(max_dd, 2),
        "avg_rr_target": round(avg_rr_target, 2),
        "trades": trades,
    }


# ── Fitur Baru: Custom Price Alert ──────────────────────────────

VALID_OPERATORS = {">", "<", ">=", "<="}


def add_price_alert(user_id: int, chat_id: int, symbol: str, operator: str, target_price: float,
                     platform: str = "telegram") -> dict:
    if not math.isfinite(target_price) or target_price <= 0 or target_price > 1e9:
        return {"error": "Harga target harus angka positif dan terbatas."}
    if operator not in VALID_OPERATORS:
        return {"error": f"Operator harus salah satu dari: {', '.join(sorted(VALID_OPERATORS))}"}
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO price_alerts (user_id, chat_id, symbol, operator, target_price, platform) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, chat_id, symbol, operator, target_price, platform),
    )
    conn.commit()
    alert_id = cur.lastrowid
    conn.close()
    return {"id": alert_id, "symbol": symbol, "operator": operator, "target_price": target_price}


def list_price_alerts(user_id: int, platform: str = "telegram"):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, symbol, operator, target_price FROM price_alerts WHERE user_id=? AND platform=? ORDER BY id",
            (user_id, platform),
        ).fetchall()
    finally:
        conn.close()
    return rows


def remove_price_alert(user_id: int, alert_id: int, platform: str = "telegram") -> bool:
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM price_alerts WHERE id=? AND user_id=? AND platform=?",
        (alert_id, user_id, platform),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_pending_price_alerts():
    """Cek semua price alert aktif, kembalikan yang sudah tercapai targetnya.
    TIDAK menghapus row di sini — sebelumnya alert langsung dihapus begitu
    target tercapai, SEBELUM delivery ke user dipastikan berhasil. Kalau
    pengiriman gagal (bot down, network error, dst), alert itu hilang
    permanen padahal user belum pernah dikasih tau. Sekarang panggil
    mark_price_alert_sent(alert_id) setelah broadcast selesai dicoba,
    baru row-nya dihapus."""
    conn = get_db()
    try:
        alerts = conn.execute("SELECT * FROM price_alerts").fetchall()
    finally:
        conn.close()

    if not alerts:
        return []

    price_cache = {}
    due = []

    for a in alerts:
        symbol = a["symbol"]
        if symbol not in price_cache:
            result = get_forex_price(symbol)
            price_cache[symbol] = float(result["price"]) if "price" in result else None

        price = price_cache[symbol]
        if price is None:
            continue

        op = a["operator"]
        target = a["target_price"]
        triggered = (
            (op == ">" and price > target) or
            (op == "<" and price < target) or
            (op == ">=" and price >= target) or
            (op == "<=" and price <= target)
        )
        if triggered:
            due.append({
                "platform": a["platform"],
                "chat_id": a["chat_id"],
                "symbol": symbol,
                "operator": op,
                "target_price": target,
                "current_price": price,
                "_dedup_key": a["id"],
            })

    return due


def mark_price_alert_sent(alert_id: int):
    """Panggil SETELAH price alert selesai dicoba dikirim (bukan sebelum) —
    baru row-nya dihapus dari DB (alert ini one-shot)."""
    conn = get_db()
    conn.execute("DELETE FROM price_alerts WHERE id=?", (alert_id,))
    conn.commit()
    conn.close()


# ── Fitur Baru: Volatility Spike Alert ──────────────────────────

VOLATILITY_SPIKE_MULTIPLIER = 1.5


def get_pending_volatility_spikes():
    """Cek ATR saat ini vs ATR ~20 candle lalu untuk tiap symbol di watchlist.
    Kalau melonjak >= VOLATILITY_SPIKE_MULTIPLIER kali, kirim alert (maks
    1x/hari). TIDAK menandai terkirim di sini — panggil
    mark_volatility_alert_sent() setelah broadcast selesai dicoba."""
    rows = get_all_watchlist_rows()
    if not rows:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    atr_cache = {}
    due = []
    conn = get_db()
    try:
        for row in rows:
            symbol = row["symbol"]
            if symbol not in atr_cache:
                df, err = _ohlcv_or_error(symbol, "1h")
                if err:
                    atr_cache[symbol] = None
                    continue
                indicators = calculate_indicators(df)
                atr_cache[symbol] = indicators

            indicators = atr_cache[symbol]
            if not indicators or indicators["atr_20_ago"] <= 0:
                continue

            ratio = indicators["atr"] / indicators["atr_20_ago"]
            if ratio < VOLATILITY_SPIKE_MULTIPLIER:
                continue

            already_sent = conn.execute(
                "SELECT 1 FROM sent_volatility_alerts WHERE user_id=? AND symbol=? "
                "AND alert_date=? AND platform=?",
                (row["user_id"], symbol, today, row["platform"]),
            ).fetchone()
            if already_sent:
                continue

            due.append({
                "platform": row["platform"],
                "chat_id": row["chat_id"],
                "symbol": symbol,
                "atr_now": round(indicators["atr"], 5),
                "atr_before": round(indicators["atr_20_ago"], 5),
                "ratio": round(ratio, 2),
                "_dedup_key": (row["user_id"], symbol, today, row["platform"]),
            })
    finally:
        conn.close()

    return due


def mark_volatility_alert_sent(dedup_key):
    """Panggil SETELAH volatility alert selesai dicoba dikirim (bukan sebelum)."""
    user_id, symbol, alert_date, platform = dedup_key
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO sent_volatility_alerts (user_id, symbol, alert_date, platform) VALUES (?, ?, ?, ?)",
        (user_id, symbol, alert_date, platform),
    )
    conn.commit()
    conn.close()


# ── Fitur Baru: Session Reminder ────────────────────────────────
# Pakai slot waktu yang sama dengan macro briefing (buka sesi Asia/London/NY, WIB).

SESSION_SLOTS = [
    (7, 0, "🌏 Sesi Asia (Tokyo) baru saja dibuka"),
    (14, 0, "🇬🇧 Sesi London baru saja dibuka"),
    (19, 30, "🇺🇸 Sesi New York baru saja dibuka"),
]


def add_session_sub(user_id: int, chat_id: int, platform: str = "telegram"):
    conn = get_db()
    conn.execute(
        "INSERT INTO session_subs (user_id, chat_id, platform) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, platform) DO UPDATE SET chat_id=excluded.chat_id",
        (user_id, chat_id, platform),
    )
    conn.commit()
    conn.close()


def remove_session_sub(user_id: int, platform: str = "telegram"):
    conn = get_db()
    conn.execute("DELETE FROM session_subs WHERE user_id=? AND platform=?", (user_id, platform))
    conn.commit()
    conn.close()


def get_session_subs():
    conn = get_db()
    try:
        rows = conn.execute("SELECT user_id, chat_id, platform FROM session_subs").fetchall()
    finally:
        conn.close()
    return rows


# ── Fitur Baru: Daily Market Debrief ────────────────────────────


def add_debrief_sub(user_id: int, chat_id: int, platform: str = "telegram"):
    conn = get_db()
    conn.execute(
        "INSERT INTO debrief_subs (user_id, chat_id, platform) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, platform) DO UPDATE SET chat_id=excluded.chat_id",
        (user_id, chat_id, platform),
    )
    conn.commit()
    conn.close()


def remove_debrief_sub(user_id: int, platform: str = "telegram"):
    conn = get_db()
    conn.execute("DELETE FROM debrief_subs WHERE user_id=? AND platform=?", (user_id, platform))
    conn.commit()
    conn.close()


def get_debrief_subs():
    conn = get_db()
    try:
        rows = conn.execute("SELECT user_id, chat_id, platform FROM debrief_subs").fetchall()
    finally:
        conn.close()
    return rows


DAILY_DEBRIEF_PAIRS = ["XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY"]


def generate_daily_debrief() -> str:
    """Update harian komprehensif: ringkasan tiap pair utama (regime, bias,
    level S/R kunci) + event kalender USD High Impact hari ini + berita
    penting. Ini laporan gabungan DATA REAL yang bot ini punya (bukan AI
    ngarang cerita) — Technical Score & level S/R yang disebut dihitung
    langsung dari indikator real-time, entry/SL/TP (kalau disebut) HARUS
    berasal dari level yang diberikan, bukan angka karangan AI."""
    def summarize_pair(symbol):
        df, err = _ohlcv_or_error(symbol, "1h")
        if err:
            return f"- {symbol}: data tidak tersedia ({err})"
        indicators = calculate_indicators(df)
        conf = calculate_confidence(indicators)
        regime = get_regime(indicators)
        sr = get_support_resistance(df)
        nearest_support = sr["supports"][0] if sr["supports"] else "N/A"
        nearest_resistance = sr["resistances"][0] if sr["resistances"] else "N/A"
        return (
            f"- {symbol}: harga {indicators['close']} | Regime {regime} | Bias {conf['bias_direction']} "
            f"(Technical Score {conf['confidence']}%, BUKAN probabilitas) | "
            f"Support terdekat: {nearest_support} | Resistance terdekat: {nearest_resistance}"
        )

    with ThreadPoolExecutor(max_workers=len(DAILY_DEBRIEF_PAIRS)) as pool:
        pair_summaries = list(pool.map(summarize_pair, DAILY_DEBRIEF_PAIRS))
    pair_text = "\n".join(pair_summaries)

    events_today = get_calendar(filter_country="USD", only_today=True)
    if events_today is None:
        events_text = "Data kalender tidak tersedia saat ini (gagal fetch)."
    else:
        high_events_today = [e for e in events_today if e.get("impact") == "High"]
        if high_events_today:
            events_text = "\n".join(
                f"- {e['title']} ({e['time']}) — Forecast: {e['forecast']}, Previous: {e['previous']}"
                for e in high_events_today
            )
        else:
            events_text = "Tidak ada event USD High Impact terjadwal hari ini."

    news_text = search_news("gold forex market today recap")

    prompt = (
        f"Buat 'Update Harian' pasar forex/emas berdasarkan DATA REAL berikut. JANGAN mengarang "
        f"angka harga/level di luar yang diberikan.\n\n"
        f"Ringkasan teknikal per pair (dari indikator real-time):\n{pair_text}\n\n"
        f"Event ekonomi USD High Impact hari ini:\n{events_text}\n\n"
        f"Berita terkait:\n{news_text}\n\n"
        f"Format laporan (maks 12 kalimat total):\n"
        f"1. Ringkasan kondisi pasar hari ini (1-2 kalimat)\n"
        f"2. Level kunci & bias tiap pair yang dikasih di atas (rujuk PERSIS dari Technical Score "
        f"dan level S/R yang diberikan — JANGAN klaim probabilitas atau kepastian arah)\n"
        f"3. Event penting yang perlu diwaspadai hari ini (kalau ada)\n"
        f"4. Satu catatan risiko/hal yang perlu diperhatikan\n\n"
        f"PENTING: kalau kamu menyebut level entry/SL/TP apapun, itu WAJIB persis dari level S/R "
        f"yang diberikan di atas, JANGAN mengarang angka baru. Selalu jelas ini pembacaan Technical "
        f"Score dari data yang tersedia, bukan sinyal/prediksi yang pasti terjadi."
    )
    return analyze_with_groq(prompt)


def header(title: str) -> str:
    """Header versi Telegram (Markdown legacy: *bold*)."""
    return f"*Bayproject.fx* — {title}\n" f"{'─' * 24}\n"


FOOTER = "\n\n_⚠️ Not Financial Advice. DYOR._"


# ── Twelve Data: Price & OHLCV ───────────────────────────────


def format_symbol(raw: str) -> str:
    """Convert EURUSD → EUR/USD. Already-slashed symbols pass through."""
    raw = raw.upper().strip()
    if "/" in raw:
        return raw
    if len(raw) == 6:
        return f"{raw[:3]}/{raw[3:]}"
    return raw


# ── biquote.io: sumber data alternatif (no-key, limit jauh lebih longgar) ──
# Dicoba SEBELUM TwelveData di tiap fungsi fetch harga/OHLC/kalender.
# Kalau biquote gagal/berubah/down, otomatis fallback ke TwelveData/Forex
# Factory seperti sebelumnya -- TIDAK ada perubahan behavior kalau biquote
# bermasalah, cuma tambahan jalur yang dicoba duluan.

BIQUOTE_BASE = "https://biquote.io"
BIQUOTE_INTERVAL_MAP = {
    "1min": "1m", "5min": "5m", "15min": "15m", "1h": "1h", "4h": "4h", "1day": "1d",
}


def _to_biquote_symbol(symbol: str) -> str:
    """XAU/USD -> XAUUSD (biquote gak pakai slash pemisah)."""
    return symbol.replace("/", "").upper()


def _get_biquote_price(symbol: str) -> dict:
    """Coba ambil harga dari biquote.io. Return {'price','symbol','source'}
    atau {'error': str} -- caller yang decide fallback ke TwelveData."""
    bq_symbol = _to_biquote_symbol(symbol)
    try:
        resp = requests.get(f"{BIQUOTE_BASE}/api/{bq_symbol}", timeout=8)
        if resp.status_code != 200:
            return {"error": f"biquote status {resp.status_code}"}
        data = resp.json()
        if "mid" not in data:
            return {"error": "biquote: field 'mid' tidak ada di response"}
        return {"price": str(data["mid"]), "symbol": symbol, "source": "biquote"}
    except Exception as e:
        return {"error": str(e)}


def _get_biquote_ohlcv(symbol: str, interval: str, outputsize: int):
    """Coba ambil OHLC dari biquote.io. Return DataFrame (dengan kolom
    tambahan _is_open kalau biquote eksplisit kasih tau candle mana yang
    masih berjalan) atau {'error': str}."""
    bq_interval = BIQUOTE_INTERVAL_MAP.get(interval)
    if bq_interval is None:
        return {"error": f"Interval {interval} tidak dipetakan ke biquote"}
    bq_symbol = _to_biquote_symbol(symbol)
    try:
        resp = requests.get(
            f"{BIQUOTE_BASE}/api/{bq_symbol}/ohlc",
            params={"interval": bq_interval, "limit": min(max(outputsize, 1), 2000)},
            timeout=10,
        )
        if resp.status_code != 200:
            return {"error": f"biquote status {resp.status_code}"}
        payload = resp.json()
        bars = payload.get("bars", [])
        if not bars:
            return {"error": "biquote: tidak ada data bar"}

        df = pd.DataFrame(bars)
        if "openTime" in df.columns:
            df = df.rename(columns={"openTime": "datetime"})
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = 0.0
        if "isOpen" in df.columns:
            df["_is_open"] = df["isOpen"].astype(bool)
        # biquote kasih bar terbaru duluan -- urutkan ascending (tua->baru)
        # supaya konsisten dengan konvensi dipakai di seluruh kode (iloc[-1]
        # = candle paling baru).
        df = df.sort_values("datetime").reset_index(drop=True)
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        return df
    except Exception as e:
        return {"error": str(e)}


def get_forex_price(symbol: str) -> dict:
    """Return {'price': str, 'symbol': str} or {'error': str}. Coba
    biquote.io dulu (15rb request/menit, no-key), fallback ke TwelveData
    kalau gagal."""
    bq_result = _get_biquote_price(symbol)
    if "price" in bq_result:
        return bq_result

    print(f"biquote gagal untuk harga {symbol} ({bq_result.get('error')}), fallback ke TwelveData")
    try:
        _wait_for_td_rate_limit_slot()
        resp = requests.get(
            f"{TWELVE_DATA_BASE}/price",
            params={"symbol": symbol, "apikey": TWELVE_DATA_API_KEY},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") == "error" or "price" not in data:
            return {"error": data.get("message", "Simbol tidak ditemukan")}
        return {"price": data["price"], "symbol": symbol, "source": "twelvedata"}
    except Exception as e:
        return {"error": str(e)}


VALID_INTERVALS = {"1min", "5min", "15min", "1h", "4h", "1day"}


def get_ohlcv(symbol: str, interval: str = "1h", outputsize: int = 100):
    """Return a pandas DataFrame with open/high/low/close/volume/datetime, or {'error': str}.
    Coba biquote.io dulu, fallback ke TwelveData kalau gagal.

    timezone=UTC dipaksa eksplisit di request TwelveData (bukan default
    TwelveData yang "Exchange" dan bervariasi per instrumen) supaya kolom
    datetime bisa dibandingkan dengan aman terhadap waktu sekarang (lihat
    _drop_unclosed_candle). biquote.io sendiri sudah selalu UTC."""
    if interval not in VALID_INTERVALS:
        return {"error": f"Interval tidak valid. Pilih: {', '.join(sorted(VALID_INTERVALS))}"}

    bq_result = _get_biquote_ohlcv(symbol, interval, outputsize)
    if isinstance(bq_result, pd.DataFrame):
        return bq_result
    print(f"biquote gagal untuk OHLC {symbol} {interval} ({bq_result.get('error')}), fallback ke TwelveData")

    try:
        _wait_for_td_rate_limit_slot()
        resp = requests.get(
            f"{TWELVE_DATA_BASE}/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "timezone": "UTC",
                "apikey": TWELVE_DATA_API_KEY,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("status") == "error" or "values" not in data:
            return {"error": data.get("message", "Data tidak tersedia")}
        rows = list(reversed(data["values"]))
        df = pd.DataFrame(rows)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = 0.0
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        return df
    except Exception as e:
        return {"error": str(e)}


INTERVAL_SECONDS = {
    "1min": 60, "5min": 300, "15min": 900,
    "1h": 3600, "4h": 14400, "1day": 86400,
}


def _drop_unclosed_candle(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Buang candle terakhir dari df kalau candle itu belum closed (masih
    'live'/sedang berjalan), supaya sinyal/indikator tidak dihitung dari
    data yang masih bisa berubah (repaint). Lihat temuan P0 'closed candle'
    di audit — .iloc[-1] sebelumnya dipakai apa adanya tanpa cek ini.

    Kalau df berasal dari biquote.io (ada kolom _is_open), PAKAI INFO ITU
    LANGSUNG — biquote eksplisit kasih tau candle mana yang masih berjalan,
    jauh lebih akurat daripada nebak dari timestamp+durasi. Kalau df dari
    TwelveData (gak ada _is_open), fallback ke heuristik lama: bandingkan
    waktu tutup candle terakhir (open_time + durasi interval) terhadap
    waktu sekarang (UTC)."""
    if df is None or len(df) < 2:
        return df

    if "_is_open" in df.columns:
        is_last_open = bool(df["_is_open"].iloc[-1])
        result = df.drop(columns=["_is_open"])
        if is_last_open:
            return result.iloc[:-1].reset_index(drop=True)
        return result

    if "datetime" not in df.columns:
        return df

    last_dt = df["datetime"].iloc[-1]
    if pd.isna(last_dt):
        return df

    duration = INTERVAL_SECONDS.get(interval)
    if duration is None:
        return df

    now_utc = datetime.now(ZoneInfo("UTC"))
    if last_dt.tzinfo is None:
        last_dt = last_dt.tz_localize("UTC")
    candle_close_time = last_dt + pd.Timedelta(seconds=duration)

    if candle_close_time > now_utc:
        # Candle terakhir belum closed — buang, pakai candle sebelumnya
        # sebagai "current" yang valid buat sinyal/indikator.
        return df.iloc[:-1].reset_index(drop=True)
    return df


def _ohlcv_or_error(symbol: str, interval: str, outputsize: int = 100):
    result = get_ohlcv(symbol, interval, outputsize)
    if isinstance(result, dict):
        return None, result.get("error", "Data tidak tersedia")
    result = _drop_unclosed_candle(result, interval)
    if len(result) < 30:
        return None, "Data candle tidak cukup untuk analisis (minimal 30 candle)."
    return result, None


# ── Technical Indicators (manual pandas implementation) ──────


def _true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low = df["high"], df["low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )
    atr = _atr(df, period)
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 1e-10))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 1e-10))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _bollinger(close: pd.Series, period=20, std_mult=2):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return sma, sma + std_mult * std, sma - std_mult * std


def calculate_indicators(df: pd.DataFrame) -> dict:
    close, high, low, open_, volume = df["close"], df["high"], df["low"], df["open"], df["volume"]

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    rsi = _rsi(close, 14)
    atr = _atr(df, 14)
    adx = _adx(df, 14)
    macd_line, signal_line = _macd(close)
    bb_mid, bb_upper, bb_lower = _bollinger(close)
    vol_sma20 = volume.rolling(20).mean()

    idx = len(df) - 1
    atr_ago_idx = max(0, idx - 20)

    def safe(series, i=-1, default=0.0):
        val = series.iloc[i]
        return float(val) if pd.notna(val) else default

    return {
        "datetime": df["datetime"].iloc[-1] if "datetime" in df.columns else "",
        "close": safe(close),
        "open": safe(open_),
        "high": safe(high),
        "low": safe(low),
        "ema20": safe(ema20),
        "ema50": safe(ema50),
        "ema200": safe(ema200),
        "rsi": safe(rsi, default=50.0),
        "atr": safe(atr),
        "atr_20_ago": safe(atr, i=atr_ago_idx),
        "adx": safe(adx),
        "macd": safe(macd_line),
        "macd_signal": safe(signal_line),
        "bb_upper": safe(bb_upper, default=safe(close)),
        "bb_middle": safe(bb_mid, default=safe(close)),
        "bb_lower": safe(bb_lower, default=safe(close)),
        "volume": safe(volume),
        "volume_sma20": safe(vol_sma20, default=safe(volume)),
    }


def calculate_confidence(indicators: dict) -> dict:
    """Technical Score (BUKAN probabilitas kemenangan trade) — vote count dari
    beberapa kelompok faktor teknikal yang searah.

    Refactor dari versi lama: sebelumnya EMA Stack, ADX Strength, Bollinger
    Middle, dan Close vs EMA200 masing-masing dihitung sebagai vote TERPISAH
    padahal keempatnya pada dasarnya mengukur hal yang sama (trend/posisi
    price vs moving average) — market yang trending kuat bisa centang
    keempatnya sekaligus dan bikin score kelihatan tinggi padahal cuma 1
    kondisi trend yang dihitung berkali-kali (audit finding P1: double
    counting). Sekarang keempatnya digabung jadi SATU kelompok Trend.

    Faktor ATR ("Volatilitas Terkontrol") di versi lama ikut menambah skor
    ke arah manapun yang SUDAH unggul — ini self-reinforcing/confirmation
    bias, bukan informasi independen (audit finding P1). Sekarang ATR
    murni informational (regime volatilitas), tidak lagi menyumbang skor.

    Bobot baru: Trend=3, Momentum=2, Volume/Activity=1, Structure=1
    (total maksimum 7 per arah), supaya kelompok yang secara konsep lebih
    kuat (trend) tidak encer jadi hanya 1/10 seperti sebelumnya, sekaligus
    tidak diinflasi jadi 4/10 dari faktor yang sebenarnya sama."""
    ema20, ema50, ema200 = indicators["ema20"], indicators["ema50"], indicators["ema200"]
    adx, rsi = indicators["adx"], indicators["rsi"]
    macd, macd_signal = indicators["macd"], indicators["macd_signal"]
    close, open_ = indicators["close"], indicators["open"]
    bb_middle = indicators["bb_middle"]
    volume, volume_sma20 = indicators["volume"], indicators["volume_sma20"]
    atr, atr_20_ago = indicators["atr"], indicators["atr_20_ago"]

    bullish_score = 0
    bearish_score = 0
    detail = []

    # ── Kelompok TREND (bobot 3) — konsensus dari 4 sub-sinyal yang secara
    # konsep sama-sama mengukur "posisi price vs moving average", jadi
    # digabung jadi SATU vote kelompok, bukan 4 vote terpisah.
    trend_bull_votes = sum([
        ema20 > ema50 > ema200,
        adx > 25 and ema20 > ema50,
        close > bb_middle,
        close > ema200,
    ])
    trend_bear_votes = sum([
        ema20 < ema50 < ema200,
        adx > 25 and ema20 < ema50,
        close <= bb_middle,
        close <= ema200,
    ])
    if trend_bull_votes >= 3:
        bullish_score += 3
        detail.append({"name": "Trend (EMA/ADX/BB/EMA200)", "direction": "bullish",
                        "note": f"{trend_bull_votes}/4 sub-sinyal trend searah bullish"})
    elif trend_bear_votes >= 3:
        bearish_score += 3
        detail.append({"name": "Trend (EMA/ADX/BB/EMA200)", "direction": "bearish",
                        "note": f"{trend_bear_votes}/4 sub-sinyal trend searah bearish"})
    else:
        detail.append({"name": "Trend (EMA/ADX/BB/EMA200)", "direction": "netral",
                        "note": f"Trend campuran ({trend_bull_votes} bullish / {trend_bear_votes} bearish dari 4 sub-sinyal) — tidak ada konsensus kuat"})

    # RSI Zone: tetap informational-only (tidak menyumbang skor).
    detail.append({"name": "RSI Zone", "direction": "netral",
                    "note": f"RSI {rsi:.2f} {'berada di zona netral (40-60)' if 40 <= rsi <= 60 else 'di luar zona netral'}"})

    # ── Kelompok MOMENTUM (bobot 2) — RSI momentum + MACD. Digabung
    # (bukan dihilangkan) karena RSI momentum & MACD sama-sama mengukur
    # kecepatan/kekuatan pergerakan harga, walau dari perhitungan beda,
    # jadi kalau keduanya sepakat itu sinyal momentum yang lebih meyakinkan
    # daripada 2 vote independen yang sebenarnya berkorelasi.
    rsi_dir = "bullish" if rsi > 60 else "bearish" if rsi < 40 else "netral"
    macd_dir = "bullish" if macd > macd_signal else "bearish"
    if rsi_dir == "bullish" and macd_dir == "bullish":
        bullish_score += 2
        detail.append({"name": "Momentum (RSI+MACD)", "direction": "bullish",
                        "note": f"RSI {rsi:.2f} > 60 DAN MACD({macd:.5f}) > Signal({macd_signal:.5f}) — sepakat"})
    elif rsi_dir == "bearish" and macd_dir == "bearish":
        bearish_score += 2
        detail.append({"name": "Momentum (RSI+MACD)", "direction": "bearish",
                        "note": f"RSI {rsi:.2f} < 40 DAN MACD({macd:.5f}) <= Signal({macd_signal:.5f}) — sepakat"})
    elif macd_dir == "bullish":
        bullish_score += 1
        detail.append({"name": "Momentum (RSI+MACD)", "direction": "bullish",
                        "note": f"MACD bullish tapi RSI ({rsi:.2f}) belum konfirmasi kuat — momentum parsial"})
    else:
        bearish_score += 1
        detail.append({"name": "Momentum (RSI+MACD)", "direction": "bearish",
                        "note": f"MACD bearish tapi RSI ({rsi:.2f}) belum konfirmasi kuat — momentum parsial"})

    # ── Volatilitas (ATR): informational-only, TIDAK lagi menyumbang skor
    # (sebelumnya self-reinforcing — otomatis nambah skor ke arah yang
    # SUDAH unggul, bukan informasi independen).
    vol_regime = "compression (menyempit)" if atr < atr_20_ago else "expansion (melebar)"
    detail.append({"name": "Volatilitas (ATR)", "direction": "netral",
                    "note": f"ATR({atr:.5f}) vs ATR 20 periode lalu({atr_20_ago:.5f}) — regime: {vol_regime}. "
                            f"Informational only, bukan vote arah."})

    # ── Kelompok VOLUME/ACTIVITY (bobot 1). Relabel eksplisit: forex/CFD
    # tidak punya centralized market volume, ini tick/activity volume dari
    # provider — bukan representasi volume pasar global (audit P1).
    candle_bullish = close > open_
    if volume > 0 and volume > volume_sma20:
        if candle_bullish:
            bullish_score += 1
            detail.append({"name": "Activity/Tick-Volume", "direction": "bullish",
                            "note": f"Activity({volume:.0f}) > SMA20({volume_sma20:.0f}), candle bullish. "
                                    f"Provider-specific, BUKAN volume pasar tersentralisasi."})
        else:
            bearish_score += 1
            detail.append({"name": "Activity/Tick-Volume", "direction": "bearish",
                            "note": f"Activity({volume:.0f}) > SMA20({volume_sma20:.0f}), candle bearish. "
                                    f"Provider-specific, BUKAN volume pasar tersentralisasi."})
    else:
        detail.append({"name": "Activity/Tick-Volume", "direction": "netral",
                        "note": "Activity tidak signifikan/tidak tersedia"})

    # ── Kelompok STRUCTURE (bobot 1) — candle terakhir. Masih raw (belum
    # displacement-candle detection dengan body/range/close-location seperti
    # direkomendasikan audit Level 2) — perbaikan itu didokumentasikan
    # sebagai item terpisah, bukan di-skip diam-diam.
    if candle_bullish:
        bullish_score += 1
        detail.append({"name": "Structure (Candle Terakhir)", "direction": "bullish",
                        "note": f"Close({close}) > Open({open_}). Catatan: masih candle mentah, "
                                f"belum displacement-candle detection (body/range/close-location)."})
    else:
        bearish_score += 1
        detail.append({"name": "Structure (Candle Terakhir)", "direction": "bearish",
                        "note": f"Close({close}) <= Open({open_}). Catatan: masih candle mentah, "
                                f"belum displacement-candle detection (body/range/close-location)."})

    max_score = 7  # 3 (trend) + 2 (momentum, capped) + 1 (volume) + 1 (structure)
    confidence = round((max(bullish_score, bearish_score) / max_score) * 100)
    edge = round((abs(bullish_score - bearish_score) / max_score) * 100)
    if bullish_score > bearish_score:
        bias_direction = "Bullish"
    elif bearish_score > bullish_score:
        bias_direction = "Bearish"
    else:
        bias_direction = "Netral"

    return {
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "max_score": max_score,
        "confidence": confidence,
        "edge": edge,
        "bias_direction": bias_direction,
        "detail_factors": detail,
        "score_label": "Technical Score",
        "score_disclaimer": (
            "Ini Technical Score dari indikator (seberapa banyak kelompok faktor teknikal "
            "searah), BUKAN probabilitas statistik kemenangan trade. Probabilitas hanya "
            "boleh diklaim setelah kalibrasi terhadap hasil historis/backtest."
        ),
    }


def get_regime(indicators: dict) -> str:
    adx, ema20, ema50, close = indicators["adx"], indicators["ema20"], indicators["ema50"], indicators["close"]
    if adx > 25 and ema20 > ema50:
        return "TRENDING UP"
    elif adx > 25 and ema20 < ema50:
        return "TRENDING DOWN"
    elif adx < 20 and close > ema50:
        return "CHOPPY UP"
    elif adx < 20 and close < ema50:
        return "CHOPPY DOWN"
    else:
        return "RANGING"


# ── Fitur Baru: Chart Image (candlestick + EMA + S/R) ────────────


# ── Fitur Baru: Market Structure (HH/HL/LH/LL + Change of Character) ────
# Catatan scope: ini deteksi STRUKTUR SWING POINT dasar (klasifikasi HH/HL/
# LH/LL + CHoCH), BUKAN full ICT Market Structure Engine (belum ada BOS
# lanjutan, order block, FVG, liquidity sweep validation, atau entry
# trigger berbasis struktur ini). Item itu tetap didokumentasikan sebagai
# proyek terpisah yang butuh backtest validation (lihat audit sebelumnya).
# HH/HL/LH/LL/CHoCH murni membantu MEMBACA struktur trend di chart, bukan
# menghasilkan sinyal entry baru.


def detect_market_structure(df: pd.DataFrame, window: int = 5) -> dict:
    """Deteksi swing high/low dan klasifikasi:
    - HH (Higher High): swing high lebih tinggi dari swing high sebelumnya
    - LH (Lower High): swing high lebih rendah dari swing high sebelumnya
    - HL (Higher Low): swing low lebih tinggi dari swing low sebelumnya
    - LL (Lower Low): swing low lebih rendah dari swing low sebelumnya
    - CHoCH (Change of Character): struktur uptrend (rangkaian HH/HL) yang
      barusan close di BAWAH swing low terakhir -> potensi reversal bearish.
      Struktur downtrend (rangkaian LH/LL) yang barusan close di ATAS swing
      high terakhir -> potensi reversal bullish.

    Index swing ('idx') adalah posisi baris di DataFrame yang dikasih
    (0-based) -- kalau dipakai buat anotasi chart, pastikan df yang sama
    persis yang dipakai buat plot (setelah truncate ke n_candles), supaya
    posisinya nyambung ke sumbu-x chart."""
    n = len(df)
    if n < window * 2 + 5:
        return {"swings": [], "trend": None, "choch": None}

    high = df["high"].reset_index(drop=True)
    low = df["low"].reset_index(drop=True)
    close = df["close"].reset_index(drop=True)

    swing_highs, swing_lows = [], []
    for i in range(window, n - window):
        window_high = high.iloc[i - window: i + window + 1]
        window_low = low.iloc[i - window: i + window + 1]
        if high.iloc[i] == window_high.max():
            swing_highs.append((i, float(high.iloc[i])))
        if low.iloc[i] == window_low.min():
            swing_lows.append((i, float(low.iloc[i])))

    labeled = []
    last_high_price = None
    for idx, price in swing_highs:
        label = None
        if last_high_price is not None:
            label = "HH" if price > last_high_price else "LH"
        labeled.append({"idx": idx, "price": price, "type": "high", "label": label})
        last_high_price = price

    last_low_price = None
    for idx, price in swing_lows:
        label = None
        if last_low_price is not None:
            label = "HL" if price > last_low_price else "LL"
        labeled.append({"idx": idx, "price": price, "type": "low", "label": label})
        last_low_price = price

    labeled.sort(key=lambda s: s["idx"])

    # Trend & swing terkonfirmasi terakhir (dari label yg udah jelas HH/HL/LH/LL)
    trend = None
    last_confirmed_low = None
    last_confirmed_high = None
    for s in labeled:
        if s["label"] in ("HH", "HL"):
            trend = "up"
        elif s["label"] in ("LH", "LL"):
            trend = "down"
        if s["type"] == "low" and s["label"]:
            last_confirmed_low = s["price"]
        if s["type"] == "high" and s["label"]:
            last_confirmed_high = s["price"]

    choch = None
    current_close = float(close.iloc[-1])
    if trend == "up" and last_confirmed_low is not None and current_close < last_confirmed_low:
        choch = {"direction": "bearish", "broken_level": last_confirmed_low, "idx": n - 1}
    elif trend == "down" and last_confirmed_high is not None and current_close > last_confirmed_high:
        choch = {"direction": "bullish", "broken_level": last_confirmed_high, "idx": n - 1}

    return {"swings": labeled, "trend": trend, "choch": choch}


def generate_chart_image(df: pd.DataFrame, symbol: str, interval: str, sr: dict = None,
                          n_candles: int = 80, market_structure: dict = None) -> bytes:
    """Render candlestick chart (OHLC + EMA20/50 + level S/R + market
    structure HH/HL/LH/LL/CHoCH) sebagai PNG bytes, dipakai supaya /chart
    bisa kirim visualnya juga, bukan cuma teks. Butuh df dengan kolom
    datetime/open/high/low/close (volume opsional).

    market_structure: hasil dari detect_market_structure() -- KALAU dikasih,
    HARUS dihitung dari df yang SAMA PERSIS sebelum dipotong ke n_candles
    (function ini yang akan motong & menyesuaikan index-nya sendiri). Kalau
    None, market structure dihitung otomatis dari data yang sudah dipotong."""
    import matplotlib
    matplotlib.use("Agg")  # non-interaktif, aman dipanggil dari thread/proses server
    import matplotlib.pyplot as plt
    import mplfinance as mpf

    plot_df_raw = df.tail(n_candles).copy().reset_index(drop=True)

    # Market structure dihitung/disesuaikan ke index candle yang KEPAKAI di
    # chart (0..len(plot_df_raw)-1), supaya posisi anotasi nyambung persis
    # ke posisi candle di sumbu-x.
    if market_structure is None:
        structure = detect_market_structure(plot_df_raw)
    else:
        offset = len(df) - n_candles if len(df) > n_candles else 0
        structure = {
            "trend": market_structure.get("trend"),
            "swings": [
                {**s, "idx": s["idx"] - offset}
                for s in market_structure.get("swings", [])
                if 0 <= s["idx"] - offset < len(plot_df_raw)
            ],
            "choch": None,
        }
        ch = market_structure.get("choch")
        if ch and 0 <= ch["idx"] - offset < len(plot_df_raw):
            structure["choch"] = {**ch, "idx": ch["idx"] - offset}

    plot_df = plot_df_raw.copy()
    if "datetime" not in plot_df.columns or plot_df["datetime"].isna().all():
        plot_df.index = pd.date_range(end=pd.Timestamp.now(), periods=len(plot_df), freq="h")
    else:
        plot_df["datetime"] = pd.to_datetime(plot_df["datetime"], utc=True, errors="coerce")
        plot_df = plot_df.dropna(subset=["datetime"]).set_index("datetime")

    plot_df = plot_df.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
    })
    has_volume = bool("Volume" in plot_df.columns and plot_df["Volume"].sum() > 0)

    addplots = []
    if len(plot_df) >= 20:
        ema20 = plot_df["Close"].ewm(span=20, adjust=False).mean()
        addplots.append(mpf.make_addplot(ema20, color="#f5a623", width=1.1))
    if len(plot_df) >= 50:
        ema50 = plot_df["Close"].ewm(span=50, adjust=False).mean()
        addplots.append(mpf.make_addplot(ema50, color="#4a90d9", width=1.1))

    hlines_vals, hlines_colors = [], []
    if sr:
        for lvl in sr.get("supports", []):
            hlines_vals.append(lvl)
            hlines_colors.append("#2ecc71")
        for lvl in sr.get("resistances", []):
            hlines_vals.append(lvl)
            hlines_colors.append("#e74c3c")

    mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350", edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle=":", gridcolor="#3a3a3a",
                                facecolor="#1e1e1e", figcolor="#1e1e1e", edgecolor="#555",
                                rc={"axes.labelcolor": "#ddd", "xtick.color": "#ddd", "ytick.color": "#ddd",
                                    "text.color": "#ddd", "axes.edgecolor": "#555"})

    plot_kwargs = dict(
        type="candle", style=style,
        volume=has_volume, title=f"\n{symbol} ({interval})",
        figsize=(10, 6), tight_layout=True,
        returnfig=True,  # supaya bisa dianotasi manual (HH/HL/LH/LL/CHoCH) sebelum disimpan
    )
    if addplots:
        plot_kwargs["addplot"] = addplots
    if hlines_vals:
        plot_kwargs["hlines"] = dict(hlines=hlines_vals, colors=hlines_colors, linestyle="--", linewidths=0.8)

    fig, axlist = mpf.plot(plot_df, **plot_kwargs)
    price_ax = axlist[0]

    # Anotasi swing points: HH/LH di atas swing high, HL/LL di bawah swing low.
    label_style = dict(fontsize=8, fontweight="bold", ha="center")
    price_range = float(plot_df["High"].max() - plot_df["Low"].min()) or 1.0
    y_offset = price_range * 0.02

    for s in structure.get("swings", []):
        if not s["label"]:
            continue
        color = "#f5a623" if s["label"] in ("HH", "LH") else "#4fc3f7"
        if s["type"] == "high":
            price_ax.annotate(
                s["label"], xy=(s["idx"], s["price"]), xytext=(s["idx"], s["price"] + y_offset),
                color=color, **label_style,
            )
            price_ax.scatter([s["idx"]], [s["price"]], color=color, s=18, zorder=5)
        else:
            price_ax.annotate(
                s["label"], xy=(s["idx"], s["price"]), xytext=(s["idx"], s["price"] - y_offset * 2.2),
                color=color, **label_style,
            )
            price_ax.scatter([s["idx"]], [s["price"]], color=color, s=18, zorder=5)

    choch = structure.get("choch")
    if choch:
        choch_color = "#e91e63" if choch["direction"] == "bearish" else "#ab47bc"
        price_ax.axhline(y=choch["broken_level"], color=choch_color, linestyle="-.", linewidth=1.2, alpha=0.85)
        price_ax.annotate(
            f"CHoCH ({choch['direction']})", xy=(choch["idx"], choch["broken_level"]),
            xytext=(max(choch["idx"] - 10, 0), choch["broken_level"] + y_offset * 2),
            color=choch_color, fontsize=8, fontweight="bold",
        )

    buf = io.BytesIO()
    fig.savefig(buf, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def get_support_resistance(df: pd.DataFrame, window: int = 5) -> dict:
    """Deteksi level S/R dari local pivot high/low, dengan 2 perbaikan dari
    audit Level 2 (P1):
    1. 'Single price problem': pivot yang berdekatan (dalam toleransi ~0.05%
       dari harga) di-cluster jadi SATU level, bukan dianggap level terpisah
       — sebelumnya tiap local extremum langsung jadi level sendiri walau
       cuma beda 1-2 pip dari level lain di dekatnya.
    2. 'No strength score': tiap level sekarang punya touch_count (berapa
       kali price approach level itu) — level dengan touch_count lebih
       tinggi = lebih signifikan/reliable sebagai S/R.

    supports/resistances tetap list of float (backward compat dengan semua
    caller yang sudah ada — RR calculation, display, dst). Info strength
    tambahan ada di support_strength/resistance_strength (dict level->touch)
    dan support_zones/resistance_zones (list dict lengkap) untuk caller yang
    mau detail lebih."""
    current_price = float(df["close"].iloc[-1])
    lows, highs = [], []
    n = len(df)
    for i in range(window, n - window):
        low_slice = df["low"].iloc[i - window: i + window + 1]
        high_slice = df["high"].iloc[i - window: i + window + 1]
        low_val, high_val = float(df["low"].iloc[i]), float(df["high"].iloc[i])
        if low_val == low_slice.min():
            lows.append(low_val)
        if high_val == high_slice.max():
            highs.append(high_val)

    tolerance = current_price * 0.0005  # ~0.05% dari harga — cluster pivot berdekatan

    def _cluster(points: list) -> list:
        """Kelompokkan angka yang berdekatan (dalam `tolerance`) jadi satu
        zone, level = rata-rata cluster, strength = jumlah anggota cluster."""
        if not points:
            return []
        points = sorted(points)
        clusters = [[points[0]]]
        for p in points[1:]:
            if p - clusters[-1][-1] <= tolerance:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [
            {"level": sum(c) / len(c), "zone_low": min(c), "zone_high": max(c), "touches": len(c)}
            for c in clusters
        ]

    support_zones_all = _cluster([l for l in lows if l < current_price])
    resistance_zones_all = _cluster([h for h in highs if h > current_price])

    # Terdekat ke harga saat ini, maksimal 3 (perilaku lama dipertahankan)
    support_zones = sorted(support_zones_all, key=lambda z: -z["level"])[:3]
    resistance_zones = sorted(resistance_zones_all, key=lambda z: z["level"])[:3]

    supports = [round(z["level"], 5) for z in support_zones]
    resistances = [round(z["level"], 5) for z in resistance_zones]

    return {
        "supports": supports,
        "resistances": resistances,
        "current_price": current_price,
        "support_zones": support_zones,
        "resistance_zones": resistance_zones,
        "support_strength": {round(z["level"], 5): z["touches"] for z in support_zones},
        "resistance_strength": {round(z["level"], 5): z["touches"] for z in resistance_zones},
    }


def detect_trap(df: pd.DataFrame, indicators: dict) -> str:
    """Wrapper backward-compat: return teks trap (dipakai banyak tempat yang
    cuma perlu tampilan/cek 'ada trap atau tidak'). Untuk akses state
    terstruktur (NONE/POTENTIAL/CONFIRMED), pakai detect_trap_state()."""
    return detect_trap_state(df, indicators)["text"]


def detect_trap_state(df: pd.DataFrame, indicators: dict) -> dict:
    """Deteksi trap dengan STATE eksplisit (NONE/POTENTIAL/CONFIRMED), bukan
    boolean sederhana (audit Level 2 finding). Divergence RSI + break level
    SENDIRIAN belum cukup buat menyatakan trap pasti — jadi:
    - POTENTIAL: pola divergence terdeteksi, tapi breaknya tipis/marginal.
    - CONFIRMED: break cukup jelas (>0.1% dari harga) DAN sudah close balik
      ke dalam level (reclaim) DAN divergence RSI cukup besar (>5 poin).

    'Short Squeeze' lama di-rename jadi 'Bullish Reversal Pressure' dan
    SELALU berstatus POTENTIAL (bukan CONFIRMED) — istilah 'squeeze' itu
    overclaim tanpa validasi liquidity reclaim + displacement candle
    lanjutan, yang belum diimplementasikan di sini."""
    if len(df) < 25:
        return {"state": "NONE", "signals": [], "text": "Tidak ada sinyal trap terdeteksi"}

    close, open_, high, low, volume = df["close"], df["open"], df["high"], df["low"], df["volume"]
    rsi_series = _rsi(close, 14)
    signals = []
    states = []

    lookback = df.iloc[-21:-1]
    recent_low, recent_low_idx = lookback["low"].min(), lookback["low"].idxmin()
    recent_high, recent_high_idx = lookback["high"].max(), lookback["high"].idxmax()

    current_low, current_high = float(low.iloc[-1]), float(high.iloc[-1])
    current_close = float(close.iloc[-1])
    rsi_now = float(rsi_series.iloc[-1])
    rsi_at_recent_low = float(rsi_series.loc[recent_low_idx])
    rsi_at_recent_high = float(rsi_series.loc[recent_high_idx])

    if current_low < recent_low and rsi_now > rsi_at_recent_low and current_close > current_low:
        break_pct = abs(recent_low - current_low) / recent_low * 100 if recent_low else 0
        rsi_div = rsi_now - rsi_at_recent_low
        reclaimed = current_close > recent_low  # close balik ke ATAS level yang di-sweep
        confirmed = break_pct > 0.1 and rsi_div > 5 and reclaimed
        state = "CONFIRMED" if confirmed else "POTENTIAL"
        states.append(state)
        signals.append(
            f"🪤 [{state}] Bear Trap: harga break support ({break_pct:.2f}%) tapi RSI divergence "
            f"({rsi_div:.1f}pt) — potensi reversal naik"
            + ("" if confirmed else " (break/divergence masih tipis, belum confirmed)")
        )

    if current_high > recent_high and rsi_now < rsi_at_recent_high and current_close < current_high:
        break_pct = abs(current_high - recent_high) / recent_high * 100 if recent_high else 0
        rsi_div = rsi_at_recent_high - rsi_now
        reclaimed = current_close < recent_high  # close balik ke BAWAH level yang di-sweep
        confirmed = break_pct > 0.1 and rsi_div > 5 and reclaimed
        state = "CONFIRMED" if confirmed else "POTENTIAL"
        states.append(state)
        signals.append(
            f"🪤 [{state}] Bull Trap: harga break resistance ({break_pct:.2f}%) tapi RSI divergence "
            f"({rsi_div:.1f}pt) — potensi reversal turun"
            + ("" if confirmed else " (break/divergence masih tipis, belum confirmed)")
        )

    vol_sma20 = volume.rolling(20).mean().iloc[-1]
    candle_range = float(high.iloc[-1] - low.iloc[-1])
    body = float(close.iloc[-1] - open_.iloc[-1])
    if (
        rsi_now < 30
        and vol_sma20 and volume.iloc[-1] > vol_sma20 * 1.5
        and body > 0 and candle_range > 0
        and (body / candle_range) > 0.6
    ):
        states.append("POTENTIAL")
        signals.append(
            "🚀 [POTENTIAL] Bullish Reversal Pressure: RSI oversold + activity spike + candle bullish besar "
            "(BUKAN squeeze terkonfirmasi — belum ada validasi liquidity reclaim + displacement lanjutan)"
        )

    if not signals:
        return {"state": "NONE", "signals": [], "text": "Tidak ada sinyal trap terdeteksi"}

    overall_state = "CONFIRMED" if "CONFIRMED" in states else "POTENTIAL"
    return {"state": overall_state, "signals": signals, "text": "\n".join(signals)}


def full_analysis(symbol: str, interval: str = "1h") -> dict:
    df, err = _ohlcv_or_error(symbol, interval)
    if err:
        return {"error": err}
    indicators = calculate_indicators(df)
    return {
        "symbol": symbol,
        "interval": interval,
        "indicators": indicators,
        "confidence": calculate_confidence(indicators),
        "regime": get_regime(indicators),
        "sr": get_support_resistance(df),
        "trap": detect_trap(df, indicators),
    }


# ── News Search (Tavily primary, DuckDuckGo fallback) ─────────


def _fetch_news_tavily(query: str):
    """Return list of {'title','body','url'} on success, or None if unavailable/failed."""
    if not TAVILY_API_KEY:
        return None
    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(
            query=f"{query} forex gold currency central bank",
            topic="news",
            days=2,
            max_results=3,
        )
        items = response.get("results", [])
        if not items:
            return []
        return [
            {
                "title": item.get("title", "No title"),
                "body": (item.get("content") or "").strip(),
                "url": item.get("url", ""),
            }
            for item in items
        ]
    except Exception as e:
        print(f"Tavily search error: {e}")
        return None


def _fetch_news_ddgs(query: str):
    """Return list of {'title','body','url'} on success, or None if failed."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(f"{query} forex gold currency central bank", max_results=3))
        if not results:
            return []
        return [
            {
                "title": item.get("title", "No title"),
                "body": (item.get("body") or "").strip(),
                "url": item.get("url", ""),
            }
            for item in results
        ]
    except Exception as e:
        print(f"DuckDuckGo search error: {e}")
        return None


def _summarize_news_with_groq(items: list) -> str:
    """Filter for forex/gold/macro relevance, translate & summarize with impact via Groq."""
    raw_lines = []
    for i, item in enumerate(items, 1):
        raw_lines.append(f"[{i}] Judul: {item['title']}\nIsi: {item['body']}\nURL: {item['url']}")
    raw_text = "\n\n".join(raw_lines)

    prompt = (
        f"Berikut {len(items)} hasil berita mentah dari pencarian web (bisa jadi ada teks navigasi "
        f"website yang tidak relevan, dan berbahasa Inggris):\n\n{raw_text}\n\n"
        "Tugas kamu:\n"
        "1. Baca setiap berita, buang/skip berita yang TIDAK relevan dengan forex, gold/emas, "
        "atau ekonomi makro (misalnya berita real estate lokal AS atau class action lawsuit saham individual).\n"
        "2. Untuk setiap berita yang relevan, ringkas jadi 2-3 kalimat dalam Bahasa Indonesia.\n"
        "3. Di akhir setiap ringkasan berita, tambahkan baris baru: "
        "\"💡 Dampak: [penjelasan singkat potensi dampak ke USD/Gold/pair forex terkait]\".\n"
        "4. Jika setelah difilter TIDAK ADA berita yang relevan sama sekali, balas HANYA dengan teks persis: "
        "\"Tidak ada berita forex/gold relevan ditemukan saat ini.\"\n"
        "5. Jangan gunakan tanda bintang (*) untuk format, gunakan bullet points (•) jika perlu."
    )
    return analyze_with_groq(prompt)


def search_news(query: str) -> str:
    """Search recent forex/gold/economic news, filter & summarize via Groq. Tries Tavily first, falls back to DuckDuckGo."""
    items = _fetch_news_tavily(query)
    if items is None:
        items = _fetch_news_ddgs(query)

    if items is None:
        return "Tidak bisa mengambil berita saat ini, coba lagi beberapa menit lagi."

    if not items:
        return "Tidak ada berita forex/gold relevan ditemukan saat ini."

    return _summarize_news_with_groq(items)


def _get_bls_employment_situation_text(event_date_str: str):
    """Employment Situation report BLS (NFP, Unemployment Rate, Average Hourly
    Earnings semuanya dari laporan yang sama) punya URL arsip yang predictable
    berdasarkan tanggal rilis: empsit_MMDDYYYY.htm. Jauh lebih akurat daripada
    web search umum karena langsung dari sumber resmi. Return None kalau gagal
    (bukan hari rilis employment situation, atau request gagal)."""
    d = _parse_ff_date(event_date_str)
    if d is None:
        return None
    url = f"https://www.bls.gov/news.release/archives/empsit_{d.strftime('%m%d%Y')}.htm"
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        text = resp.text
        idx = text.upper().find("THE EMPLOYMENT SITUATION")
        if idx == -1:
            return None
        # Ambil ~12000 karakter HTML mentah setelah judul (paragraf Average
        # Hourly Earnings ada di bagian "Establishment Survey Data" yang
        # cukup jauh dari judul, 4000 karakter kemarin kepotong duluan).
        snippet = text[idx: idx + 12000]
        clean = re.sub(r"<[^>]+>", " ", snippet)
        clean = re.sub(r"&nbsp;|&amp;", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean
    except Exception as e:
        print(f"Gagal fetch BLS employment situation: {e}")
        return None


BLS_EMPLOYMENT_EVENT_TITLES = {"non-farm employment change", "unemployment rate", "average hourly earnings m/m"}


def _extract_bls_employment_metrics(bls_text: str) -> dict:
    """Satu kali AI call buat ekstrak SEMUA angka penting (NFP, Unemployment
    Rate, Average Hourly Earnings) dari satu kutipan BLS sekaligus — lebih
    hemat (1 fetch + 1 AI call, bukan 3x) dan lebih konsisten daripada
    tanya satu-satu per event secara terpisah."""
    prompt = (
        f"Berikut kutipan resmi dari rilis BLS Employment Situation:\n\n{bls_text}\n\n"
        f"Ekstrak TIGA angka berikut dari teks di atas. Jawab PERSIS dengan format 3 baris ini, "
        f"tanpa tambahan penjelasan apapun:\n"
        f"NFP: [angka nonfarm payroll employment change, atau 'tidak ditemukan']\n"
        f"UNEMPLOYMENT: [angka unemployment rate dalam persen, atau 'tidak ditemukan']\n"
        f"EARNINGS: [angka average hourly earnings m/m, atau 'tidak ditemukan']\n\n"
        f"JANGAN mengarang angka yang tidak ada di teks."
    )
    raw = analyze_with_groq(prompt)

    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("NFP:"):
            result["non-farm employment change"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("UNEMPLOYMENT:"):
            result["unemployment rate"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("EARNINGS:"):
            result["average hourly earnings m/m"] = line.split(":", 1)[1].strip()
    return result


_bls_metrics_cache = {}


def _get_bls_metrics_for_date(date_str: str) -> dict:
    """Cache in-memory per proses: fetch + ekstrak BLS employment situation
    cuma sekali per tanggal, dipakai ulang buat NFP/Unemployment/Earnings
    yang tanggalnya sama, supaya tidak fetch bls.gov berkali-kali (rawan
    kena rate-limit / bikin hasil tidak konsisten)."""
    if date_str in _bls_metrics_cache:
        return _bls_metrics_cache[date_str]

    bls_text = _get_bls_employment_situation_text(date_str)
    metrics = _extract_bls_employment_metrics(bls_text) if bls_text else {}
    _bls_metrics_cache[date_str] = metrics
    return metrics


def _fetch_actual_from_web(event: dict) -> str:
    """Fallback ketika field Actual kosong di feed kalender (keterbatasan
    feed gratis Forex Factory) tapi event sudah pasti lewat waktunya:
    coba sumber resmi (BLS) dulu untuk event yang cocok, baru fallback ke
    pencarian web umum kalau tidak match atau gagal."""
    title_lower = event["title"].lower()

    if title_lower in BLS_EMPLOYMENT_EVENT_TITLES:
        metrics = _get_bls_metrics_for_date(event["date"])
        value = metrics.get(title_lower)
        if value and "tidak ditemukan" not in value.lower():
            return f"Actual: {value}"
        return "Tidak ditemukan di sumber BLS"

    query = f"{event['title']} actual result {event['date']}"
    items = _fetch_news_tavily(query)
    if items is None:
        items = _fetch_news_ddgs(query)
    if not items:
        return "tidak ditemukan hasil pencarian web untuk event ini"

    raw_lines = []
    for i, item in enumerate(items, 1):
        raw_lines.append(f"[{i}] Judul: {item['title']}\nIsi: {item['body']}\nURL: {item['url']}")
    raw_text = "\n\n".join(raw_lines)

    prompt = (
        f"Berikut hasil pencarian web soal event ekonomi \"{event['title']}\" ({event['date']}):\n\n"
        f"{raw_text}\n\n"
        f"Dari hasil di atas, ekstrak angka ACTUAL/hasil rilis resmi event ini kalau ada disebutkan "
        f"secara eksplisit. Jawab HANYA dengan salah satu dari:\n"
        f"- Angka actual-nya kalau ketemu jelas (cth: 'Actual: 4.1% (unemployment rate)')\n"
        f"- 'Tidak ditemukan angka actual yang jelas di hasil pencarian ini' kalau tidak ada — "
        f"JANGAN mengarang angka."
    )
    return analyze_with_groq(prompt)


def get_calendar_actual_fallback(events, max_lookups: int = 5) -> str:
    """Untuk event High/Medium impact yang sudah lewat waktunya tapi Actual-nya
    kosong di feed kalender, cari via web search (atau sumber resmi kalau ada
    jalurnya) sebagai fallback. Dibatasi max_lookups biar tidak terlalu banyak
    request per pesan.

    Prioritas urutan: (1) event yang punya jalur sumber resmi akurat (BLS,
    dst) — supaya slot lookup kepakai buat yang paling mungkin berhasil,
    baru (2) High impact, baru (3) Medium impact. TIDAK sekadar ambil N
    event pertama secara kronologis, karena event penting (mis. NFP) sering
    ada di akhir minggu dan bisa keburu kehabisan slot oleh event low-value
    yang tanggalnya lebih awal."""
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    candidates = []
    for e in events:
        actual = e.get("actual", "N/A")
        if actual and actual != "N/A":
            continue
        if e.get("impact") not in ("High", "Medium"):
            continue
        event_dt = _parse_ff_datetime(e["date"], e["time"])
        if event_dt is None or event_dt >= now_ny:
            continue
        candidates.append(e)

    if not candidates:
        return ""

    def _priority(e):
        has_accurate_source = e["title"].lower() in BLS_EMPLOYMENT_EVENT_TITLES
        impact_rank = 0 if e["impact"] == "High" else 1
        return (0 if has_accurate_source else 1, impact_rank)

    candidates = sorted(candidates, key=_priority)[:max_lookups]
    lines = ["🔍 Pencarian tambahan (karena Actual kosong di feed kalender):"]
    for e in candidates:
        result = _fetch_actual_from_web(e)
        lines.append(f"- {e['title']} ({e['date']}): {result}")
    return "\n".join(lines)


# ── Fitur Baru: Event Preview & Prediction (NFP/CPI/FOMC/PPI) ───
# Untuk event yang punya dampak besar ke pasar, alert biasa (cuma forecast/
# previous) dirasa kurang — jadi sebelum event rilis, bot kumpulkan berita
# terkait + bikin analisa prediksi arah kemungkinan hasilnya.

MAJOR_EVENT_KEYWORDS = {
    "non-farm", "nonfarm", "nfp", "payroll",
    "cpi", "consumer price index",
    "fomc", "federal funds rate", "fed interest rate", "rate decision",
    "ppi", "producer price index",
}


def is_major_event(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in MAJOR_EVENT_KEYWORDS)


RELATED_EVENT_KEYWORDS = {
    "nonfarm": ["unemployment rate", "average hourly earnings", "adp", "jobless claims", "participation rate", "payroll"],
    "non-farm": ["unemployment rate", "average hourly earnings", "adp", "jobless claims", "participation rate", "payroll"],
    "nfp": ["unemployment rate", "average hourly earnings", "adp", "jobless claims", "participation rate", "payroll"],
    "payroll": ["unemployment rate", "average hourly earnings", "adp", "jobless claims"],
    "cpi": ["core cpi", "ppi", "core ppi", "pce", "core pce", "inflation"],
    "consumer price index": ["core cpi", "ppi", "core ppi", "pce", "core pce"],
    "ppi": ["cpi", "core cpi", "core ppi", "pce"],
    "producer price index": ["cpi", "core cpi", "pce"],
    "fomc": ["fed", "interest rate", "rate decision", "powell", "dot plot", "press conference"],
    "federal funds rate": ["fed", "fomc", "powell", "dot plot", "press conference"],
    "rate decision": ["fed", "fomc", "powell", "dot plot"],
}


def get_related_calendar_context(event: dict, max_events: int = 8) -> str:
    """Ambil event kalender USD lain yang berkaitan dengan `event`:
    (1) event lain di hari yang sama, dan (2) leading indicator terkait
    kategori event (mis. untuk NFP: Unemployment Rate, ADP, Jobless Claims).
    Dipakai sebagai bahan tambahan buat analisa prediksi."""
    all_events = get_calendar(filter_country="USD")
    if not all_events:
        return _calendar_status_note(all_events)

    title_lower = event["title"].lower()
    related_kw = []
    for key, kws in RELATED_EVENT_KEYWORDS.items():
        if key in title_lower:
            related_kw = kws
            break

    same_day = [
        e for e in all_events
        if e["date"] == event["date"] and e["title"] != event["title"]
    ]
    keyword_related = [
        e for e in all_events
        if e["title"] != event["title"]
        and e["date"] != event["date"]
        and any(kw in e["title"].lower() for kw in related_kw)
    ]

    combined = same_day + keyword_related
    # Dedup jaga urutan, batasi jumlah biar prompt gak kebanjiran.
    seen = set()
    unique = []
    for e in combined:
        key = (e["title"], e["date"], e["time"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
        if len(unique) >= max_events:
            break

    if not unique:
        return "Tidak ada event kalender lain yang berkaitan langsung minggu ini."

    return format_calendar_for_prompt(unique)


def generate_event_prediction(event: dict) -> str:
    """Kumpulkan berita + data kalender terkait event (NFP/CPI/FOMC/PPI dsb),
    lalu minta AI merangkum EVIDENCE (bukan menebak bebas) jadi bias
    BEAT/INLINE/MISS dengan strength LOW/MEDIUM/HIGH — sesuai rekomendasi
    audit P2 ("LLM sebaiknya menyintesis evidence, bukan diperlakukan
    sebagai statistical forecasting engine"). Sebelumnya prompt minta AI
    langsung "prediksi kemungkinan hasil" secara bebas, yang gampang
    menghasilkan kesan presisi/confidence yang tidak didukung model
    probabilistik apapun. Sekarang AI WAJIB nyebutin evidence konkret
    (leading indicator apa, konsensus dari mana) sebelum kasih bias/strength
    — kalau evidence-nya tipis, strength HARUS LOW, bukan dikarang tinggi."""
    title = event["title"]
    news_text = search_news(f"{title} preview forecast expectations")
    related_calendar_text = get_related_calendar_context(event)

    actual = event.get("actual", "N/A")
    actual_line = f"Actual (sudah dirilis): {actual}\n" if actual and actual != "N/A" else ""

    prompt = (
        f"Event ekonomi penting akan/baru saja rilis: {title}\n"
        f"Waktu: {event['date']} {event['time']}\n"
        f"Forecast: {event['forecast']} | Previous: {event['previous']}\n"
        f"{actual_line}\n"
        f"Data kalender ekonomi USD lain yang berkaitan (event di hari yang sama & leading "
        f"indicator terkait, termasuk Actual kalau sudah dirilis):\n{related_calendar_text}\n\n"
        f"Berita/konteks terkait dari pencarian web real-time:\n{news_text}\n\n"
        f"Tugasmu: SINTESIS evidence di atas jadi kesimpulan terstruktur, BUKAN menebak bebas. "
        f"Wajib ikuti format berikut PERSIS (isi bagian dalam kurung siku, hapus tanda kurungnya):\n\n"
        f"EVIDENCE:\n"
        f"- [Sebutkan leading indicator konkret yang tersedia dari data di atas — mis. ADP, "
        f"Jobless Claims, JOLTS, ISM Employment, Core PPI/CPI, wage trend, dst — dan arahnya. "
        f"Kalau TIDAK ADA leading indicator relevan di data yang diberikan, tulis 'Tidak ada "
        f"leading indicator relevan di data yang tersedia' — JANGAN mengarang.]\n"
        f"- [Sebutkan consensus/ekspektasi pasar dari berita kalau ada disebutkan]\n"
        f"- [Sebutkan revisi data periode sebelumnya kalau relevan/disebutkan]\n\n"
        f"BIAS: [BEAT / INLINE / MISS — hasil rilis kemungkinan di atas / sesuai / di bawah forecast]\n"
        f"STRENGTH: [LOW / MEDIUM / HIGH — HIGH hanya kalau ada MINIMAL 2 evidence konkret yang "
        f"searah dan spesifik; kalau evidence di atas kosong/samar, WAJIB LOW, jangan dinaikkan]\n\n"
        f"DAMPAK USD/XAU:\n"
        f"- Kalau BEAT: [dampak ke USD dan XAU/USD]\n"
        f"- Kalau MISS: [dampak ke USD dan XAU/USD]\n\n"
        f"Total maksimal 8 kalimat. Jangan pakai kata 'confidence tinggi' atau klaim presisi lain "
        f"yang tidak didukung evidence yang kamu sebutkan di atas."
    )
    return analyze_with_groq(prompt)


# ── Macro/Micro Economic Briefing ───────────────────────────────
# Catatan: DDGS (fallback tanpa API key) bisa kena rate-limit kalau dipanggil
# terlalu sering — briefing ini sengaja dibatasi ke jadwal sesi (3x/hari),
# bukan setiap jam, untuk menjaga reliabilitas.

MACRO_QUERIES = [
    "Federal Reserve interest rate policy",
    "US inflation CPI PCE data",
    "dollar index DXY outlook",
    "gold XAUUSD price outlook",
]


_MACRO_NEWS_CACHE_TTL_SECONDS = 45 * 60  # 45 menit
_macro_news_cache = {"items": None, "fetched_at": None}


def _fetch_macro_news_items(force_refresh: bool = False) -> list:
    """Gabungkan hasil dari beberapa query makro jadi satu list item unik.

    Di-cache selama _MACRO_NEWS_CACHE_TTL_SECONDS supaya /macro manual yang
    dipanggil berdekatan waktu (oleh user berbeda, atau berdekatan dengan
    job terjadwal 3x/hari) tidak memicu 4 query baru ke Tavily/DDGS setiap
    kali — DDGS fallback gampang kena rate-limit kalau dipanggil sering.
    """
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    cached = _macro_news_cache["items"]
    fetched_at = _macro_news_cache["fetched_at"]
    if (
        not force_refresh
        and cached is not None
        and fetched_at is not None
        and (now - fetched_at).total_seconds() < _MACRO_NEWS_CACHE_TTL_SECONDS
    ):
        return cached

    seen_urls = set()
    combined = []
    for q in MACRO_QUERIES:
        items = _fetch_news_tavily(q)
        if items is None:
            items = _fetch_news_ddgs(q)
        if not items:
            continue
        for it in items:
            url = it.get("url", "")
            if url and url in seen_urls:
                continue
            seen_urls.add(url)
            combined.append(it)

    # Kalau fetch gagal total (list kosong karena error, bukan karena
    # memang tidak ada berita), jangan timpa cache lama yang masih valid —
    # lebih baik pakai data lama daripada data kosong.
    if combined or cached is None:
        _macro_news_cache["items"] = combined
        _macro_news_cache["fetched_at"] = now
        return combined
    return cached


# ── Narrative Bias (Daily/Weekly/Monthly, ICT-style liquidity sweep) ─────
# Deterministik: arah bias dihitung dari data candle via fungsi Python biasa,
# BUKAN diserahkan ke LLM untuk ditebak. Groq hanya boleh menjelaskan konteks
# macro di sekitar arah yang sudah dihitung di sini, tidak boleh mengubah arah.
#
# CATATAN VERIFIKASI: logika di bawah mengasumsikan candle index -1 dari
# Twelve Data adalah candle yang SEDANG BERJALAN (belum closed), sehingga
# index -2 dipakai sebagai "previous period" (PDH/PDL dst). Asumsi ini BELUM
# diverifikasi terhadap data live — jalankan _debug_print_candles() sebelum
# mengandalkan fitur ini untuk keputusan trading.

_NARRATIVE_PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]


def _pair_to_twelvedata_symbol(pair: str) -> str:
    """'XAUUSD' -> 'XAU/USD', 'EURUSD' -> 'EUR/USD'. Pakai format_symbol yang
    sudah ada di file ini supaya konsisten dengan fungsi harga lain."""
    return format_symbol(pair)


_OHLC_CACHE_TTL_SECONDS = {
    # Weekly/monthly candle praktis tidak berubah dalam hitungan menit —
    # cache lebih lama drastis mengurangi jumlah API call yang gampang
    # kena limit 8 credit/menit di free-tier Twelve Data.
    "1day": 20 * 60,        # 20 menit
    "1week": 6 * 60 * 60,   # 6 jam
    "1month": 24 * 60 * 60,  # 24 jam
}
_ohlc_cache = {}  # key: (symbol, interval) -> {"data": [...], "fetched_at": datetime}


_TD_CALL_TIMESTAMPS = []  # epoch seconds dari semua live call TwelveData (cache-hit tidak masuk sini)
_TD_RATE_LIMIT_PER_MIN = 7  # akun ini limitnya 8 credit/menit; sisakan 1 buffer

TD_RATE_LIMIT_LOCK = threading.Lock()


def _wait_for_td_rate_limit_slot():
    """Blocking wait supaya SEMUA request TwelveData (get_ohlcv, get_forex_price,
    fetch_period_ohlc) tidak menembak lebih dari _TD_RATE_LIMIT_PER_MIN call
    live dalam 60 detik terakhir, gabung dalam satu budget. Sebelumnya ini
    cuma melindungi fetch_period_ohlc — get_ohlcv/get_forex_price bisa lewat
    tanpa proteksi dan menghabiskan quota jalur lain (temuan audit P1).
    Dikunci dengan lock supaya aman dipanggil dari banyak request/user
    bersamaan (bot melayani banyak user sekaligus, bukan satu proses linear)."""
    with TD_RATE_LIMIT_LOCK:
        now = time.time()
        global _TD_CALL_TIMESTAMPS
        _TD_CALL_TIMESTAMPS = [t for t in _TD_CALL_TIMESTAMPS if now - t < 60]
        if len(_TD_CALL_TIMESTAMPS) >= _TD_RATE_LIMIT_PER_MIN:
            sleep_for = 60 - (now - _TD_CALL_TIMESTAMPS[0]) + 0.5
            if sleep_for > 0:
                print(f"TD rate limit guard: nunggu {sleep_for:.1f}s sebelum fetch berikutnya")
                time.sleep(sleep_for)
        _TD_CALL_TIMESTAMPS.append(time.time())


def fetch_period_ohlc(symbol: str, interval: str, outputsize: int = 3):
    """Ambil beberapa candle terakhir Twelve Data untuk interval besar
    ('1day', '1week', '1month'). Return list candle terurut LAMA -> BARU,
    atau list kosong kalau gagal/data tidak cukup.

    Di-cache per (symbol, interval) dengan TTL berbeda-beda (lihat
    _OHLC_CACHE_TTL_SECONDS) untuk menghindari limit 8 credit/menit
    Twelve Data — build_narrative_block() memanggil fungsi ini 12x
    (4 pair x 3 interval) sekaligus, jadi tanpa cache pasti kena limit.

    Catatan: interval '1week'/'1month' TIDAK ada di VALID_INTERVALS yang
    dipakai get_ohlcv() untuk analisis teknikal candle kecil, jadi sengaja
    dibuat fungsi fetch terpisah di sini, bukan reuse get_ohlcv().
    """
    cache_key = (symbol, interval)
    ttl = _OHLC_CACHE_TTL_SECONDS.get(interval, 20 * 60)
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    cached = _ohlc_cache.get(cache_key)
    if cached and (now - cached["fetched_at"]).total_seconds() < ttl:
        return cached["data"]

    try:
        _wait_for_td_rate_limit_slot()
        resp = requests.get(
            f"{TWELVE_DATA_BASE}/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "timezone": "UTC",
                "apikey": TWELVE_DATA_API_KEY,
            },
            timeout=15,
        )
        data = resp.json()
        values = data.get("values")
        if data.get("status") == "error" or not values:
            print(f"fetch_period_ohlc gagal ({symbol}, {interval}): {data.get('message', 'no values')}")
            # Kalau gagal (mis. rate limit) tapi ada cache lama, lebih baik
            # pakai data basi daripada kosong total.
            if cached:
                return cached["data"]
            return []
        values = list(reversed(values))  # Twelve Data kirim terbaru dulu, kita balik
        result = [
            {
                "datetime": v["datetime"],
                "dt": pd.to_datetime(v["datetime"], utc=True, errors="coerce"),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
            }
            for v in values
        ]
        _ohlc_cache[cache_key] = {"data": result, "fetched_at": now}
        return result
    except Exception as ex:
        print(f"fetch_period_ohlc exception ({symbol}, {interval}): {ex}")
        if cached:
            return cached["data"]
        return []


def _debug_print_candles(symbol: str = "XAU/USD"):
    """Helper verifikasi manual. Jalankan sekali lewat python3 -c atau shell
    interaktif SEBELUM mengandalkan narrative bias untuk keputusan nyata:

        python3 -c "from main import _debug_print_candles; _debug_print_candles()"

    Cek apakah candle terakhir (index -1) tanggalnya HARI INI (candle live)
    atau tanggal SEBELUMNYA (candle sudah closed). Kalau ternyata sudah
    closed, index -2 di determine_liquidity_bias() harus digeser ke -1,
    dan butuh sumber harga real-time terpisah untuk 'candle berjalan'.
    """
    candles = fetch_period_ohlc(symbol, "1day", 3)
    for c in candles:
        print(c)
    print("Hari ini (server):", datetime.now(ZoneInfo("Asia/Jakarta")).date())


def determine_liquidity_bias(candles: list, label: str) -> dict:
    """Logika ICT: bandingkan candle berjalan (index -1) vs high/low candle
    SEBELUMNYA (index -2). Sweep high tanpa sweep low -> BEARISH (target
    liquidity di bawah). Sweep low tanpa sweep high -> BULLISH (target
    liquidity di atas). Ini bias = skenario probabilitas tertinggi, BUKAN
    prediksi pasti — konsisten dengan definisi di materi ICT yang dipakai.

    Validasi freshness otomatis: cek apakah candle[-1] itu masih 'wajar'
    sebagai representasi periode berjalan (bukan data basi/stale karena
    market tutup lama atau gap fetch) — sebelumnya ini cuma dicek manual
    lewat _debug_print_candles()."""
    if len(candles) < 2:
        return {"label": label, "bias": "Data tidak cukup", "detail": "Candle kurang dari 2."}

    prev, cur = candles[-2], candles[-1]
    swept_high = cur["high"] > prev["high"]
    swept_low = cur["low"] < prev["low"]

    staleness_note = ""
    cur_dt = cur.get("dt")
    if cur_dt is not None and not pd.isna(cur_dt):
        age_days = (datetime.now(ZoneInfo("UTC")) - cur_dt).total_seconds() / 86400
        # Toleransi longgar (3 hari) karena weekend/holiday market tutup —
        # di atas itu kemungkinan data basi/gap fetch, bukan sekadar libur.
        if age_days > 3:
            staleness_note = (
                f" ⚠️ Data candle terakhir berumur ~{age_days:.1f} hari — kemungkinan "
                f"basi/gap fetch, bukan candle yang benar-benar berjalan saat ini."
            )

    if swept_high and not swept_low:
        bias = "BEARISH"
        detail = f"Sweep {label}H ({prev['high']:.2f}), target {label}L ({prev['low']:.2f}).{staleness_note}"
    elif swept_low and not swept_high:
        bias = "BULLISH"
        detail = f"Sweep {label}L ({prev['low']:.2f}), target {label}H ({prev['high']:.2f}).{staleness_note}"
    elif swept_high and swept_low:
        bias = "NETRAL (dua sisi ter-sweep)"
        detail = f"Butuh konfirmasi arah lanjutan.{staleness_note}"
    else:
        bias = "NETRAL (belum sweep)"
        detail = f"Masih di range {label}L–{label}H.{staleness_note}"

    return {"label": label, "bias": bias, "detail": detail}


def build_narrative_bias_report(pair: str) -> dict:
    """Hitung Daily/Weekly/Monthly bias untuk satu pair, berbasis liquidity
    sweep PDH/PDL, PWH/PWL, PMH/PML. Return dict terstruktur (bukan string)
    supaya bisa disuntik ke prompt Groq sebagai FAKTA, bukan opini yang bisa
    diubah arahnya oleh model."""
    symbol = _pair_to_twelvedata_symbol(pair)
    d = determine_liquidity_bias(fetch_period_ohlc(symbol, "1day", 3), "PD")
    w = determine_liquidity_bias(fetch_period_ohlc(symbol, "1week", 3), "PW")
    m = determine_liquidity_bias(fetch_period_ohlc(symbol, "1month", 3), "PM")
    dirs = [x["bias"].split()[0] for x in (d, w, m) if "Data tidak cukup" not in x["bias"]]
    aligned = len(dirs) == 3 and len(set(dirs)) == 1
    return {"pair": pair, "daily": d, "weekly": w, "monthly": m, "aligned": aligned}


def build_narrative_block() -> str:
    """Bangun blok teks narrative bias untuk semua pair di _NARRATIVE_PAIRS,
    siap disuntik ke prompt Groq. Kalau semua pair gagal fetch data (mis.
    API key kosong atau limit habis), return pesan eksplisit, bukan string
    kosong yang bisa bikin Groq diam-diam mengarang."""
    reports = [build_narrative_bias_report(p) for p in _NARRATIVE_PAIRS]

    lines = []
    any_data = False
    for r in reports:
        d, w, m = r["daily"], r["weekly"], r["monthly"]
        if "Data tidak cukup" in d["bias"] and "Data tidak cukup" in w["bias"] and "Data tidak cukup" in m["bias"]:
            lines.append(f"{r['pair']}: Data candle tidak tersedia (fetch gagal).")
            continue
        any_data = True
        align_note = "SELARAS" if r["aligned"] else "tidak selaras antar timeframe"
        lines.append(
            f"{r['pair']}: Daily={d['bias']} ({d['detail']}) | "
            f"Weekly={w['bias']} ({w['detail']}) | "
            f"Monthly={m['bias']} ({m['detail']}) | Status: {align_note}"
        )

    if not any_data:
        return (
            "TIDAK ADA DATA CANDLE YANG BERHASIL DIAMBIL untuk narrative bias — "
            "kemungkinan TWELVE_DATA_API_KEY kosong/invalid, atau limit API habis. "
            "JANGAN mengarang arah bias teknikal, laporkan sebagai data tidak tersedia."
        )
    return "\n".join(lines)


def generate_macro_briefing() -> str:
    """Bangun laporan insight makro/mikro + bias arah untuk Gold, USD, dan forex mayor.

    Menggabungkan: berita makro terbaru (Fed, inflasi, DXY, gold) + event
    high-impact USD hari ini dari kalender. Hasil akhir tetap lewat Groq
    untuk sintesis, dengan instruksi eksplisit agar model tidak mengarang
    angka bila data mentah tipis.
    """
    with ThreadPoolExecutor(max_workers=3) as pool:
        news_items, events_today, narrative_block = pool.map(
            lambda fn: fn(),
            (
                _fetch_macro_news_items,
                lambda: get_calendar(filter_country="USD", filter_impact="High", only_today=True),
                build_narrative_block,
            ),
        )

    if not news_items and not events_today and "TIDAK ADA DATA CANDLE" in narrative_block:
        return (
            "Tidak ada data berita makro, event kalender, maupun candle teknikal yang "
            "berhasil diambil saat ini. Coba lagi beberapa saat lagi."
        )

    news_block = "Tidak ada berita makro relevan yang berhasil diambil."
    if news_items:
        lines = []
        for i, item in enumerate(news_items, 1):
            lines.append(
                f"[{i}] {item['title']}\n{item['body'][:500]}\nSumber: {item['url']}"
            )
        news_block = "\n\n".join(lines)

    calendar_block = format_calendar_for_prompt(events_today) if events_today else (
        _calendar_status_note(events_today) if events_today is None
        else "Tidak ada event High Impact USD terjadwal hari ini."
    )

    now_str = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%d %b %Y, %H:%M WIB")

    prompt = (
        f"Waktu saat ini: {now_str}.\n\n"
        f"=== BERITA MAKRO MENTAH (bisa berbahasa Inggris, ada noise) ===\n{news_block}\n\n"
        f"=== EVENT HIGH IMPACT USD HARI INI (dari kalender) ===\n{calendar_block}\n\n"
        f"=== BIAS TEKNIKAL TERHITUNG (FAKTA MATEMATIS dari candle, JANGAN DIUBAH ARAHNYA) ===\n"
        f"{narrative_block}\n\n"
        "Tugas kamu: buat laporan Insight Ekonomi Makro & Mikro untuk trader XAU/USD dan forex, "
        "TERSTRUKTUR PERSIS seperti ini:\n\n"
        "1. RINGKASAN MAKRO GLOBAL — 3-5 kalimat, hanya dari data di atas. Kalau data terlalu tipis "
        "untuk topik tertentu (mis. tidak ada berita Fed baru), katakan itu secara eksplisit, JANGAN mengarang.\n"
        "2. SENTIMEN DOLLAR (DXY) — Bullish/Bearish/Netral, dengan alasan singkat dari data di atas.\n"
        "3. BIAS XAU/USD (GOLD) — WAJIB pakai arah bias Daily dari 'BIAS TEKNIKAL TERHITUNG' di atas untuk "
        "XAUUSD, JANGAN menentukan arah sendiri dan JANGAN membalik arahnya. Sebutkan status alignment "
        "Daily-Weekly-Monthly, lalu jelaskan apakah berita makro di atas mendukung atau berlawanan dengan "
        "bias teknikal itu. Kalau narrative bias untuk XAUUSD berstatus 'Data tidak tersedia', katakan itu "
        "secara eksplisit, jangan mengarang arah.\n"
        "4. BIAS FOREX MAYOR — sama seperti poin 3, untuk EUR/USD, GBP/USD, USD/JPY. Satu baris per pair: "
        "WAJIB pakai arah dari 'BIAS TEKNIKAL TERHITUNG' (jangan menebak sendiri), + status alignment, + "
        "apakah macro news mendukung/melawan arah tersebut.\n"
        "5. EVENT PERLU DIWASPADAI HARI INI — daftar event high-impact dari kalender di atas beserta jam WIB "
        "(konversi dari waktu event, asumsikan waktu event dalam format kalender adalah EST/EDT New York kalau "
        "tidak disebutkan zona lain), atau tulis \"Tidak ada\" kalau memang kosong.\n"
        "6. Tutup dengan satu baris disclaimer singkat bahwa ini bukan rekomendasi finansial, dan bahwa bias "
        "adalah skenario probabilitas tertinggi, bukan prediksi pasti.\n\n"
        "Gunakan bullet points (•), JANGAN gunakan tanda bintang (*). Bahasa Indonesia. Ringkas dan actionable, "
        "jangan bertele-tele."
    )

    return analyze_with_groq(prompt, system_prompt=MACRO_SYSTEM_PROMPT)


# ── Keyword Detection ─────────────────────────────────────────

PRICE_KEYWORDS = {
    "harga", "price", "berapa", "xauusd", "eurusd",
    "gbpusd", "usdjpy", "gold", "emas", "saat ini", "sekarang",
}

PAIR_MAP = {
    "xauusd": "XAU/USD",
    "gold": "XAU/USD",
    "emas": "XAU/USD",
    "eurusd": "EUR/USD",
    "gbpusd": "GBP/USD",
    "usdjpy": "USD/JPY",
}

CALENDAR_KEYWORDS = {
    "kalender", "calendar", "jadwal", "event", "minggu ini", "hari ini",
    "high impact", "rilis", "nfp", "cpi", "fomc", "gdp", "actual", "hasil",
}

REGIME_KEYWORDS = {
    "regime", "bias", "kondisi market", "trending", "choppy", "analisis", "teknikal",
}

NEWS_SEARCH_KEYWORDS = {
    "news", "berita", "speaks", "pidato", "statement",
    "hawkish", "dovish", "terbaru", "breaking",
}


def is_price_question(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in PRICE_KEYWORDS)


def extract_pair(text: str):
    lower = text.lower()
    for keyword, symbol in PAIR_MAP.items():
        if keyword in lower:
            return symbol
    return None


def is_calendar_question(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in CALENDAR_KEYWORDS)


def is_regime_question(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in REGIME_KEYWORDS)


def is_news_question(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in NEWS_SEARCH_KEYWORDS)


# ── Calendar (Forex Factory RSS) ──────────────────────────────


def _parse_ff_date(date_str: str):
    """Parse tanggal dari ForexFactory RSS (format umum: 'M-D-YYYY'). Return date object atau None kalau gagal."""
    for fmt in ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


_calendar_raw_cache = {"events": None, "fetched_at": 0, "last_attempt_at": 0}
CALENDAR_CACHE_TTL = 300  # 5 menit — samain sama window rate-limit FF (2 req/5menit)
                          # buat feed export gratis. Sebelumnya get_calendar()
                          # SAMA SEKALI TIDAK caching, jadi tiap /calendar, /high,
                          # /bias, /eventpreview, chat AI, macro briefing, DAN
                          # scheduler tiap 15 menit semuanya fetch fresh ke FF —
                          # gampang banget nembus limit mereka dan malah dapat
                          # balasan HTML "Request Denied" yang gagal diparse XML
                          # ("mismatched tag") — ini akar dari banyak error
                          # kalender yang kejadian sepanjang pengembangan bot ini.
CALENDAR_RETRY_BACKOFF = 60  # kalau fetch gagal, jangan coba lagi < 60 detik
                              # (mencegah retry storm yang malah memperpanjang rate-limit)


BIQUOTE_IMPORTANCE_MAP = {"high": "High", "medium": "Medium", "low": "Low"}


def _fetch_biquote_calendar_raw():
    """Ambil kalender dari biquote.io (JSON proper, actual/forecast/previous
    eksplisit null kalau belum ada -- gak perlu tebak-tebak string 'N/A'
    kayak FF RSS). Return list of dict format SAMA dengan _fetch_calendar_raw
    (FF), atau None kalau gagal (caller fallback ke FF RSS).

    Field 'country' di sini diisi dari 'currency' biquote (USD/EUR/GBP/dst)
    -- konsisten dengan seluruh kode yang sebenarnya filter by CURRENCY
    code, bukan country code beneran (peninggalan konvensi FF)."""
    try:
        now_utc = datetime.now(ZoneInfo("UTC"))
        from_dt = (now_utc - pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        to_dt = (now_utc + pd.Timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
        resp = requests.get(
            f"{BIQUOTE_BASE}/api/calendar",
            params={"from": from_dt, "to": to_dt, "limit": 500},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"biquote calendar status {resp.status_code}")
            return None
        events = resp.json()
        if not isinstance(events, list):
            print("biquote calendar: response bukan list")
            return None

        result = []
        for e in events:
            event_dt_utc = pd.to_datetime(e.get("time"), utc=True, errors="coerce")
            if pd.isna(event_dt_utc):
                continue
            event_dt_ny = event_dt_utc.tz_convert(ZoneInfo("America/New_York"))
            result.append({
                "title": e.get("name", ""),
                "country": e.get("currency", ""),
                "date": event_dt_ny.strftime("%m-%d-%Y"),
                "time": event_dt_ny.strftime("%I:%M%p").lower().lstrip("0") or "12:00am",
                "impact": BIQUOTE_IMPORTANCE_MAP.get(str(e.get("importance", "")).lower(), ""),
                "forecast": str(e["forecast"]) if e.get("forecast") is not None else "N/A",
                "previous": str(e["previous"]) if e.get("previous") is not None else "N/A",
                "actual": str(e["actual"]) if e.get("actual") is not None else "N/A",
            })
        return result
    except Exception as e:
        print(f"biquote calendar fetch gagal: {e}")
        return None


def _fetch_calendar_raw():
    """Fetch + parse mentah kalender, di-cache CALENDAR_CACHE_TTL detik.
    Coba biquote.io dulu (JSON proper, limit jauh lebih longgar daripada FF
    RSS yang cuma 2 request/5menit) — kalau gagal, fallback ke Forex
    Factory RSS seperti sebelumnya. Return list of dict semua event (semua
    country/impact) tanpa filter, atau None kalau KEDUANYA gagal. Filter
    country/impact/date dilakukan di get_calendar() SETELAH ambil dari
    cache ini."""
    now = time.time()
    if _calendar_raw_cache["events"] is not None and (now - _calendar_raw_cache["fetched_at"]) < CALENDAR_CACHE_TTL:
        return _calendar_raw_cache["events"]

    if (now - _calendar_raw_cache["last_attempt_at"]) < CALENDAR_RETRY_BACKOFF:
        # Baru aja gagal coba fetch, jangan langsung coba lagi -- pakai data
        # cache lama kalau ada (mending data agak basi daripada spam pas
        # lagi kena rate-limit), atau None kalau memang belum pernah berhasil.
        return _calendar_raw_cache["events"]

    _calendar_raw_cache["last_attempt_at"] = now

    bq_events = _fetch_biquote_calendar_raw()
    if bq_events is not None:
        _calendar_raw_cache["events"] = bq_events
        _calendar_raw_cache["fetched_at"] = now
        return bq_events

    print("biquote calendar gagal, fallback ke Forex Factory RSS")
    try:
        response = requests.get(FF_RSS_URL, timeout=10)
        content_start = response.content[:200].strip().lower()
        if content_start.startswith(b"<!doctype") or content_start.startswith(b"<html"):
            # FF balikin HTML, bukan XML — ini tandanya kena rate-limit
            # ("Request Denied" page), bukan error jaringan biasa.
            print("Error fetching calendar: FF mengembalikan HTML (kemungkinan kena rate-limit export)")
            return _calendar_raw_cache["events"]  # fallback ke cache lama kalau ada

        root = ET.fromstring(response.content)
        events = []
        for event in root.iter("event"):
            events.append({
                "title": event.findtext("title", ""),
                "country": event.findtext("country", ""),
                "date": event.findtext("date", ""),
                "time": event.findtext("time", ""),
                "impact": event.findtext("impact", ""),
                "forecast": event.findtext("forecast", "N/A"),
                "previous": event.findtext("previous", "N/A"),
                "actual": event.findtext("actual", "N/A"),
            })
        _calendar_raw_cache["events"] = events
        _calendar_raw_cache["fetched_at"] = now
        return events
    except Exception as e:
        print(f"Error fetching calendar: {e}")
        return _calendar_raw_cache["events"]  # fallback ke cache lama kalau ada


def get_calendar(filter_country="USD", filter_impact=None, only_today=False):
    """Ambil event dari ForexFactory RSS (feed mingguan) — hasil mentah
    di-cache 5 menit (lihat _fetch_calendar_raw), filter country/impact/date
    dilakukan di sini per-panggilan supaya tetap fleksibel tanpa fetch ulang.

    only_today=True akan membuang event yang tanggalnya bukan hari ini
    (dibandingkan di timezone America/New_York, karena FF RSS memakai
    waktu Eastern). Tanpa filter ini, feed berisi SATU MINGGU penuh
    event, jadi klaim 'event hari ini' di briefing bisa salah kalau
    tidak difilter di sini.
    """
    raw_events = _fetch_calendar_raw()
    if raw_events is None:
        return None  # None = gagal fetch (beda dari [] = berhasil tapi memang kosong)

    events = []
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    skipped_unparsed = 0
    for e in raw_events:
        if e["country"] != filter_country:
            continue
        if filter_impact and e["impact"] != filter_impact:
            continue
        if only_today:
            event_date = _parse_ff_date(e["date"])
            if event_date is None:
                skipped_unparsed += 1
                continue
            if event_date != today_ny:
                continue
        events.append({
            "title": e["title"], "date": e["date"], "time": e["time"], "impact": e["impact"],
            "forecast": e["forecast"], "previous": e["previous"], "actual": e["actual"],
        })
    if only_today and skipped_unparsed:
        print(f"get_calendar: {skipped_unparsed} event dibuang karena format tanggal tak dikenal")
    return events


def _calendar_status_note(events) -> str:
    """Pesan status dipakai saat events kosong/gagal, supaya user & AI bisa
    bedain 'API Forex Factory gagal diakses' vs 'memang tidak ada event'."""
    if events is None:
        return (
            "⚠️ Gagal mengambil data kalender ekonomi dari Forex Factory saat ini "
            "(kemungkinan timeout/error jaringan). Ini BUKAN berarti tidak ada event — "
            "datanya sedang tidak bisa diakses. Coba lagi beberapa saat lagi."
        )
    return "Tidak ada event yang cocok dengan kriteria ini (data berhasil diambil, memang kosong)."


def _parse_ff_datetime(date_str: str, time_str: str):
    """Gabungkan date+time FF jadi datetime lengkap (timezone NY). Return
    None kalau time-nya non-standar (mis. 'All Day', 'Tentative', 'Day 1')."""
    d = _parse_ff_date(date_str)
    if d is None:
        return None
    try:
        t = datetime.strptime(time_str.strip().upper(), "%I:%M%p").time()
    except (ValueError, AttributeError):
        return None
    return datetime.combine(d, t, tzinfo=ZoneInfo("America/New_York"))


def format_calendar_for_prompt(events) -> str:
    if not events:
        return _calendar_status_note(events)

    now_ny = datetime.now(ZoneInfo("America/New_York"))
    lines = [f"(Waktu sekarang: {now_ny.strftime('%A, %d %B %Y %H:%M')} waktu New York/Eastern)"]
    for e in events:
        impact_label = f"[{e['impact'].upper()}]" if e["impact"] else ""
        actual = e.get("actual", "N/A")
        event_dt = _parse_ff_datetime(e["date"], e["time"])

        if actual and actual != "N/A":
            status = f"Actual: {actual}"
        elif event_dt is not None and event_dt < now_ny:
            # Waktunya sudah lewat tapi field actual masih kosong di data —
            # JANGAN bilang 'belum dirilis' (itu menyiratkan masih di masa
            # depan, padahal salah). Ini keterbatasan sumber data.
            status = (
                "Actual: TIDAK TERSEDIA di data (event ini SUDAH LEWAT waktunya — "
                "kemungkinan besar sudah rilis di dunia nyata, tapi datanya belum ter-update "
                "di feed ini. JANGAN bilang 'belum dirilis' atau 'masih di masa depan' "
                "untuk event ini)"
            )
        else:
            status = "Actual: belum dirilis (belum waktunya)"

        lines.append(
            f"- {impact_label} {e['title']} | {e['date']} {e['time']} "
            f"| Forecast: {e['forecast']} | Previous: {e['previous']} | {status}"
        )
    return "\n".join(lines)


# ── Groq Text ────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "Kamu adalah Bayproject.fx AI Agent, analis forex profesional. "
    "Kamu memiliki akses ke data realtime: harga, kalender ekonomi, indikator teknikal, "
    "dan berita terkini yang diinject sebagai konteks. "
    "Selalu gunakan data yang diinject, JANGAN gunakan memori training untuk data harga, jadwal, atau berita. "
    "Jawab dalam Bahasa Indonesia. Analisis konkret dan actionable. "
    "Jika data tersedia, selalu sebutkan angka spesifik bukan perkiraan umum. "
    "Gunakan bullet points (•) untuk daftar, JANGAN gunakan tanda bintang (*) karena akan merusak format pesan Telegram.\n\n"
    "PENTING soal batasan kemampuanmu — JANGAN DILANGGAR:\n"
    "Kamu TIDAK punya kemampuan untuk menjadwalkan atau mengirim pesan otomatis di masa depan "
    "hanya dengan menjanjikannya di percakapan biasa. Satu-satunya cara fitur terjadwal beneran "
    "aktif adalah user mengetik command spesifik ini sendiri:\n"
    "- /macroon (Telegram) atau !macroon (Discord): briefing makro otomatis 3x/hari\n"
    "- /debriefon atau !debriefon: rangkuman pasar harian otomatis\n"
    "- /sessionon atau !sessionon: notifikasi tiap sesi trading dibuka\n"
    "- /alert atau !alert [pair] [operator] [harga]: custom price alert\n"
    "- /watch atau !watch [pair]: auto-signal & trap alert utk pair itu\n\n"
    "JANGAN PERNAH bilang 'saya akan kirim update besok pagi', 'saya akan kasih tau kalau harga "
    "tembus level X', atau janji serupa lainnya di chat bebas — itu BOHONG, kamu tidak benar-benar "
    "bisa melakukan itu hanya dengan mengatakannya. Kalau user minta update/alert otomatis, "
    "SEBUTKAN command yang relevan dari daftar di atas supaya mereka ketik sendiri, jangan "
    "berpura-pura kamu yang akan mengurusnya."
)


MACRO_SYSTEM_PROMPT = (
    "Kamu adalah Bayproject.fx AI Agent, analis makro forex profesional. "
    "Kamu HANYA boleh menyebutkan angka (harga, level, persentase) yang MUNCUL SECARA EKSPLISIT "
    "di data yang diinject ke prompt. JANGAN pernah menyebutkan angka spesifik (harga, level "
    "psikologis, target) hasil perkiraan atau ingatan training kamu sendiri — kalau datanya "
    "tidak menyebut angka pasti, gunakan bahasa kualitatif (mis. 'melemah', 'mendekati area kunci') "
    "tanpa angka, atau katakan datanya tidak menyebutkan angka spesifik. "
    "Jawab dalam Bahasa Indonesia. Gunakan bullet points (•), JANGAN gunakan tanda bintang (*)."
)


SYSTEM_PROMPT_AGENTIC = (
    "Kamu adalah Bayproject.fx AI Agent, analis forex profesional. "
    "Kamu memiliki akses ke data realtime: harga, kalender ekonomi, indikator teknikal, "
    "dan berita terkini yang diinject sebagai konteks. "
    "Selalu gunakan data yang diinject, JANGAN gunakan memori training untuk data harga, jadwal, atau berita. "
    "Jawab dalam Bahasa Indonesia. Analisis konkret dan actionable. "
    "Jika data tersedia, selalu sebutkan angka spesifik bukan perkiraan umum. "
    "Gunakan bullet points (•) untuk daftar, JANGAN gunakan tanda bintang (*) karena akan merusak format pesan Telegram.\n\n"
    "PENTING — kamu SEKARANG punya akses tool beneran (bukan cuma ngomong):\n"
    "Kalau user minta aktifin/matiin langganan, bikin alert harga, atau atur watchlist, kamu BISA dan "
    "HARUS panggil tool yang sesuai (subscribe_daily_debrief, create_price_alert, dst) — itu akan "
    "BENERAN mengubah setting mereka di database, bukan cuma janji kosong. Setelah tool selesai "
    "dieksekusi, konfirmasi ke user dengan bahasa natural berdasarkan hasil tool tsb.\n\n"
    "Batasan tetap berlaku untuk hal yang TIDAK ada tool-nya: kamu TIDAK bisa menjadwalkan hal lain "
    "di luar tool yang tersedia, dan TIDAK bisa mengeksekusi trade/order beneran (bot ini tidak "
    "terhubung ke broker apapun) — untuk permintaan begitu, jelaskan keterbatasannya dengan jujur, "
    "jangan berpura-pura bisa."
)


AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "subscribe_daily_debrief",
        "description": "Aktifkan langganan update harian otomatis (dikirim jam 05:00 WIB tiap hari, "
                        "berisi ringkasan multi-pair + event kalender hari itu) untuk user ini.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "unsubscribe_daily_debrief",
        "description": "Matikan langganan update harian otomatis untuk user ini.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "subscribe_macro_briefing",
        "description": "Aktifkan langganan insight makro & bias otomatis 3x/hari (sesi Asia/London/New York).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "unsubscribe_macro_briefing",
        "description": "Matikan langganan insight makro otomatis.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "subscribe_session_reminder",
        "description": "Aktifkan notifikasi otomatis setiap sesi trading (Asia/London/New York) dibuka.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "unsubscribe_session_reminder",
        "description": "Matikan notifikasi sesi trading otomatis.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "create_price_alert",
        "description": "Buat alert harga: user diberi tahu SEKALI saat harga pair tertentu mencapai target.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Pair, mis. XAUUSD, EURUSD, GBPJPY"},
                "operator": {"type": "string", "enum": [">", "<", ">=", "<="]},
                "target_price": {"type": "number"},
            },
            "required": ["symbol", "operator", "target_price"],
        },
    }},
    {"type": "function", "function": {
        "name": "add_watchlist",
        "description": "Tambahkan pair ke watchlist user -- akan dapat auto-signal & trap alert otomatis "
                        "untuk pair itu.",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    }},
    {"type": "function", "function": {
        "name": "remove_watchlist",
        "description": "Hapus pair dari watchlist user.",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    }},
]


def _execute_agent_tool(tool_name: str, tool_args: dict, user_id: int, chat_id: int, platform: str) -> str:
    """Eksekusi tool call dari AI — INI yang beneran mengubah database
    (subscribe/unsubscribe/alert/watchlist), bukan sekadar teks. Semua tool
    di sini sengaja dibatasi ke aksi yang REVERSIBLE & RENDAH RISIKO — user
    bisa undo kapan aja lewat command manual (macrooff, unwatch, unalert,
    dst), dan tidak ada satupun yang mengeksekusi order/trade beneran."""
    try:
        if not isinstance(tool_args, dict):
            return "Gagal: argumen tool harus object."
        if tool_name in ("create_price_alert", "add_watchlist", "remove_watchlist"):
            if not re.fullmatch(r"[A-Za-z]{3}/?[A-Za-z]{3}", str(tool_args.get("symbol", ""))):
                return "Gagal: format pair tidak valid."
        if tool_name == "subscribe_daily_debrief":
            add_debrief_sub(user_id, chat_id, platform=platform)
            return "Berhasil: langganan update harian (jam 05:00 WIB) diaktifkan."
        elif tool_name == "unsubscribe_daily_debrief":
            remove_debrief_sub(user_id, platform=platform)
            return "Berhasil: langganan update harian dimatikan."
        elif tool_name == "subscribe_macro_briefing":
            add_macro_sub(user_id, chat_id, platform=platform)
            return "Berhasil: langganan macro briefing 3x/hari diaktifkan."
        elif tool_name == "unsubscribe_macro_briefing":
            remove_macro_sub(user_id, platform=platform)
            return "Berhasil: langganan macro briefing dimatikan."
        elif tool_name == "subscribe_session_reminder":
            add_session_sub(user_id, chat_id, platform=platform)
            return "Berhasil: notifikasi sesi trading (Asia/London/NY) diaktifkan."
        elif tool_name == "unsubscribe_session_reminder":
            remove_session_sub(user_id, platform=platform)
            return "Berhasil: notifikasi sesi trading dimatikan."
        elif tool_name == "create_price_alert":
            symbol = format_symbol(tool_args["symbol"])
            operator = tool_args["operator"]
            target_price = float(tool_args["target_price"])
            result = add_price_alert(user_id, chat_id, symbol, operator, target_price, platform=platform)
            if "error" in result:
                return f"Gagal membuat alert: {result['error']}"
            return f"Berhasil: alert #{result['id']} dibuat untuk {symbol} {operator} {target_price}."
        elif tool_name == "add_watchlist":
            symbol = format_symbol(tool_args["symbol"])
            add_watch(user_id, chat_id, symbol, platform=platform)
            return f"Berhasil: {symbol} ditambahkan ke watchlist."
        elif tool_name == "remove_watchlist":
            symbol = format_symbol(tool_args["symbol"])
            remove_watch(user_id, symbol, platform=platform)
            return f"Berhasil: {symbol} dihapus dari watchlist."
        else:
            return f"Tool '{tool_name}' tidak dikenal."
    except Exception as ex:
        print(f"Gagal eksekusi agent tool {tool_name}: {ex}")
        return f"Gagal eksekusi: {ex}"


def analyze_with_groq_agentic(prompt, user_id: int, chat_id: int, platform: str,
                               conversation_history=None, system_prompt=None) -> str:
    """Sama seperti analyze_with_groq, TAPI AI dikasih akses TOOL CALLING
    beneran (AGENT_TOOLS) — kalau user minta 'aktifin update harian ya',
    AI bisa BENERAN mengeksekusi itu (lewat _execute_agent_tool), bukan
    cuma janji di teks doang. Alur: (1) kirim prompt+tools ke model, (2)
    kalau model minta tool call, eksekusi & kirim hasilnya balik ke model,
    (3) model kasih respons natural final berdasarkan hasil eksekusi."""
    hdrs = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT_AGENTIC}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": 2000,
        "reasoning_effort": "low",
        "tools": AGENT_TOOLS,
        "tool_choice": "auto",
    }
    execution_results = []
    try:
        response = chat_completion(payload, timeout=30)
        data = response.json()
        if "choices" not in data:
            err_msg = data.get("error", {}).get("message", str(data))
            print(f"Groq API error (agentic, model={GROQ_MODEL}): {err_msg}")
            return f"Error dari Groq API: {err_msg}"

        msg = data["choices"][0]["message"]
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            content = msg.get("content", "")
            if not content or not content.strip():
                return "⚠️ AI tidak berhasil menghasilkan jawaban kali ini. Coba lagi."
            return content

        # Model minta 1+ tool dieksekusi -- jalankan semua, lapor hasilnya balik ke model.
        messages.append(msg)
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                fn_args = {}
            result_text = _execute_agent_tool(fn_name, fn_args, user_id, chat_id, platform)
            execution_results.append(result_text)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})

        # Panggilan kedua: minta model rangkum hasil eksekusi jadi respons natural.
        payload2 = {
            "model": GROQ_MODEL, "messages": messages,
            "max_tokens": 500, "reasoning_effort": "low",
        }
        response2 = chat_completion(payload2, timeout=30)
        data2 = response2.json()
        if "choices" not in data2:
            return "Hasil aksi:\n" + "\n".join(execution_results)
        content2 = data2["choices"][0]["message"].get("content", "")
        return content2 if content2 and content2.strip() else "Hasil aksi:\n" + "\n".join(execution_results)
    except Exception as e:
        if execution_results:
            return "Hasil aksi (ringkasan AI tidak tersedia):\n" + "\n".join(execution_results)
        return f"Error koneksi ke Groq: {str(e)}"


def analyze_with_groq(prompt, conversation_history=None, system_prompt=None):
    hdrs = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": 2000,  # openai/gpt-oss-120b adalah reasoning model -- token budget
                              # kepakai buat "reasoning tokens" internal SEBELUM jawaban final.
                              # Nilai lama (700) kadang habis semua buat reasoning doang,
                              # nyisain 0 token buat jawaban -> content kosong (mis. macro
                              # briefing muncul header+footer doang tanpa isi). 2000 kasih
                              # ruang cukup buat reasoning + jawaban lengkap.
        "reasoning_effort": "low",  # task kita (sintesis data jadi laporan, jawab pertanyaan
                                    # trading) gak butuh penalaran berat ala soal matematika/
                                    # coding kompleks -- "low" nyisain lebih banyak token buat
                                    # jawaban aktual, bukan proses mikir yang gak perlu segitu
                                    # dalam. Parameter ini didukung khusus GPT-OSS di Groq.
    }
    try:
        response = chat_completion(payload, timeout=30)
        data = response.json()
        if "choices" not in data:
            # Bukan error koneksi — Groq berhasil dihubungi tapi menolak
            # request (mis. model deprecated/invalid, quota habis, dst).
            # Tampilkan pesan asli dari Groq, jangan digeneralisir jadi
            # "error koneksi" yang menyesatkan diagnosis.
            err_msg = data.get("error", {}).get("message", str(data))
            print(f"Groq API error (model={GROQ_MODEL}): {err_msg}")
            return f"Error dari Groq API: {err_msg}"

        content = data["choices"][0]["message"]["content"]
        if not content or not content.strip():
            # Groq berhasil dihubungi, "choices" ada, tapi content-nya kosong.
            # Ini pola gagal yang diketahui pada reasoning model (GPT-OSS)
            # kalau token budget habis di reasoning sebelum sempat nulis
            # jawaban (finish_reason biasanya "length"). Jangan diam-diam
            # kirim pesan kosong ke user -- itu yang bikin macro briefing
            # kemarin muncul header+footer doang tanpa isi.
            finish_reason = data["choices"][0].get("finish_reason", "unknown")
            print(f"Groq API: content kosong dari model={GROQ_MODEL}, finish_reason={finish_reason}")
            return (
                "⚠️ AI tidak berhasil menghasilkan jawaban kali ini (kemungkinan token budget "
                "habis di reasoning). Coba lagi."
            )
        return content
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error koneksi ke Groq: {str(e)}"


# ── Groq Vision ──────────────────────────────────────────────


def detect_mime_type(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def analyze_image_with_groq(image_bytes: bytes, caption: str = None) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = detect_mime_type(image_bytes)
    question = (
        caption
        if caption
        else (
            "Ini adalah chart forex. Tolong analisis secara teknikal: "
            "identifikasi tren, support/resistance, pola candlestick atau chart pattern yang terlihat, "
            "dan berikan bias Bullish/Bearish/Netral dengan alasan."
        )
    )
    hdrs = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {
                        "type": "text",
                        "text": (
                            f"{SYSTEM_PROMPT}\n\n"
                            "Selalu sertakan: Tren, Support/Resistance, Pattern, dan Bias akhir.\n\n"
                            f"Pertanyaan: {question}"
                        ),
                    },
                ],
            }
        ],
        "max_tokens": 1500,  # sama alasannya kayak analyze_with_groq -- qwen3.6-27b juga
                              # punya thinking mode yang makan token budget kalau diaktifkan.
        "reasoning_effort": "none",  # analisa chart itu tugas deskriptif (baca apa yang
                                     # keliatan di gambar), bukan penalaran matematis/coding
                                     # kompleks -- non-thinking mode lebih cepat dan gak
                                     # beresiko habisin token buat "mikir" doang.
    }
    try:
        response = chat_completion(payload, timeout=60, vision=True)
        data = response.json()
        if "error" in data:
            err = data["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            print(f"Vision API error ({response.status_code}, model={GROQ_VISION_MODEL}): {err_msg}")
            return f"❌ API error: {err_msg}"

        content = data["choices"][0]["message"]["content"]
        if not content or not content.strip():
            finish_reason = data["choices"][0].get("finish_reason", "unknown")
            print(f"Groq Vision API: content kosong dari model={GROQ_VISION_MODEL}, finish_reason={finish_reason}")
            return "⚠️ AI tidak berhasil menganalisis gambar kali ini (kemungkinan token budget habis). Coba lagi."
        return content
    except Exception as e:
        print(f"Exception in analyze_image_with_groq: {e}")
        return f"Error analisis gambar: {str(e)}"


# ── Conversation History ─────────────────────────────────────
# Di-key per (platform, user_id) supaya user_id Telegram & Discord tidak
# tercampur histori percakapannya.

conversation_histories = {}


def get_history(user_id, platform: str = "telegram"):
    return conversation_histories.get((platform, user_id), [])


def add_to_history(user_id, role, content, platform: str = "telegram"):
    key = (platform, user_id)
    if key not in conversation_histories:
        conversation_histories[key] = []
    conversation_histories[key].append({"role": role, "content": content})
    if len(conversation_histories[key]) > 10:
        conversation_histories[key] = conversation_histories[key][-10:]


# ── Shared keyword-injection logic (handle_message + /ai) ────


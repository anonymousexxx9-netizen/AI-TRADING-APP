"""Single-owner mobile API. Start from backend/: uvicorn api:app --port 8000."""
import asyncio
import base64
import contextlib
import io
import json
import math
import os
from pathlib import Path
import secrets
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name('.env'))
os.environ.setdefault('MPLBACKEND', 'Agg')
import numpy as np
import pandas as pd
from PIL import Image, UnidentifiedImageError
from fastapi import FastAPI, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, ConfigDict, field_validator
import core
import storage

chat_lock = threading.Lock()
CHAT_PRESENTATION = '''
Format jawaban untuk layar HP:
- Jawab langsung dan mulai dengan kesimpulan singkat 1–2 kalimat.
- Untuk analisis pasar, gunakan maksimal empat bagian relevan: Ringkasan, Data penting,
  Dampak pada pair yang ditanya, dan Hal yang perlu dipantau. Lewati bagian yang tidak diperlukan.
- Gunakan heading Markdown dan bullet pendek. Satu poin satu gagasan; cetak tebal hanya angka atau istilah penting.
- Hindari tabel lebar, daftar semua instrumen yang tidak ditanya, dan pengulangan disclaimer.
- Default ringkas sekitar 150–250 kata; berikan rincian lebih panjang jika pengguna memintanya.
- Pisahkan data terverifikasi dari interpretasi. Jangan menciptakan harga, level entry/SL/TP,
  hasil event, atau sumber yang tidak tersedia dalam konteks. Pertahankan keterbatasan data.
- Untuk aksi tool, cukup jelaskan hasil aktual secara singkat; tidak perlu struktur analisis pasar.
'''
chart_lock = threading.Lock()
security = HTTPBearer(auto_error=False)


def authorized(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    expected = os.getenv('APP_ACCESS_TOKEN', '')
    if len(expected) < 32:
        raise HTTPException(503, 'APP_ACCESS_TOKEN server belum diatur (minimal 32 karakter).')
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(401, 'Kunci akses tidak valid.', headers={'WWW-Authenticate': 'Bearer'})


@contextlib.asynccontextmanager
async def lifespan(app):
    if len(os.getenv('APP_ACCESS_TOKEN', '')) < 32:
        raise RuntimeError('Set APP_ACCESS_TOKEN to a random secret of at least 32 characters.')
    Path(core.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    storage.init()
    task = None
    if os.getenv('ENABLE_WORKER', 'true').lower() == 'true':
        import worker
        task = asyncio.create_task(worker.run())
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title='Bayproject Private API', version='1.0.0', lifespan=lifespan,
              dependencies=[Depends(authorized)], docs_url=None, redoc_url=None, openapi_url=None)


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, Decimal):
        return clean(float(value))
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def result(value):
    if isinstance(value, dict) and value.get('error'):
        raise HTTPException(502, str(value['error']))
    return clean(value)


def ai_result(value):
    if not value or value.startswith(('Error ', '❌', '⚠️ AI')):
        raise HTTPException(502, 'Layanan AI belum siap atau gagal merespons. Periksa konfigurasi model/provider di server.')
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Pair(Input):
    symbol: str = Field(default='XAU/USD', min_length=6, max_length=7, pattern=r'^[A-Za-z]{3}/?[A-Za-z]{3}$')

    @field_validator('symbol')
    @classmethod
    def format_pair(cls, v):
        return core.format_symbol(v)


class MarketInput(Pair):
    interval: Literal['1min', '5min', '15min', '1h', '4h', '1day'] = '1h'
    candles: int = Field(default=500, ge=120, le=1000)


class AlertInput(Pair):
    operator: Literal['>', '<', '>=', '<='] = '>='
    target_price: float = Field(gt=0, le=1e9)


class LotInput(Pair):
    balance: float = Field(gt=0, le=1e12)
    risk_percent: float = Field(gt=0, le=100)
    sl_pips: float = Field(gt=0, le=1e9)


class MessageInput(Input):
    message: str = Field(min_length=1, max_length=6000)
    image: str | None = Field(default=None, max_length=7_000_000)


class TextInput(Input):
    query: str = Field(default='gold USD forex', min_length=1, max_length=300)


class SubscriptionInput(Input):
    enabled: bool


class DeviceInput(Input):
    token: str = Field(pattern=r'^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]+\]$', max_length=256)


class CorrelationInput(Input):
    symbols: list[str] = Field(default=['XAU/USD', 'EUR/USD', 'GBP/USD', 'USD/JPY'], min_length=2, max_length=6)

    @field_validator('symbols')
    @classmethod
    def validate_pairs(cls, values):
        normalized = [Pair(symbol=v).symbol for v in values]
        if len(set(normalized)) < 2:
            raise ValueError('Pilih minimal dua pair berbeda.')
        return list(dict.fromkeys(normalized))


@app.get('/health')
def health():
    return {'status': 'ok', 'ai_configured': bool(os.getenv('AI_API_KEY') or os.getenv('GROQ_API_KEY')),
            'text_model': core.GROQ_MODEL, 'vision_model': core.GROQ_VISION_MODEL,
            'worker_enabled': os.getenv('ENABLE_WORKER', 'true').lower() == 'true',
            'worker_last_tick': core.get_setting('mobile_worker_tick'), 'timezone': 'Asia/Jakarta'}


@app.get('/watchlist')
def watchlist():
    return [dict(r) for r in core.list_watch(1, 'mobile')]


@app.post('/watchlist')
def watch_add(body: Pair):
    core.add_watch(1, 1, body.symbol, 'mobile')
    return watchlist()


@app.delete('/watchlist')
def watch_remove(body: Pair):
    core.remove_watch(1, body.symbol, 'mobile')
    return watchlist()


@app.post('/market/price')
def price(body: Pair):
    return {**result(core.get_forex_price(body.symbol)), 'fetched_at': datetime.now(timezone.utc).isoformat()}


@app.post('/market/analysis')
def analysis(body: MarketInput):
    df = core.get_ohlcv(body.symbol, body.interval, 200)
    if isinstance(df, dict):
        return result(df)
    if df is None or len(df) < 50:
        raise HTTPException(502, 'Data candle belum cukup.')
    indicators = core.calculate_indicators(df)
    sr = core.get_support_resistance(df)
    return result({'symbol': body.symbol, 'interval': body.interval,
                   'indicators': indicators, 'confidence': core.calculate_confidence(indicators),
                   'regime': core.get_regime(indicators), 'sr': sr,
                   'trap': core.detect_trap(df, indicators),
                   'pattern': core.detect_candlestick_pattern(df),
                   'structure': core.detect_market_structure(df),
                   'candles': df.tail(80).to_dict(orient='records'),
                   'fetched_at': datetime.now(timezone.utc).isoformat()})


@app.post('/market/chart')
def chart(body: MarketInput):
    df = core.get_ohlcv(body.symbol, body.interval, 200)
    if isinstance(df, dict):
        return result(df)
    with chart_lock:
        png = core.generate_chart_image(df, body.symbol, body.interval, core.get_support_resistance(df))
    return {'image': base64.b64encode(png).decode()}


@app.post('/market/confluence')
def confluence(body: Pair):
    return result(core.get_confluence(body.symbol))


@app.post('/market/explain')
def explain(body: MarketInput):
    data = analysis(body)
    data.pop('candles', None)
    return {'text': ai_result(core.analyze_with_groq(
        'Jelaskan analisis teknikal berikut dalam bahasa Indonesia. Gunakan hanya angka yang tersedia. '
        'Technical score bukan probabilitas profit.\n' + json.dumps(data, ensure_ascii=False)))}


@app.post('/signals/scan')
def scan(body: MarketInput):
    return result(core.generate_scan_signal(body.symbol, body.interval))


@app.post('/signals/entry')
def entry():
    return result(core.generate_xau_entry_signal())


@app.post('/signals/backtest')
def backtest(body: MarketInput):
    return result(core.backtest_scan_signal(body.symbol, body.interval, body.candles))


@app.post('/tools/lot')
def lot(body: LotInput):
    return result(core.calculate_lot_size(body.balance, body.risk_percent, body.sl_pips, body.symbol))


@app.post('/tools/correlation')
def correlation(body: CorrelationInput):
    return result(core.get_correlation_matrix(body.symbols))


@app.get('/calendar')
def calendar(high: bool = False, today: bool = False):
    events = core.get_calendar('USD', 'High' if high else None, today)
    if events is None:
        raise HTTPException(502, 'Feed kalender tidak tersedia.')
    return clean(events)


@app.post('/calendar/preview')
def event_preview(body: TextInput):
    events = core.get_calendar('USD')
    if events is None:
        raise HTTPException(502, 'Feed kalender tidak tersedia.')
    matched = [e for e in events if body.query.lower() in e['title'].lower()]
    if not matched:
        raise HTTPException(404, 'Event tidak ditemukan dalam kalender minggu ini.')
    return {'text': ai_result(core.generate_event_prediction(matched[0]))}


@app.post('/calendar/bias')
def event_bias(body: TextInput):
    events = core.get_calendar('USD')
    if events is None:
        raise HTTPException(502, 'Feed kalender tidak tersedia.')
    context = core.format_calendar_for_prompt(events)
    return {'text': ai_result(core.analyze_with_groq(f'Analisis bias fundamental {body.query}. Gunakan data ini, nyatakan jika tidak tersedia:\n{context}'))}


@app.post('/calendar/actual')
def calendar_actual():
    events = core.get_calendar('USD')
    if events is None:
        raise HTTPException(502, 'Feed kalender tidak tersedia.')
    return {'text': core.get_calendar_actual_fallback(events) or 'Tidak ada data actual tambahan.'}


@app.post('/reports/{kind}')
def report(kind: Literal['news', 'macro', 'debrief'], body: TextInput):
    fn = {'news': lambda: core.search_news(body.query), 'macro': core.generate_macro_briefing,
          'debrief': core.generate_daily_debrief}[kind]
    return {'text': ai_result(fn())}


@app.get('/alerts')
def alerts():
    return [dict(r) for r in core.list_price_alerts(1, 'mobile')]


@app.post('/alerts')
def add_alert(body: AlertInput):
    return result(core.add_price_alert(1, 1, body.symbol, body.operator, body.target_price, 'mobile'))


@app.delete('/alerts/{alert_id}')
def delete_alert(alert_id: int):
    if not core.remove_price_alert(1, alert_id, 'mobile'):
        raise HTTPException(404, 'Alert tidak ditemukan.')
    return {'ok': True}


SUBS = {'macro': (core.add_macro_sub, core.remove_macro_sub, core.get_macro_subs),
        'session': (core.add_session_sub, core.remove_session_sub, core.get_session_subs),
        'debrief': (core.add_debrief_sub, core.remove_debrief_sub, core.get_debrief_subs)}


@app.get('/subscriptions')
def subscriptions():
    return {key: any(r['platform'] == 'mobile' and r['user_id'] == 1 for r in getter())
            for key, (_, _, getter) in SUBS.items()}


@app.put('/subscriptions/{kind}')
def subscription(kind: Literal['macro', 'session', 'debrief'], body: SubscriptionInput):
    add, remove, _ = SUBS[kind]
    add(1, 1, 'mobile') if body.enabled else remove(1, 'mobile')
    return subscriptions()


@app.get('/notifications')
def notifications():
    with core.get_db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM mobile_inbox ORDER BY id DESC LIMIT 100')]


@app.post('/notifications/read')
def read_notifications():
    with core.get_db() as db:
        db.execute('UPDATE mobile_inbox SET read=1')
    return {'ok': True}


@app.post('/devices')
def register_device(body: DeviceInput):
    with core.get_db() as db:
        db.execute('INSERT OR IGNORE INTO mobile_devices(token) VALUES (?)', (body.token,))
    return {'ok': True}


@app.delete('/devices')
def remove_device(body: DeviceInput):
    with core.get_db() as db:
        db.execute('DELETE FROM mobile_devices WHERE token=?', (body.token,))
        db.execute("UPDATE mobile_deliveries SET status='removed' WHERE token=? AND status IN ('pending','ticket')", (body.token,))
    return {'ok': True}


@app.post('/notifications/test')
def test_notification():
    return {'id': storage.enqueue('Notifikasi uji Bayproject. Koneksi inbox berhasil.')}


@app.get('/chat')
def chat_history():
    return storage.history()


@app.delete('/chat')
def clear_chat():
    with chat_lock, core.get_db() as db:
        db.execute('DELETE FROM mobile_chat')
    return {'ok': True}


def build_context(message):
    pair = core.extract_pair(message) or 'XAU/USD'
    context = None
    if core.is_price_question(message):
        context = core.get_forex_price(pair)
    elif core.is_calendar_question(message):
        events = core.get_calendar('USD')
        context = core.format_calendar_for_prompt(events) if events is not None else 'Feed kalender gagal diambil.'
        if events and any(k in message.lower() for k in ('actual', 'hasil', 'sudah')):
            context += '\n' + core.get_calendar_actual_fallback(events)
    elif core.is_news_question(message):
        context = core.search_news(message)
    elif core.is_regime_question(message):
        context = core.full_analysis(pair)
    if context is None:
        return message
    return (f'Data yang diambil pada {datetime.now(timezone.utc).isoformat()}:\n'
            f'{json.dumps(clean(context), ensure_ascii=False)}\n'
            f'Jika data gagal/kurang, nyatakan keterbatasannya; jangan mengarang harga atau hasil event.\nPertanyaan: {message}')


@app.post('/chat')
def chat(body: MessageInput):
    if not body.message.strip():
        raise HTTPException(422, 'Pesan tidak boleh kosong.')
    # Serialize agent actions and history so double sends cannot interleave turns.
    with chat_lock:
        if body.image:
            try:
                raw = base64.b64decode(body.image, validate=True)
                if len(raw) > 5_000_000:
                    raise ValueError()
                with Image.open(io.BytesIO(raw)) as im:
                    if im.width * im.height > 20_000_000 or im.format not in ('PNG', 'JPEG', 'WEBP'):
                        raise ValueError()
                    im.verify()
            except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise HTTPException(422, 'Gunakan gambar PNG/JPEG/WebP valid, maksimal 5 MB / 20 megapiksel.')
            reply = ai_result(core.analyze_image_with_groq(raw, body.message))
        else:
            prior = [{'role': r['role'], 'content': r['content']} for r in storage.history(10)]
            reply = ai_result(core.analyze_with_groq_agentic(build_context(body.message), 1, 1, 'mobile', conversation_history=prior,
                system_prompt=core.SYSTEM_PROMPT_AGENTIC + '\n' + CHAT_PRESENTATION))
        storage.add_message('user', ('[Screenshot chart]\n' if body.image else '') + body.message)
        storage.add_message('assistant', reply)
    return {'text': reply}

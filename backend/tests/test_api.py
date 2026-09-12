import asyncio
import base64
import io
import os
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import ai_gateway
import core
import scheduler
import storage
import worker

TOKEN = 'test-private-token-' + 'x' * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_ACCESS_TOKEN', TOKEN)
    monkeypatch.setenv('ENABLE_WORKER', 'false')
    monkeypatch.setattr(core, 'DB_PATH', str(tmp_path / 'test.db'))
    # Fail any unintended external call. Tests use fixtures, not live market prices.
    monkeypatch.setattr('requests.get', Mock(side_effect=AssertionError('Unexpected network')))
    monkeypatch.setattr('requests.post', Mock(side_effect=AssertionError('Unexpected network')))
    with TestClient(api.app, headers={'Authorization': 'Bearer ' + TOKEN}) as c:
        yield c


def test_private_routes_and_no_docs(client):
    assert client.get('/health').status_code == 200
    assert client.get('/health', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    client.headers.clear()
    assert client.get('/watchlist').status_code == 401
    assert client.get('/docs').status_code == 404


def test_watchlist_alerts_and_subscriptions(client):
    assert client.post('/watchlist', json={'symbol': 'eurusd'}).json() == [{'symbol': 'EUR/USD'}]
    client.post('/watchlist', json={'symbol': 'EURUSD'})
    assert len(client.get('/watchlist').json()) == 1
    assert client.post('/watchlist', json={'symbol': '../../secret'}).status_code == 422
    alert = client.post('/alerts', json={'symbol': 'EURUSD', 'target_price': 1.2}).json()
    assert len(client.get('/alerts').json()) == 1
    assert client.delete(f"/alerts/{alert['id']}").status_code == 200
    assert client.delete(f"/alerts/{alert['id']}").status_code == 404
    assert client.post('/alerts', json={'target_price': -1}).status_code == 422
    assert client.put('/subscriptions/macro', json={'enabled': True}).json()['macro']
    assert not client.put('/subscriptions/macro', json={'enabled': False}).json()['macro']
    assert client.request('DELETE', '/watchlist', json={'symbol': 'EURUSD'}).json() == []


def test_chat_persists_and_injects_price_context(client, monkeypatch):
    monkeypatch.setattr(core, 'get_forex_price', lambda p: {'symbol': p, 'price': '123.45'})
    mock = Mock(return_value='Jawaban uji')
    monkeypatch.setattr(core, 'analyze_with_groq_agentic', mock)
    response = client.post('/chat', json={'message': 'harga EURUSD sekarang?'})
    assert response.status_code == 200
    assert '123.45' in mock.call_args.args[0]
    assert len(client.get('/chat').json()) == 2
    storage.init()  # Reinitializing storage must not discard history.
    assert len(storage.history()) == 2
    assert client.delete('/chat').status_code == 200
    assert client.get('/chat').json() == []


def test_ai_failure_not_saved_as_success(client, monkeypatch):
    monkeypatch.setattr(core, 'analyze_with_groq_agentic', lambda *a, **kw: 'Error koneksi ke Groq: unavailable')
    assert client.post('/chat', json={'message': 'halo'}).status_code == 502
    assert client.get('/chat').json() == []


def test_image_validation_and_vision(client, monkeypatch):
    assert client.post('/chat', json={'message': 'chart', 'image': 'invalid!'}).status_code == 422
    buf = io.BytesIO()
    Image.new('RGB', (10, 10)).save(buf, 'PNG')
    monkeypatch.setattr(core, 'analyze_image_with_groq', lambda *a: 'Chart uji')
    response = client.post('/chat', json={'message': 'chart', 'image': base64.b64encode(buf.getvalue()).decode()})
    assert response.json()['text'] == 'Chart uji'
    assert '[Screenshot chart]' in client.get('/chat').json()[0]['content']


def test_gateway_routes_and_strips_provider_specific_parameter(monkeypatch):
    monkeypatch.setenv('AI_API_KEY', 'fake-key')
    monkeypatch.setenv('AI_BASE_URL', 'https://router.invalid/v1/')
    monkeypatch.delenv('AI_REASONING_EFFORT', raising=False)
    post = Mock()
    monkeypatch.setattr(ai_gateway.requests, 'post', post)
    ai_gateway.chat_completion({'model': 'test/model', 'reasoning_effort': 'low', 'tools': [{'type': 'function'}]})
    assert post.call_args.args[0] == 'https://router.invalid/v1/chat/completions'
    assert 'reasoning_effort' not in post.call_args.kwargs['json']
    assert post.call_args.kwargs['json']['tools']


def test_agent_tool_action_and_failed_summary(client, monkeypatch):
    first = Mock()
    first.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': None,
        'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'create_price_alert',
        'arguments': '{"symbol":"EURUSD","operator":">","target_price":1.5}'}}]}}]}
    monkeypatch.setattr(core, 'chat_completion', Mock(side_effect=[first, TimeoutError('summary timeout')]))
    answer = core.analyze_with_groq_agentic('buat alert', 1, 1, 'mobile')
    assert 'Berhasil: alert #' in answer
    assert len(client.get('/alerts').json()) == 1
    assert 'Gagal' in core._execute_agent_tool('create_price_alert', {'symbol': 'EURUSD', 'operator': '>', 'target_price': float('nan')}, 1, 1, 'mobile')
    assert len(client.get('/alerts').json()) == 1


def test_failed_agent_tool_does_not_report_success(client, monkeypatch):
    first = Mock()
    first.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'a', 'function': {'name': 'create_price_alert', 'arguments': '{}'}}]}}]}
    second = Mock()
    second.json.return_value = {'error': {'message': 'failed'}}
    monkeypatch.setattr(core, 'chat_completion', Mock(side_effect=[first, second]))
    answer = core.analyze_with_groq_agentic('buat alert', 1, 1, 'mobile')
    assert 'Gagal' in answer and 'Berhasil' not in answer


def test_triggered_alert_is_durable_before_removed(client, monkeypatch):
    client.post('/alerts', json={'symbol': 'EURUSD', 'target_price': 1.2})
    monkeypatch.setattr(core, 'get_forex_price', lambda p: {'price': '1.3'})
    original = storage.enqueue
    monkeypatch.setattr(storage, 'enqueue', Mock(side_effect=OSError('disk failure')))
    with pytest.raises(OSError):
        asyncio.run(scheduler.check_price_alerts(None, None))
    assert len(client.get('/alerts').json()) == 1
    monkeypatch.setattr(storage, 'enqueue', original)
    asyncio.run(scheduler.check_price_alerts(None, None))
    assert client.get('/alerts').json() == []
    assert 'EUR/USD' in client.get('/notifications').json()[0]['body']


def test_schedule_catches_missed_minute_and_deduplicates(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Briefing uji')
    client.put('/subscriptions/macro', json={'enabled': True})
    now = datetime(2026, 9, 12, 7, 16, tzinfo=scheduler.JAKARTA)
    asyncio.run(worker.tick(False, now))
    asyncio.run(worker.tick(False, now))
    assert len(client.get('/notifications').json()) == 1


def test_push_ticket_receipt_and_unregister(client, monkeypatch):
    token = 'ExponentPushToken[test-device]'
    assert client.post('/devices', json={'token': token}).status_code == 200
    client.post('/notifications/test')
    ticket, receipt = Mock(), Mock()
    ticket.json.return_value = {'data': {'status': 'ok', 'id': 'ticket-1'}}
    receipt.json.return_value = {'data': {'ticket-1': {'status': 'ok'}}}
    monkeypatch.setattr(worker.requests, 'post', Mock(side_effect=[ticket, receipt]))
    worker.flush_push()
    with core.get_db() as db:
        assert db.execute('SELECT status FROM mobile_deliveries').fetchone()['status'] == 'delivered'
    client.request('DELETE', '/devices', json={'token': token})
    with core.get_db() as db:
        assert db.execute('SELECT count(*) FROM mobile_devices').fetchone()[0] == 0


@pytest.fixture
def candles():
    count = 220
    rng = np.random.default_rng(4)
    close = 2000 + np.cumsum(rng.normal(.1, 2, count))
    return pd.DataFrame({'datetime': pd.date_range('2025-01-01', periods=count, freq='h'),
        'open': close - .5, 'high': close + 3, 'low': close - 3, 'close': close, 'volume': rng.integers(100, 1000, count)})


def test_original_analysis_chart_backtest_and_lot(client, monkeypatch, candles):
    monkeypatch.setattr(core, 'get_ohlcv', lambda *a, **kw: candles.copy())
    monkeypatch.setattr(core, 'get_forex_price', lambda p: {'price': '2000', 'symbol': p})
    for endpoint in ['/market/analysis', '/market/chart', '/market/confluence', '/signals/scan', '/signals/backtest']:
        response = client.post(endpoint, json={'symbol': 'XAUUSD'})
        assert response.status_code == 200, (endpoint, response.text)
        if endpoint == '/market/chart':
            assert base64.b64decode(response.json()['image']).startswith(b'\x89PNG')
    response = client.post('/tools/lot', json={'symbol': 'EURUSD', 'balance': 1000, 'risk_percent': 1, 'sl_pips': 20})
    assert response.status_code == 200
    assert response.json()['risk_amount'] == 10
    assert api.clean({'bad': np.float64('nan')}) == {'bad': None}


def test_calendar_and_reporting_errors(client, monkeypatch):
    monkeypatch.setattr(core, 'get_calendar', lambda *a, **kw: None)
    assert client.get('/calendar').status_code == 502
    monkeypatch.setattr(core, 'get_calendar', lambda *a, **kw: [])
    assert client.get('/calendar').json() == []
    assert client.post('/calendar/preview', json={'query': 'CPI'}).status_code == 404
    assert client.post('/tools/lot', json={'balance': 0, 'risk_percent': 1, 'sl_pips': 0}).status_code == 422


def test_report_and_signal_endpoints_preserve_core_outputs(client, monkeypatch):
    monkeypatch.setattr(core, 'generate_xau_entry_signal', lambda: {'text': 'WAIT - fixture', 'entry_format_v2': True})
    assert client.post('/signals/entry').json()['entry_format_v2']
    monkeypatch.setattr(core, 'get_correlation_matrix', lambda syms: {'symbols': syms, 'matrix': {}})
    assert client.post('/tools/correlation', json={'symbols': ['EURUSD', 'USDJPY']}).json()['symbols'] == ['EUR/USD', 'USD/JPY']
    monkeypatch.setattr(core, 'get_calendar', lambda *a: [{'title': 'CPI y/y'}])
    monkeypatch.setattr(core, 'generate_event_prediction', lambda e: 'Preview fixture')
    assert client.post('/calendar/preview', json={'query': 'CPI'}).json()['text'] == 'Preview fixture'
    monkeypatch.setattr(core, 'search_news', lambda q: 'News fixture')
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro fixture')
    monkeypatch.setattr(core, 'generate_daily_debrief', lambda: 'Debrief fixture')
    for kind in ['news', 'macro', 'debrief']:
        assert client.post('/reports/' + kind, json={}).status_code == 200


def test_failed_scheduled_report_is_retried(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    client.put('/subscriptions/macro', json={'enabled': True})
    now = datetime(2026, 9, 12, 7, 16, tzinfo=scheduler.JAKARTA)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Error koneksi ke provider')
    asyncio.run(worker.tick(False, now))
    assert client.get('/notifications').json() == []
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Recovered report')
    asyncio.run(worker.tick(False, now))
    assert len(client.get('/notifications').json()) == 1

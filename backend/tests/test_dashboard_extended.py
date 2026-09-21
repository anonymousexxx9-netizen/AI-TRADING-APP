import asyncio
import json
from datetime import datetime
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import core
import scheduler
import worker

TOKEN = 'test-private-token-' + 'x' * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_ACCESS_TOKEN', TOKEN)
    monkeypatch.setenv('ENABLE_WORKER', 'false')
    monkeypatch.setattr(core, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr('requests.get', Mock(side_effect=AssertionError('Unexpected network')))
    monkeypatch.setattr('requests.post', Mock(side_effect=AssertionError('Unexpected network')))
    with TestClient(api.app, headers={'Authorization': 'Bearer ' + TOKEN}) as c:
        yield c


def test_dashboard_all_six_kinds_empty_queued(client):
    client.post('/dashboard/refresh')
    queued = core.get_queued_refreshes(10)
    assert set(queued) == {'macro', 'debrief', 'calendar', 'analysis', 'news', 'signals'}


def test_dashboard_six_sections_pending_state(client):
    client.post('/dashboard/refresh')
    dashboard = client.get('/dashboard').json()
    for kind in ['macro', 'debrief', 'calendar', 'analysis', 'news', 'signals']:
        assert dashboard[kind]['pending'] is True
        assert dashboard[kind]['text'] is None


def test_dashboard_cache_six_kinds_persist(client, monkeypatch):
    for kind in ['macro', 'debrief', 'calendar', 'analysis', 'news', 'signals']:
        core.set_cached_report(kind, f'{kind} content')
        core.mark_refresh_done(kind)
    dashboard = client.get('/dashboard').json()
    for kind in ['macro', 'debrief', 'calendar', 'analysis', 'news', 'signals']:
        assert dashboard[kind]['text'] == f'{kind} content'
        assert not dashboard[kind].get('pending')


def test_dashboard_refresh_queue_bounded_retry_three_attempts(client):
    core.queue_dashboard_refresh('macro')
    assert 'macro' in core.get_queued_refreshes()
    core.increment_refresh_attempt('macro')
    core.increment_refresh_attempt('macro')
    core.increment_refresh_attempt('macro')
    assert 'macro' not in core.get_queued_refreshes()


def test_dashboard_refresh_stale_two_attempts_still_queued(client):
    core.queue_dashboard_refresh('debrief')
    core.increment_refresh_attempt('debrief')
    core.increment_refresh_attempt('debrief')
    assert 'debrief' in core.get_queued_refreshes()


def test_scheduler_macro_queues_always_populates_cache(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro result')
    monkeypatch.setattr(core, 'get_macro_subs', lambda: [])
    client.put('/subscriptions/macro', json={'enabled': False})
    now = datetime(2026, 9, 12, 7, 16, tzinfo=scheduler.JAKARTA)
    asyncio.run(worker.tick(False, now))
    dashboard = client.get('/dashboard').json()
    assert dashboard['macro']['text'] == 'Macro result'
    assert not dashboard['macro'].get('pending')


def test_scheduler_debrief_queues_always_no_subs(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_daily_debrief', lambda: 'Debrief result')
    monkeypatch.setattr(core, 'get_debrief_subs', lambda: [])
    now = datetime(2026, 9, 12, 5, 16, tzinfo=scheduler.JAKARTA)
    asyncio.run(worker.tick(False, now))
    dashboard = client.get('/dashboard').json()
    assert dashboard['debrief']['text'] == 'Debrief result'


@pytest.fixture
def candles():
    count = 220
    rng = np.random.default_rng(4)
    close = 2000 + np.cumsum(rng.normal(.1, 2, count))
    return pd.DataFrame({'datetime': pd.date_range('2025-01-01', periods=count, freq='h'),
        'open': close - .5, 'high': close + 3, 'low': close - 3, 'close': close, 'volume': rng.integers(100, 1000, count)})


def test_worker_refreshes_all_six_kinds(client, monkeypatch, candles):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro')
    monkeypatch.setattr(core, 'generate_daily_debrief', lambda: 'Debrief')
    monkeypatch.setattr(core, 'get_calendar', lambda *a, **kw: [{'title': 'CPI'}])
    monkeypatch.setattr(core, 'get_ohlcv', lambda *a, **kw: candles.copy())
    monkeypatch.setattr(core, 'search_news', lambda q: 'News result')
    monkeypatch.setattr(core, 'generate_xau_entry_signal', lambda: {'entry': '2000', 'sl': '1990'})
    for kind in ['macro', 'debrief', 'calendar', 'analysis', 'news', 'signals']:
        core.queue_dashboard_refresh(kind)
    asyncio.run(worker.tick(False))
    asyncio.run(worker.tick(False))
    asyncio.run(worker.tick(False))
    dashboard = client.get('/dashboard').json()
    for kind in ['macro', 'debrief', 'calendar', 'analysis', 'news', 'signals']:
        assert dashboard[kind]['text'] is not None
        assert not dashboard[kind].get('pending')


def test_worker_bounded_retry_increments_attempt(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Error: failed')
    core.queue_dashboard_refresh('macro')
    asyncio.run(worker.tick(False))
    assert 'macro' in core.get_queued_refreshes()
    
    asyncio.run(worker.tick(False))
    asyncio.run(worker.tick(False))
    asyncio.run(worker.tick(False))
    assert 'macro' not in core.get_queued_refreshes()


def test_worker_partial_failure_continues_other_kinds(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Error: macro failed')
    monkeypatch.setattr(core, 'generate_daily_debrief', lambda: 'Debrief OK')
    core.queue_dashboard_refresh('macro')
    core.queue_dashboard_refresh('debrief')
    asyncio.run(worker.tick(False))
    dashboard = client.get('/dashboard').json()
    assert 'macro' in core.get_queued_refreshes()
    assert dashboard['debrief']['text'] == 'Debrief OK'
    assert not dashboard['debrief'].get('pending')


def test_dashboard_no_tracker_creation_from_signal_cache(client, monkeypatch):
    monkeypatch.setattr(core, 'generate_xau_entry_signal', lambda: {'entry': '2000', 'sl': '1990'})
    core.queue_dashboard_refresh('signals')
    asyncio.run(worker.tick(False))
    with core.get_db() as db:
        count = db.execute('SELECT count(*) FROM signal_trackers').fetchone()[0]
    assert count == 0


def test_dashboard_snapshot_persists_server_isolated(client, monkeypatch):
    data = {'macro': {'text': 'Macro', 'generated_at': datetime.now().isoformat()}}
    snapshot_key = connection.url if 'connection' in dir() else 'http://test'
    core.set_dashboard_snapshot(snapshot_key, data)
    retrieved = core.get_dashboard_snapshot(snapshot_key)
    assert retrieved['data'] == data
    
    different_key = 'http://different'
    assert core.get_dashboard_snapshot(different_key) is None

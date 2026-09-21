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


def test_dashboard_empty_first_run_queues_refresh(client):
    dashboard = client.get('/dashboard').json()
    assert dashboard == {}
    client.post('/dashboard/refresh')
    queued = core.get_queued_refreshes(limit=10)
    assert set(queued) == {'macro', 'debrief', 'calendar', 'analysis', 'news', 'signals'}


def test_dashboard_shows_pending_state(client, monkeypatch):
    client.post('/dashboard/refresh')
    dashboard = client.get('/dashboard').json()
    assert all(dashboard[k].get('pending') for k in dashboard)
    assert all(dashboard[k].get('text') is None for k in dashboard)


def test_dashboard_cache_persists_and_clears_pending(client, monkeypatch):
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro cached')
    core.set_cached_report('macro', 'Macro cached')
    core.mark_refresh_done('macro')
    dashboard = client.get('/dashboard').json()
    assert dashboard['macro']['text'] == 'Macro cached'
    assert not dashboard['macro'].get('pending')


def test_dashboard_stale_state_shows_text_with_pending(client, monkeypatch):
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Old macro')
    core.set_cached_report('macro', 'Old macro')
    core.queue_dashboard_refresh('macro')
    dashboard = client.get('/dashboard').json()
    assert dashboard['macro']['text'] == 'Old macro'
    assert dashboard['macro']['pending'] is True


def test_scheduler_macro_briefing_populates_cache(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro from scheduler')
    client.put('/subscriptions/macro', json={'enabled': True})
    now = datetime(2026, 9, 12, 7, 16, tzinfo=scheduler.JAKARTA)
    asyncio.run(worker.tick(False, now))
    dashboard = client.get('/dashboard').json()
    assert dashboard['macro']['text'] == 'Macro from scheduler'
    assert not dashboard['macro'].get('pending')


def test_scheduler_debrief_populates_cache(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_daily_debrief', lambda: 'Debrief from scheduler')
    client.put('/subscriptions/debrief', json={'enabled': True})
    now = datetime(2026, 9, 12, 5, 16, tzinfo=scheduler.JAKARTA)
    asyncio.run(worker.tick(False, now))
    dashboard = client.get('/dashboard').json()
    assert dashboard['debrief']['text'] == 'Debrief from scheduler'


def test_worker_refreshes_queued_calendar(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'get_calendar', lambda *a, **kw: [{'title': 'CPI', 'time': '12:00'}])
    core.queue_dashboard_refresh('calendar')
    asyncio.run(worker.tick(False))
    dashboard = client.get('/dashboard').json()
    assert 'calendar' in dashboard
    calendar_data = json.loads(dashboard['calendar']['text'])
    assert any(e['title'] == 'CPI' for e in calendar_data)
    assert not dashboard['calendar'].get('pending')


@pytest.fixture
def candles():
    count = 220
    rng = np.random.default_rng(4)
    close = 2000 + np.cumsum(rng.normal(.1, 2, count))
    return pd.DataFrame({'datetime': pd.date_range('2025-01-01', periods=count, freq='h'),
        'open': close - .5, 'high': close + 3, 'low': close - 3, 'close': close, 'volume': rng.integers(100, 1000, count)})


def test_worker_refreshes_queued_analysis(client, monkeypatch, candles):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'get_ohlcv', lambda *a, **kw: candles.copy())
    core.queue_dashboard_refresh('analysis')
    asyncio.run(worker.tick(False))
    dashboard = client.get('/dashboard').json()
    assert 'analysis' in dashboard
    analysis_data = json.loads(dashboard['analysis']['text'])
    assert 'indicators' in analysis_data
    assert 'confidence' in analysis_data
    assert not dashboard['analysis'].get('pending')


def test_report_endpoint_does_not_create_tracker(client, monkeypatch):
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro briefing text')
    client.post('/reports/macro', json={})
    with core.get_db() as db:
        count = db.execute('SELECT count(*) FROM signal_trackers').fetchone()[0]
    assert count == 0


def test_scheduler_macro_does_not_create_tracker(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro briefing')
    client.put('/subscriptions/macro', json={'enabled': True})
    now = datetime(2026, 9, 12, 7, 16, tzinfo=scheduler.JAKARTA)
    asyncio.run(worker.tick(False, now))
    with core.get_db() as db:
        count = db.execute('SELECT count(*) FROM signal_trackers').fetchone()[0]
    assert count == 0


def test_dashboard_refresh_clears_queued_status(client, monkeypatch):
    async def noop(*args):
        pass
    monkeypatch.setattr(scheduler, 'check_high_impact_alerts', noop)
    monkeypatch.setattr(scheduler, 'check_price_alerts', noop)
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro result')
    client.post('/dashboard/refresh')
    assert 'macro' in core.get_queued_refreshes()
    client.put('/subscriptions/macro', json={'enabled': True})
    now = datetime(2026, 9, 12, 7, 16, tzinfo=scheduler.JAKARTA)
    asyncio.run(worker.tick(False, now))
    assert 'macro' not in core.get_queued_refreshes()
    assert not client.get('/dashboard').json()['macro'].get('pending')


def test_dashboard_partial_cache_mixed_pending_ready(client, monkeypatch):
    monkeypatch.setattr(core, 'generate_macro_briefing', lambda: 'Macro cached')
    core.set_cached_report('macro', 'Macro cached')
    core.queue_dashboard_refresh('debrief')
    dashboard = client.get('/dashboard').json()
    assert dashboard['macro']['text'] == 'Macro cached'
    assert not dashboard['macro'].get('pending')
    assert dashboard['debrief']['pending'] is True
    assert dashboard['debrief']['text'] is None

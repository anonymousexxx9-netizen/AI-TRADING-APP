import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
from unittest.mock import Mock
from fastapi.testclient import TestClient
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import core

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


def test_dashboard_calendar_high_impact_seven_days(client, monkeypatch):
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    events = [
        {'title': 'CPI', 'date': today_ny.strftime('%m-%d-%Y'), 'time': '12:00PM', 'impact': 'High', 'country': 'USD', 'forecast': '3.2%', 'previous': '3.1%', 'actual': 'N/A'},
        {'title': 'Jobs', 'date': (today_ny + timedelta(days=1)).strftime('%m-%d-%Y'), 'time': '1:30PM', 'impact': 'High', 'country': 'USD', 'forecast': '150K', 'previous': '145K', 'actual': 'N/A'},
        {'title': 'Low event', 'date': (today_ny + timedelta(days=2)).strftime('%m-%d-%Y'), 'time': '2:00PM', 'impact': 'Low', 'country': 'USD', 'forecast': 'N/A', 'previous': 'N/A', 'actual': 'N/A'},
        {'title': 'Future event', 'date': (today_ny + timedelta(days=10)).strftime('%m-%d-%Y'), 'time': '3:00PM', 'impact': 'High', 'country': 'USD', 'forecast': 'N/A', 'previous': 'N/A', 'actual': 'N/A'},
    ]
    monkeypatch.setattr(core, 'get_calendar', lambda *a, **kw: [e for e in events if e['impact'] == 'High' and (kw.get('days') is None or (datetime.strptime(e['date'], '%m-%d-%Y').date() - today_ny).days <= kw.get('days', 999))])
    
    result = client.get('/dashboard/calendar').json()
    assert result['status'] in ['ok', 'cached']
    assert len(result['events']) == 2
    assert all(e['impact'] == 'High' for e in result['events'])


def test_dashboard_calendar_empty_when_no_high_impact(client, monkeypatch):
    monkeypatch.setattr(core, 'get_calendar', lambda *a, **kw: [])
    result = client.get('/dashboard/calendar').json()
    assert result['status'] == 'ok'
    assert result['events'] == []


def test_dashboard_calendar_unavailable_on_fetch_fail(client, monkeypatch):
    monkeypatch.setattr(core, 'get_calendar', lambda *a, **kw: None)
    result = client.get('/dashboard/calendar').json()
    assert result['status'] == 'unavailable'
    assert result['events'] == []


def test_get_calendar_days_filter(monkeypatch):
    from datetime import timedelta
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    
    raw_events = [
        {'title': 'Event0', 'date': f'{today_ny.month:02d}-{today_ny.day:02d}-{today_ny.year}', 'time': '12:00PM', 'impact': 'High', 'country': 'USD', 'forecast': 'N/A', 'previous': 'N/A', 'actual': 'N/A'},
        {'title': 'Event3', 'date': (today_ny + timedelta(days=3)).strftime('%m-%d-%Y'), 'time': '1:00PM', 'impact': 'High', 'country': 'USD', 'forecast': 'N/A', 'previous': 'N/A', 'actual': 'N/A'},
        {'title': 'Event8', 'date': (today_ny + timedelta(days=8)).strftime('%m-%d-%Y'), 'time': '2:00PM', 'impact': 'High', 'country': 'USD', 'forecast': 'N/A', 'previous': 'N/A', 'actual': 'N/A'},
    ]
    
    monkeypatch.setattr(core, '_fetch_calendar_raw', lambda: raw_events)
    
    result = core.get_calendar('USD', 'High', days=7)
    assert result is not None
    assert len(result) <= 2
    assert all(e['title'] in ['Event0', 'Event3'] for e in result)


def test_get_calendar_days_excludes_past(monkeypatch):
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    yesterday = today_ny.replace(day=max(1, today_ny.day-1))
    
    raw_events = [
        {'title': 'Past', 'date': f'{yesterday.month:02d}-{yesterday.day:02d}-{yesterday.year}', 'time': '12:00PM', 'impact': 'High', 'country': 'USD', 'forecast': 'N/A', 'previous': 'N/A', 'actual': 'N/A'},
        {'title': 'Today', 'date': f'{today_ny.month:02d}-{today_ny.day:02d}-{today_ny.year}', 'time': '1:00PM', 'impact': 'High', 'country': 'USD', 'forecast': 'N/A', 'previous': 'N/A', 'actual': 'N/A'},
    ]
    
    monkeypatch.setattr(core, '_fetch_calendar_raw', lambda: raw_events)
    
    result = core.get_calendar('USD', 'High', days=7)
    assert result is not None
    assert len(result) == 1
    assert result[0]['title'] == 'Today'

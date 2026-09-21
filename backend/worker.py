"""One worker per database. Durable inbox first, retryable Expo push second."""
import asyncio
from datetime import datetime, timedelta
import logging
import os
import time
import requests
import core
import scheduler

log = logging.getLogger(__name__)


def scheduled_jobs(now):
    """Catch up today's slots for two hours; persist dedup across restarts."""
    jobs = []
    for h, m, message in scheduler.SESSION_SLOTS:
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if timedelta(0) <= now - target < timedelta(hours=2):
            jobs.extend([(f'macro:{target.isoformat()}', scheduler.send_macro_briefing, ()),
                         (f'session:{target.isoformat()}', scheduler.send_session_reminder, (message,))])
    target = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if timedelta(0) <= now - target < timedelta(hours=2):
        jobs.append((f'debrief:{target.isoformat()}', scheduler.send_daily_debrief, ()))
    return jobs


async def check_signal_trackers_async(_telegram_app, _discord_bot):
    await asyncio.to_thread(core.check_signal_trackers)


async def tick(slow_checks=True, now=None):
    checks = [scheduler.check_high_impact_alerts, scheduler.check_price_alerts, check_signal_trackers_async]
    if slow_checks:
        checks += [scheduler.check_watchlist_traps, scheduler.check_scan_signals, scheduler.check_volatility_spikes]
    for fn in checks:
        try:
            await fn(None, None)
        except Exception:
            log.exception('Worker check failed: %s', fn.__name__)
    
    queued = core.get_queued_refreshes(2)
    for kind in queued:
        try:
            if kind == 'macro':
                result = core.generate_macro_briefing()
                if result and not result.startswith(('Error ', '❌', '⚠️ AI', 'Error:')):
                    core.set_cached_report('macro', result)
                    core.mark_refresh_done('macro')
                else:
                    core.increment_refresh_attempt(kind)
            elif kind == 'debrief':
                result = core.generate_daily_debrief()
                if result and not result.startswith(('Error ', '❌', '⚠️ AI', 'Error:')):
                    core.set_cached_report('debrief', result)
                    core.mark_refresh_done('debrief')
                else:
                    core.increment_refresh_attempt(kind)
            elif kind == 'calendar':
                events = core.get_calendar('USD')
                if events is not None:
                    import json
                    from api import clean
                    core.set_cached_report('calendar', json.dumps(clean(events)))
                    core.mark_refresh_done('calendar')
                else:
                    core.increment_refresh_attempt(kind)
            elif kind == 'analysis':
                df = core.get_ohlcv('XAU/USD', '1h', 200)
                if df is not None and len(df) >= 50:
                    indicators = core.calculate_indicators(df)
                    sr = core.get_support_resistance(df)
                    import json
                    from api import clean
                    analysis = {'indicators': indicators, 'confidence': core.calculate_confidence(indicators),
                                'regime': core.get_regime(indicators), 'sr': sr,
                                'trap': core.detect_trap(df, indicators), 'pattern': core.detect_candlestick_pattern(df),
                                'structure': core.detect_market_structure(df)}
                    core.set_cached_report('analysis', json.dumps(clean(analysis)))
                    core.mark_refresh_done('analysis')
                else:
                    core.increment_refresh_attempt(kind)
            elif kind == 'news':
                result = core.search_news('gold USD forex')
                if result and not result.startswith(('Error ', '❌', '⚠️ AI', 'Error:')):
                    core.set_cached_report('news', result)
                    core.mark_refresh_done('news')
                else:
                    core.increment_refresh_attempt(kind)
            elif kind == 'signals':
                signal = core.generate_xau_entry_signal()
                if signal and not signal.get('error'):
                    import json
                    from api import clean
                    core.set_cached_report('signals', json.dumps(clean(signal)))
                    core.mark_refresh_done('signals')
                else:
                    core.increment_refresh_attempt(kind)
        except Exception:
            log.exception('Dashboard %s refresh failed', kind)
            core.increment_refresh_attempt(kind)
    
    for key, fn, args in scheduled_jobs(now or datetime.now(scheduler.JAKARTA)):
        if core.get_setting('mobile_job:' + key):
            continue
        try:
            await fn(None, None, *args)
            core.set_setting('mobile_job:' + key, 'done')
        except Exception:
            log.exception('Scheduled report failed: %s', key)
    core.set_setting('mobile_worker_tick', datetime.now(scheduler.JAKARTA).isoformat())


def flush_push():
    headers = {'Content-Type': 'application/json'}
    if os.getenv('EXPO_ACCESS_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['EXPO_ACCESS_TOKEN']
    with core.get_db() as db:
        pending = [dict(r) for r in db.execute('''SELECT d.*, n.body FROM mobile_deliveries d
            JOIN mobile_inbox n ON n.id=d.notification_id
            WHERE d.status='pending' AND d.attempts<5 LIMIT 50''')]
    for item in pending:
        try:
            response = requests.post('https://exp.host/--/api/v2/push/send', headers=headers,
                json={'to': item['token'], 'title': 'Bayproject', 'body': item['body'][:180],
                      'sound': 'default', 'channelId': 'market', 'data': {'notificationId': item['notification_id']}}, timeout=15)
            response.raise_for_status()
            ticket = response.json()['data']
            if isinstance(ticket, list):
                ticket = ticket[0]
            status = 'ticket' if ticket.get('status') == 'ok' and ticket.get('id') else 'pending'
            error = ticket.get('details', {}).get('error')
            if error == 'DeviceNotRegistered':
                status = 'removed'
            with core.get_db() as db:
                db.execute('UPDATE mobile_deliveries SET status=?,ticket=?,error=?,attempts=attempts+1 WHERE notification_id=? AND token=?',
                           (status, ticket.get('id'), error, item['notification_id'], item['token']))
                if status == 'removed':
                    db.execute('DELETE FROM mobile_devices WHERE token=?', (item['token'],))
        except Exception:
            log.exception('Push send failed; inbox remains available')
            with core.get_db() as db:
                db.execute("UPDATE mobile_deliveries SET attempts=attempts+1,error='Transport failed' WHERE notification_id=? AND token=?",
                           (item['notification_id'], item['token']))
    with core.get_db() as db:
        tickets = [dict(r) for r in db.execute("SELECT * FROM mobile_deliveries WHERE status='ticket' LIMIT 100")]
    if not tickets:
        return
    response = requests.post('https://exp.host/--/api/v2/push/getReceipts', headers=headers,
                             json={'ids': [r['ticket'] for r in tickets]}, timeout=15)
    response.raise_for_status()
    receipts = response.json().get('data', {})
    with core.get_db() as db:
        for item in tickets:
            receipt = receipts.get(item['ticket'])
            if not receipt:
                continue
            error = receipt.get('details', {}).get('error')
            status = 'delivered' if receipt.get('status') == 'ok' else 'failed'
            db.execute('UPDATE mobile_deliveries SET status=?,error=? WHERE notification_id=? AND token=?',
                       (status, error, item['notification_id'], item['token']))
            if error == 'DeviceNotRegistered':
                db.execute('DELETE FROM mobile_devices WHERE token=?', (item['token'],))


async def run():
    last_slow = -float('inf')
    while True:
        slow = time.monotonic() - last_slow >= 900
        try:
            await asyncio.to_thread(lambda: asyncio.run(tick(slow)))
            if slow:
                last_slow = time.monotonic()
            await asyncio.to_thread(flush_push)
        except Exception:
            log.exception('Worker cycle failed')
        await asyncio.sleep(60)

"""Private mobile inbox and chat persistence; shares the original bot database."""
import json
import core


def init():
    core.init_db()
    with core.get_db() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS mobile_chat (
          id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
        CREATE TABLE IF NOT EXISTS mobile_inbox (
          id INTEGER PRIMARY KEY, body TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
        CREATE TABLE IF NOT EXISTS mobile_devices (token TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS mobile_deliveries (
          notification_id INTEGER NOT NULL, token TEXT NOT NULL, ticket TEXT,
          status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
          error TEXT, PRIMARY KEY(notification_id,token));
        ''')
    core.register_user(1, 1, "mobile")


def history(limit=100):
    with core.get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM (SELECT * FROM mobile_chat ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))]


def add_message(role, content):
    with core.get_db() as db:
        db.execute("INSERT INTO mobile_chat(role,content) VALUES (?,?)", (role, content))


def enqueue(body):
    with core.get_db() as db:
        row = db.execute("INSERT INTO mobile_inbox(body) VALUES (?)", (body,))
        db.execute("INSERT INTO mobile_deliveries(notification_id,token) SELECT ?,token FROM mobile_devices", (row.lastrowid,))
    return row.lastrowid

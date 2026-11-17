#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB_ROOT = Path(__file__).resolve().parent / "web"
DB_PATH = os.environ.get("NOTES_DB", "/data/notes.db")
PORT = int(os.environ.get("PORT", "8080"))
PASSWORD_HASH = os.environ.get("NOTES_PASSWORD_SHA256", "").strip().lower()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").encode()
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
SESSION_AGE = 60 * 60 * 24 * 30
MAX_BODY = 2 * 1024 * 1024
NOTE_PATH = re.compile(r"^/api/notes/([a-zA-Z0-9-]+)$")
LOGIN_ATTEMPTS = defaultdict(deque)
LOGIN_LOCK = threading.Lock()

if not re.fullmatch(r"[a-f0-9]{64}", PASSWORD_HASH):
    raise SystemExit("NOTES_PASSWORD_SHA256 musi być skrótem SHA-256")
if len(SESSION_SECRET) < 32:
    raise SystemExit("SESSION_SECRET musi mieć co najmniej 32 znaki")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS notes_updated_idx ON notes(updated_at DESC)")


def make_session():
    expires = str(int(time.time()) + SESSION_AGE)
    signature = hmac.new(SESSION_SECRET, expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def valid_session(token):
    try:
        expires, signature = token.split(".", 1)
        if int(expires) < int(time.time()):
            return False
        expected = hmac.new(SESSION_SECRET, expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


class AppHandler(SimpleHTTPRequestHandler):
    server_version = "Pulpit/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        if self.path.startswith("/api/") or self.path in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status, data, extra_headers=None):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Nieprawidłowy rozmiar żądania")
        if length < 1 or length > MAX_BODY:
            raise ValueError("Nieprawidłowy rozmiar żądania")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Nieprawidłowy JSON")

    def session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("pulpit_session")
        return morsel.value if morsel else ""

    def authenticated(self):
        return valid_session(self.session_token())

    def require_auth(self):
        if self.authenticated():
            return True
        self.send_json(401, {"error": "Wymagane logowanie"})
        return False

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.send_json(200, {"status": "ok"})
        if path == "/api/session":
            return self.send_json(200, {"authenticated": self.authenticated()})
        if path == "/api/notes":
            if not self.require_auth():
                return
            with connect_db() as db:
                rows = db.execute("SELECT id, title, body, created_at, updated_at FROM notes ORDER BY updated_at DESC").fetchall()
            return self.send_json(200, {"notes": [dict(row) for row in rows]})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.same_origin():
            return self.send_json(403, {"error": "Niedozwolone źródło"})
        if path == "/api/login":
            return self.handle_login()
        if path == "/api/logout":
            cookie = "pulpit_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            if COOKIE_SECURE:
                cookie += "; Secure"
            return self.send_json(200, {"ok": True}, {"Set-Cookie": cookie})
        if path == "/api/notes":
            if not self.require_auth():
                return
            try:
                data = self.read_json()
            except ValueError as error:
                return self.send_json(400, {"error": str(error)})
            note_id = str(uuid.uuid4())
            title = str(data.get("title", "Nowa notatka"))[:120]
            body = str(data.get("body", ""))[:1000000]
            timestamp = now_iso()
            with connect_db() as db:
                db.execute("INSERT INTO notes(id, title, body, created_at, updated_at) VALUES(?,?,?,?,?)", (note_id, title, body, timestamp, timestamp))
            return self.send_json(201, {"note": {"id": note_id, "title": title, "body": body, "created_at": timestamp, "updated_at": timestamp}})
        return self.send_json(404, {"error": "Nie znaleziono"})

    def handle_login(self):
        client = self.client_address[0]
        now = time.time()
        with LOGIN_LOCK:
            attempts = LOGIN_ATTEMPTS[client]
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                return self.send_json(429, {"error": "Za dużo prób. Spróbuj ponownie za minutę."})
        try:
            data = self.read_json()
        except ValueError as error:
            return self.send_json(400, {"error": str(error)})
        supplied = hashlib.sha256(str(data.get("password", "")).encode()).hexdigest()
        if not hmac.compare_digest(supplied, PASSWORD_HASH):
            with LOGIN_LOCK:
                LOGIN_ATTEMPTS[client].append(now)
            return self.send_json(401, {"error": "Nieprawidłowe hasło"})
        with LOGIN_LOCK:
            LOGIN_ATTEMPTS.pop(client, None)
        cookie = f"pulpit_session={make_session()}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_AGE}"
        if COOKIE_SECURE:
            cookie += "; Secure"
        return self.send_json(200, {"ok": True}, {"Set-Cookie": cookie})

    def do_PUT(self):
        path = urlparse(self.path).path
        match = NOTE_PATH.match(path)
        if not match:
            return self.send_json(404, {"error": "Nie znaleziono"})
        if not self.same_origin():
            return self.send_json(403, {"error": "Niedozwolone źródło"})
        if not self.require_auth():
            return
        try:
            data = self.read_json()
        except ValueError as error:
            return self.send_json(400, {"error": str(error)})
        note_id = match.group(1)
        title = str(data.get("title", ""))[:120]
        body = str(data.get("body", ""))[:1000000]
        timestamp = now_iso()
        with connect_db() as db:
            cursor = db.execute("UPDATE notes SET title=?, body=?, updated_at=? WHERE id=?", (title, body, timestamp, note_id))
        if cursor.rowcount == 0:
            return self.send_json(404, {"error": "Notatka nie istnieje"})
        return self.send_json(200, {"note": {"id": note_id, "title": title, "body": body, "updated_at": timestamp}})

    def do_DELETE(self):
        path = urlparse(self.path).path
        match = NOTE_PATH.match(path)
        if not match:
            return self.send_json(404, {"error": "Nie znaleziono"})
        if not self.same_origin():
            return self.send_json(403, {"error": "Niedozwolone źródło"})
        if not self.require_auth():
            return
        with connect_db() as db:
            cursor = db.execute("DELETE FROM notes WHERE id=?", (match.group(1),))
        if cursor.rowcount == 0:
            return self.send_json(404, {"error": "Notatka nie istnieje"})
        return self.send_json(200, {"ok": True})


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AppHandler)
    print(f"Pulpit działa na porcie {PORT}; baza: {DB_PATH}")
    server.serve_forever()

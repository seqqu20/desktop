#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape

WEB_ROOT = Path(__file__).resolve().parent / "web"
DB_PATH = os.environ.get("NOTES_DB", "/data/notes.db")
PORT = int(os.environ.get("PORT", "8080"))
PASSWORD_HASH = os.environ.get("NOTES_PASSWORD_SHA256", "").strip().lower()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").encode()
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
SESSION_AGE = 60 * 60 * 24 * 30
MAX_BODY = 2 * 1024 * 1024
MAX_OCR_BODY = 20 * 1024 * 1024
MAX_OCR_PAGES = 30
NOTE_PATH = re.compile(r"^/api/notes/([a-zA-Z0-9-]+)$")
LOGIN_ATTEMPTS = defaultdict(deque)
LOGIN_LOCK = threading.Lock()
OCR_LOCK = threading.BoundedSemaphore(1)

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


def run_command(arguments, timeout=180):
    return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=timeout)


def pdf_page_count(input_path):
    result = run_command(["pdfinfo", str(input_path)], timeout=30)
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
    if not match:
        raise subprocess.CalledProcessError(1, "pdfinfo")
    return int(match.group(1))


def extract_native_text(input_path, temp_path, pages):
    output_path = temp_path / "native.txt"
    run_command(["pdftotext", "-layout", "-enc", "UTF-8", str(input_path), str(output_path)], timeout=90)
    content = output_path.read_text("utf-8", errors="replace")
    meaningful = len(re.sub(r"\s+", "", content))
    if meaningful < max(120, pages * 60):
        return None
    page_texts = content.split("\f")
    return [page.strip() for page in page_texts[:pages]] + [""] * max(0, pages - len(page_texts))


def ocr_pdf(input_path, temp_path, pages):
    image_prefix = temp_path / "page"
    run_command([
        "pdftoppm", "-jpeg", "-r", "300", "-jpegopt", "quality=92",
        "-f", "1", "-l", str(pages), str(input_path), str(image_prefix)
    ], timeout=240)
    images = sorted(temp_path.glob("page-*.jpg"), key=lambda path: int(re.search(r"(\d+)$", path.stem).group(1)))
    if not images:
        raise subprocess.CalledProcessError(1, "pdftoppm")
    page_texts = []
    for index, image in enumerate(images, 1):
        output_base = temp_path / f"ocr-{index}"
        run_command([
            "tesseract", str(image), str(output_base), "-l", "pol+eng",
            "--dpi", "300", "--psm", "3", "txt"
        ], timeout=180)
        page_texts.append(output_base.with_suffix(".txt").read_text("utf-8", errors="replace").strip())
    return page_texts


def build_docx(page_texts, output_path, title):
    def xml_text(value):
        clean = "".join(character for character in value if character in "\t\n\r" or ord(character) >= 32)
        return escape(clean)

    paragraphs = []
    for page_index, text in enumerate(page_texts):
        blocks = re.split(r"\n\s*\n", text.strip()) if text.strip() else [""]
        for block in blocks:
            runs = []
            for line_index, line in enumerate(block.splitlines() or [""]):
                if line_index:
                    runs.append("<w:br/>")
                runs.append(f'<w:t xml:space="preserve">{xml_text(line)}</w:t>')
            paragraphs.append(f'<w:p><w:pPr><w:spacing w:after="120"/></w:pPr><w:r>{"".join(runs)}</w:r></w:p>')
        if page_index < len(page_texts) - 1:
            paragraphs.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(paragraphs)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1247" w:bottom="1134" w:left="1247"/></w:sectPr></w:body>
</w:document>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
    relationships = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)


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
        clean_path = urlparse(self.path).path
        if clean_path.startswith("/api/") or clean_path.endswith((".html", ".js", ".css")) or clean_path == "/":
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

    def send_binary(self, status, payload, content_type, filename):
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", filename).strip("-") or "document.docx"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
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
        if path == "/api/ocr/pdf-to-word":
            if not self.require_auth():
                return
            return self.handle_pdf_to_word()
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

    def handle_pdf_to_word(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_json(400, {"error": "Nieprawidłowy rozmiar pliku"})
        if length < 5 or length > MAX_OCR_BODY:
            return self.send_json(413, {"error": "PDF może mieć maksymalnie 20 MB"})
        if not OCR_LOCK.acquire(blocking=False):
            return self.send_json(429, {"error": "Trwa inna konwersja OCR. Spróbuj ponownie za chwilę."})
        try:
            content = self.rfile.read(length)
            if not content.startswith(b"%PDF-"):
                return self.send_json(415, {"error": "Wybrany plik nie jest prawidłowym PDF-em"})
            original_name = unquote(self.headers.get("X-Filename", "document.pdf"))
            output_name = f"{Path(original_name).stem or 'document'}-OCR.docx"
            with tempfile.TemporaryDirectory(prefix="pulpit-ocr-") as temp:
                temp_path = Path(temp)
                input_path = temp_path / "input.pdf"
                input_path.write_bytes(content)
                pages = pdf_page_count(input_path)
                if pages < 1 or pages > MAX_OCR_PAGES:
                    return self.send_json(422, {"error": f"PDF może mieć maksymalnie {MAX_OCR_PAGES} stron"})
                page_texts = extract_native_text(input_path, temp_path, pages)
                if page_texts is None:
                    page_texts = ocr_pdf(input_path, temp_path, pages)
                output_path = temp_path / "output.docx"
                build_docx(page_texts, output_path, Path(original_name).stem)
                payload = output_path.read_bytes()
            return self.send_binary(200, payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", output_name)
        except subprocess.TimeoutExpired:
            return self.send_json(504, {"error": "OCR trwał zbyt długo. Spróbuj z mniejszym dokumentem."})
        except subprocess.CalledProcessError as error:
            print("Błąd OCR:", error)
            return self.send_json(422, {"error": "Nie udało się odczytać tego dokumentu PDF"})
        except Exception as error:
            print("Nieoczekiwany błąd OCR:", repr(error))
            return self.send_json(500, {"error": "Nie udało się utworzyć dokumentu Word"})
        finally:
            OCR_LOCK.release()

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

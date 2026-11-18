#!/usr/bin/env python3
import hashlib
import hmac
import csv
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
MAX_SCAN_IMAGE_BODY = 15 * 1024 * 1024
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


def has_native_text(input_path, temp_path):
    output_path = temp_path / "layout-native.txt"
    run_command(["pdftotext", "-enc", "UTF-8", str(input_path), str(output_path)], timeout=90)
    return len(re.sub(r"\s+", "", output_path.read_text("utf-8", errors="replace"))) >= 12


def build_scanned_layout_docx(page_data, output_path):
    document_parts = []
    document_relationships = []
    for page_index, (image_path, lines, width_px, height_px) in enumerate(page_data, 1):
        width_pt = width_px * 72 / 300
        height_pt = height_px * 72 / 300
        shapes = [f'''<w:r><w:pict><v:rect id="page-bg-{page_index}" stroked="f" style="position:absolute;left:0pt;top:0pt;width:{width_pt:.2f}pt;height:{height_pt:.2f}pt;z-index:-251658240;mso-position-horizontal-relative:page;mso-position-vertical-relative:page"><v:imagedata r:id="rId{page_index}" o:title=""/></v:rect></w:pict></w:r>''']
        for line_index, line in enumerate(lines, 1):
            left, top, right, bottom = line["bbox"]
            x = max(0, left - 2) * 72 / 300
            y = max(0, top - 2) * 72 / 300
            width = max(8, right - left + 5) * 72 / 300
            height = max(10, bottom - top + 5) * 72 / 300
            font_half_points = max(14, min(72, round((bottom - top) * 72 / 300 * 1.7)))
            text = escape(line["text"])
            background = line["background"]
            shapes.append(f'''<w:r><w:pict><v:rect id="ocr-{page_index}-{line_index}" stroked="f" fillcolor="{background}" style="position:absolute;left:{x:.2f}pt;top:{y:.2f}pt;width:{width:.2f}pt;height:{height:.2f}pt;z-index:{line_index};mso-position-horizontal-relative:page;mso-position-vertical-relative:page"><v:textbox inset="0,0,0,0"><w:txbxContent><w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="{font_half_points}"/><w:szCs w:val="{font_half_points}"/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:txbxContent></v:textbox></v:rect></w:pict></w:r>''')
        page_break = '<w:pageBreakBefore/>' if page_index > 1 else ''
        document_parts.append(f'<w:p><w:pPr>{page_break}<w:spacing w:before="0" w:after="0"/></w:pPr>{"".join(shapes)}</w:p>')
        document_relationships.append(f'<Relationship Id="rId{page_index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/page-{page_index}.jpg"/>')
    width_twips = round(page_data[0][2] * 72 / 300 * 20)
    height_twips = round(page_data[0][3] * 72 / 300 * 20)
    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"><w:body>{''.join(document_parts)}<w:sectPr><w:pgSz w:w="{width_twips}" w:h="{height_twips}"/><w:pgMar w:top="0" w:right="0" w:bottom="0" w:left="0" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr></w:body></w:document>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="jpg" ContentType="image/jpeg"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'''
    root_relationships = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    word_relationships = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{''.join(document_relationships)}</Relationships>'''
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_relationships)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", word_relationships)
        for page_index, (image_path, *_rest) in enumerate(page_data, 1):
            archive.write(image_path, f"word/media/page-{page_index}.jpg")


def build_layout_docx(input_path, temp_path, output_path, pages):
    if not has_native_text(input_path, temp_path):
        image_prefix = temp_path / "layout-page"
        run_command([
            "pdftoppm", "-jpeg", "-r", "300", "-jpegopt", "quality=94",
            "-f", "1", "-l", str(pages), str(input_path), str(image_prefix)
        ], timeout=360)
        images = sorted(temp_path.glob("layout-page-*.jpg"), key=lambda path: int(re.search(r"(\d+)$", path.stem).group(1)))
        page_data = []
        import cv2
        for index, image in enumerate(images, 1):
            output_base = temp_path / f"layout-ocr-{index}"
            run_command([
                "tesseract", str(image), str(output_base), "-l", "pol+eng",
                "--dpi", "300", "--psm", "3", "tsv"
            ], timeout=240)
            raster = cv2.imread(str(image))
            height_px, width_px = raster.shape[:2]
            grouped = {}
            with output_base.with_suffix(".tsv").open("r", encoding="utf-8", errors="replace") as stream:
                for row in csv.DictReader(stream, delimiter="\t"):
                    if row.get("level") != "5" or not row.get("text", "").strip():
                        continue
                    key = (row["block_num"], row["par_num"], row["line_num"])
                    left, top, width, height = (int(row[name]) for name in ("left", "top", "width", "height"))
                    item = grouped.setdefault(key, {"words": [], "bbox": [left, top, left + width, top + height]})
                    item["words"].append(row["text"].strip())
                    item["bbox"] = [min(item["bbox"][0], left), min(item["bbox"][1], top), max(item["bbox"][2], left + width), max(item["bbox"][3], top + height)]
            lines = []
            for item in grouped.values():
                left, top, right, bottom = item["bbox"]
                sample = raster[max(0, top - 4):min(height_px, bottom + 4), max(0, left - 4):min(width_px, right + 4)]
                if sample.size:
                    pixels = sample.reshape(-1, 3)
                    bright = pixels[pixels.mean(axis=1) > 150]
                    color = (bright if len(bright) else pixels).mean(axis=0)
                    background = f"#{round(color[2]):02x}{round(color[1]):02x}{round(color[0]):02x}"
                else:
                    background = "#ffffff"
                lines.append({"text": " ".join(item["words"]), "bbox": item["bbox"], "background": background})
            page_data.append((image, lines, width_px, height_px))
        if not page_data:
            raise subprocess.CalledProcessError(1, "pdftoppm")
        return build_scanned_layout_docx(page_data, output_path)
    from pdf2docx import Converter
    converter = Converter(str(input_path))
    try:
        converter.convert(str(output_path), start=0, end=None)
    finally:
        converter.close()


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

    def send_binary(self, status, payload, content_type, filename, extra_headers=None):
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", filename).strip("-") or "document.docx"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
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
        if path == "/api/ocr/pdf-to-word":
            if not self.require_auth():
                return
            return self.handle_pdf_to_word()
        if path == "/api/scanner/process":
            if not self.require_auth():
                return
            return self.handle_scan_image()
        if path == "/api/scanner/ocr-pdf":
            if not self.require_auth():
                return
            return self.handle_scanner_ocr_pdf()
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

    def read_binary(self, maximum, label):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError(f"Nieprawidłowy rozmiar {label}")
        if length < 1 or length > maximum:
            raise ValueError(f"{label} jest za duży")
        return self.rfile.read(length)

    def handle_scan_image(self):
        try:
            content = self.read_binary(MAX_SCAN_IMAGE_BODY, "Obraz")
            import cv2
            import numpy as np
            raster = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
            if raster is None:
                return self.send_json(415, {"error": "Nie udało się odczytać zdjęcia"})
            height, width = raster.shape[:2]
            if height * width > 50_000_000:
                return self.send_json(413, {"error": "Zdjęcie ma zbyt wysoką rozdzielczość"})
            crop_detected = False
            if self.headers.get("X-Auto-Crop", "true").lower() == "true":
                scale = min(1.0, 1400 / max(height, width))
                preview = cv2.resize(raster, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else raster.copy()
                gray = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (5, 5), 0)
                edges = cv2.Canny(gray, 45, 140)
                edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
                contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                document = None
                for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
                    perimeter = cv2.arcLength(contour, True)
                    polygon = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
                    if len(polygon) == 4 and cv2.contourArea(polygon) > preview.shape[0] * preview.shape[1] * 0.18:
                        document = polygon.reshape(4, 2).astype("float32") / scale
                        break
                if document is not None:
                    sums = document.sum(axis=1)
                    diffs = np.diff(document, axis=1).reshape(-1)
                    points = np.array([document[np.argmin(sums)], document[np.argmin(diffs)], document[np.argmax(sums)], document[np.argmax(diffs)]], dtype="float32")
                    top_left, top_right, bottom_right, bottom_left = points
                    target_width = int(max(np.linalg.norm(bottom_right - bottom_left), np.linalg.norm(top_right - top_left)))
                    target_height = int(max(np.linalg.norm(top_right - bottom_right), np.linalg.norm(top_left - bottom_left)))
                    if target_width > 200 and target_height > 200:
                        target = np.array([[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]], dtype="float32")
                        raster = cv2.warpPerspective(raster, cv2.getPerspectiveTransform(points, target), (target_width, target_height), borderMode=cv2.BORDER_REPLICATE)
                        crop_detected = True
            rotation = int(self.headers.get("X-Rotate", "0")) % 360
            if rotation == 90:
                raster = cv2.rotate(raster, cv2.ROTATE_90_CLOCKWISE)
            elif rotation == 180:
                raster = cv2.rotate(raster, cv2.ROTATE_180)
            elif rotation == 270:
                raster = cv2.rotate(raster, cv2.ROTATE_90_COUNTERCLOCKWISE)
            mode = self.headers.get("X-Scan-Mode", "color").lower()
            if mode == "gray":
                gray = cv2.cvtColor(raster, cv2.COLOR_BGR2GRAY)
                raster = cv2.cvtColor(cv2.createCLAHE(2.0, (8, 8)).apply(gray), cv2.COLOR_GRAY2BGR)
            elif mode == "bw":
                gray = cv2.cvtColor(raster, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (3, 3), 0)
                raster = cv2.cvtColor(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12), cv2.COLOR_GRAY2BGR)
            else:
                lab = cv2.cvtColor(raster, cv2.COLOR_BGR2LAB)
                light, channel_a, channel_b = cv2.split(lab)
                light = cv2.createCLAHE(1.6, (8, 8)).apply(light)
                raster = cv2.cvtColor(cv2.merge((light, channel_a, channel_b)), cv2.COLOR_LAB2BGR)
            height, width = raster.shape[:2]
            if max(height, width) > 2800:
                scale = 2800 / max(height, width)
                raster = cv2.resize(raster, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
            success, encoded = cv2.imencode(".jpg", raster, [cv2.IMWRITE_JPEG_QUALITY, 93])
            if not success:
                raise ValueError("Nie udało się zapisać zdjęcia")
            return self.send_binary(200, encoded.tobytes(), "image/jpeg", "scan.jpg", {"X-Crop-Detected": "true" if crop_detected else "false"})
        except ValueError as error:
            return self.send_json(400, {"error": str(error)})
        except Exception as error:
            print("Błąd skanera:", repr(error))
            return self.send_json(500, {"error": "Nie udało się przetworzyć zdjęcia"})

    def handle_scanner_ocr_pdf(self):
        try:
            content = self.read_binary(MAX_OCR_BODY, "PDF")
        except ValueError as error:
            return self.send_json(413, {"error": str(error)})
        if not content.startswith(b"%PDF-"):
            return self.send_json(415, {"error": "Wybrany plik nie jest prawidłowym PDF-em"})
        if not OCR_LOCK.acquire(blocking=False):
            return self.send_json(429, {"error": "Trwa inne zadanie OCR. Spróbuj ponownie za chwilę."})
        try:
            with tempfile.TemporaryDirectory(prefix="pulpit-scan-ocr-") as temp:
                temp_path = Path(temp)
                input_path = temp_path / "scan.pdf"
                input_path.write_bytes(content)
                pages = pdf_page_count(input_path)
                if pages < 1 or pages > MAX_OCR_PAGES:
                    return self.send_json(422, {"error": f"PDF może mieć maksymalnie {MAX_OCR_PAGES} stron"})
                prefix = temp_path / "page"
                run_command(["pdftoppm", "-jpeg", "-r", "300", "-jpegopt", "quality=94", str(input_path), str(prefix)], timeout=360)
                images = sorted(temp_path.glob("page-*.jpg"), key=lambda path: int(re.search(r"(\d+)$", path.stem).group(1)))
                page_pdfs = []
                for index, image in enumerate(images, 1):
                    output_base = temp_path / f"ocr-page-{index}"
                    run_command(["tesseract", str(image), str(output_base), "-l", "pol+eng", "--dpi", "300", "--psm", "3", "pdf"], timeout=240)
                    page_pdfs.append(str(output_base.with_suffix(".pdf")))
                if not page_pdfs:
                    raise subprocess.CalledProcessError(1, "pdftoppm")
                output_path = temp_path / "document-ocr.pdf"
                run_command(["pdfunite", *page_pdfs, str(output_path)], timeout=120)
                payload = output_path.read_bytes()
            return self.send_binary(200, payload, "application/pdf", "skan-OCR.pdf")
        except subprocess.TimeoutExpired:
            return self.send_json(504, {"error": "OCR trwał zbyt długo"})
        except Exception as error:
            print("Błąd OCR skanera:", repr(error))
            return self.send_json(500, {"error": "Nie udało się utworzyć PDF-u OCR"})
        finally:
            OCR_LOCK.release()

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
            layout_mode = self.headers.get("X-OCR-Mode", "text").lower() == "layout"
            suffix = "uklad-OCR" if layout_mode else "OCR"
            output_name = f"{Path(original_name).stem or 'document'}-{suffix}.docx"
            with tempfile.TemporaryDirectory(prefix="pulpit-ocr-") as temp:
                temp_path = Path(temp)
                input_path = temp_path / "input.pdf"
                input_path.write_bytes(content)
                pages = pdf_page_count(input_path)
                if pages < 1 or pages > MAX_OCR_PAGES:
                    return self.send_json(422, {"error": f"PDF może mieć maksymalnie {MAX_OCR_PAGES} stron"})
                output_path = temp_path / "output.docx"
                if layout_mode:
                    build_layout_docx(input_path, temp_path, output_path, pages)
                else:
                    page_texts = extract_native_text(input_path, temp_path, pages)
                    if page_texts is None:
                        page_texts = ocr_pdf(input_path, temp_path, pages)
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

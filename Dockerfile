FROM python:3.13-slim

WORKDIR /app
COPY vendor/python-wheels /tmp/python-wheels

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       poppler-utils \
       tesseract-ocr \
       tesseract-ocr-eng \
       tesseract-ocr-pol \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --no-index --find-links=/tmp/python-wheels \
       pdf2docx==0.5.8 PyMuPDF==1.24.14 opencv-python-headless==4.10.0.84 \
       numpy==2.1.3 python-docx==1.1.2 fonttools==4.55.3 fire==0.7.1 \
       lxml==5.3.0 typing-extensions==4.12.2 termcolor==2.5.0 \
    && rm -rf /tmp/python-wheels \
    && mkdir -p /data

COPY backend/server.py /app/server.py
COPY index.html desktop.css desktop.js /app/web/
COPY apps /app/web/apps
COPY assets /app/web/assets

ENV PORT=8080 \
    NOTES_DB=/data/notes.db \
    COOKIE_SECURE=true

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" || exit 1

CMD ["python", "/app/server.py"]

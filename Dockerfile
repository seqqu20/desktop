FROM python:3.13-alpine

WORKDIR /app
COPY backend/server.py /app/server.py
COPY index.html desktop.css desktop.js /app/web/
COPY apps /app/web/apps
COPY assets /app/web/assets

RUN mkdir -p /data && chmod -R a+rX /app/web

ENV PORT=8080 \
    NOTES_DB=/data/notes.db \
    COOKIE_SECURE=true

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" || exit 1

CMD ["python", "/app/server.py"]

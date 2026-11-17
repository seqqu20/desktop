#!/usr/bin/env bash
cd "$(dirname "$0")"
PORT=8765
if command -v python3 >/dev/null 2>&1; then
  (sleep 1; open "http://127.0.0.1:$PORT/" 2>/dev/null || xdg-open "http://127.0.0.1:$PORT/" 2>/dev/null || true) &
  echo "Mój Pulpit działa lokalnie: http://127.0.0.1:$PORT/"
  python3 -m http.server "$PORT" --bind 127.0.0.1
else
  echo "Brakuje Python 3. Zainstaluj go i uruchom plik ponownie."
  read -r
fi


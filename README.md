# Pulpit

Strona główna stylizowana na pulpit systemu Windows. Każda tworzona aplikacja dostaje własną ikonę, pozycję w menu Start i okno. Pierwszą aplikacją jest lokalny edytor PDF — PDF Studio.

## Funkcje

- pulpit, menu Start i pasek zadań,
- okna z minimalizacją, maksymalizacją i przeciąganiem,
- PDF Studio: tekst, rysowanie, obrazy, skalowanie i eksport PDF,
- prywatny Notatnik w SQLite: logowanie, synchronizacja, autozapis i eksport TXT,
- Konwerter: JPG/PNG/WebP, łączenie PDF, TXT/Markdown do PDF oraz PDF do Word z OCR PL+EN w trybie tekstowym lub z zachowaniem układu,
- edycja, łączenie i tworzenie PDF odbywa się w przeglądarce; tylko OCR działa na prywatnym VPS,
- wszystkie biblioteki są zapisane lokalnie w repozytorium,
- gotowa konfiguracja Docker dla VPS.

## Windows — uruchomienie lokalne

Uruchom `START-WINDOWS.bat`. Skrypt wystartuje lokalny serwer i otworzy stronę w przeglądarce.

## macOS / Linux — uruchomienie lokalne

```bash
chmod +x START-MAC-LINUX.command
./START-MAC-LINUX.command
```

## VPS — pierwsze wdrożenie

Projekt działa w osobnym katalogu `/opt/desktop-pulpit`, osobnym kontenerze `desktop-pulpit-web` i domyślnie tylko na lokalnym porcie `8093`. Nie usuwa ani nie zatrzymuje innych projektów.

```bash
sudo mkdir -p /opt/desktop-pulpit
sudo chown "$USER":"$USER" /opt/desktop-pulpit
git clone https://github.com/seqqu20/desktop.git /opt/desktop-pulpit
cd /opt/desktop-pulpit

read -rsp "Ustaw hasło do Notatnika: " NOTES_PASSWORD; echo
NOTES_HASH=$(printf '%s' "$NOTES_PASSWORD" | sha256sum | awk '{print $1}')
SESSION_SECRET=$(openssl rand -hex 32)
printf 'PULPIT_PORT=8093\nNOTES_PASSWORD_SHA256=%s\nSESSION_SECRET=%s\nCOOKIE_SECURE=true\n' "$NOTES_HASH" "$SESSION_SECRET" > .env
chmod 600 .env
unset NOTES_PASSWORD NOTES_HASH SESSION_SECRET

docker compose up -d --build
docker compose ps
curl -I http://127.0.0.1:8093
```

## VPS — aktualizacja

```bash
cd /opt/desktop-pulpit
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Nie uruchamiaj `docker system prune`, `docker compose down` w katalogu innego projektu ani poleceń usuwających `/opt`.

## Podpięcie domeny

Reverse proxy na VPS powinno kierować wybraną domenę do `http://127.0.0.1:8093`. Port można zmienić bez edycji pliku:

```bash
PULPIT_PORT=8094 docker compose up -d --build
```

Dla OCR ustaw w sekcji `location /` konfiguracji Nginx limit i dłuższy czas oczekiwania:

```nginx
client_max_body_size 21M;
proxy_read_timeout 900s;
proxy_send_timeout 900s;
```

Notatki są przechowywane w trwałym wolumenie Dockera `desktop-pulpit-notes`. Przebudowa kontenera nie usuwa bazy. Nie usuwaj tego wolumenu.

## Dodawanie aplikacji

Nowe aplikacje umieszczaj w `apps/<nazwa>/`, a ich ikonę i okno dodawaj w `index.html` oraz rejestruj w `desktop.js`.

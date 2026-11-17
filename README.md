# Pulpit

Strona główna stylizowana na pulpit systemu Windows. Każda tworzona aplikacja dostaje własną ikonę, pozycję w menu Start i okno. Pierwszą aplikacją jest lokalny edytor PDF — PDF Studio.

## Funkcje

- pulpit, menu Start i pasek zadań,
- okna z minimalizacją, maksymalizacją i przeciąganiem,
- PDF Studio: tekst, rysowanie, obrazy, skalowanie i eksport PDF,
- dokumenty PDF są przetwarzane w przeglądarce,
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

Projekt działa w osobnym katalogu `/opt/pulpit`, osobnym kontenerze `pulpit-web` i domyślnie tylko na lokalnym porcie `8093`. Nie usuwa ani nie zatrzymuje innych projektów.

```bash
sudo mkdir -p /opt/pulpit
sudo chown "$USER":"$USER" /opt/pulpit
git clone https://github.com/seqqu20/pulpit.git /opt/pulpit
cd /opt/pulpit
docker compose up -d --build
docker compose ps
curl -I http://127.0.0.1:8093
```

## VPS — aktualizacja

```bash
cd /opt/pulpit
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

## Dodawanie aplikacji

Nowe aplikacje umieszczaj w `apps/<nazwa>/`, a ich ikonę i okno dodawaj w `index.html` oraz rejestruj w `desktop.js`.

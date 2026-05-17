# Invoice Ninja – Erfolgsrechnung Export

A lightweight self-hosted web tool that generates a **PDF tax report (Erfolgsrechnung)** directly from your Invoice Ninja instance — designed for Swiss sole proprietors (*Einzelfirma*) filing their personal income tax return.

## Features

- 📊 **Live preview** of revenue, expenses and profit before exporting
- 📄 **One-click PDF download** — generated in-memory, no files stored on the server
- ⚙️ **Settings UI** — configure your IN URL, API token, company name and category exclusions directly in the browser
- 🗂️ **Category filter** — select which expense categories to include or exclude per export
- 🐳 **Docker-ready** — runs as a standalone container, completely independent of your Invoice Ninja container

## PDF Report Contents

1. **Einnahmen** — all payments received in the selected year (cash basis)
2. **Ausgaben** — all expenses grouped by category, with individual line items
3. **Ergebnis** — Reingewinn / Verlust
4. **Offene Forderungen** — unpaid invoices (Sent / Overdue)
5. **Kategorie-Übersicht** — compact expense summary by category

---

## Quick Start (Docker)

### 1. Clone the repository

```bash
git clone https://github.com/fabianbircher/in-steuerexport.git
cd in-steuerexport
```

### 2. Start the container

```bash
docker compose up -d --build
```

### 3. Open the web UI

```
http://your-nas-ip:5757
```

Go to **Einstellungen** and enter your Invoice Ninja URL and API token. Click **Verbindung testen** to verify, then save.

---

## Configuration

Settings are stored in `config/settings.json` (persisted via Docker volume, never overwritten by updates).

On first start, copy the example:

```bash
cp config/settings.example.json config/settings.json
```

### settings.json

| Field | Description |
|---|---|
| `in_url` | Full Invoice Ninja API URL, e.g. `https://invoices.example.com/api/v1` |
| `in_token` | Your Invoice Ninja API token (Settings → API Tokens) |
| `firma` | Company / business name shown on the PDF |
| `name` | Owner name shown on the PDF |
| `excluded_categories` | List of expense category names to exclude from exports |
| `port` | Port the server listens on (default: `5757`) |

All settings can also be configured through the **Einstellungen** page in the browser — no need to edit the file manually.

### Environment variables (optional override)

You can override the config file path via environment variable:

```yaml
# docker-compose.yml
environment:
  - CONFIG_PATH=/app/config/settings.json
```

---

## Invoice Ninja Integration

Add a custom navigation link in Invoice Ninja so the export is accessible directly from the sidebar:

1. IN → **Settings → Custom Navigation Links**
2. Add:
   - **Label:** Erfolgsrechnung Export
   - **URL:** `http://your-nas-ip:5757`
3. Save

---

## Running locally (without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and edit config
cp config/settings.example.json config/settings.json
# Edit config/settings.json with your IN URL and token

# Start server
python3 server.py
```

Open `http://localhost:5757` in your browser.

---

## Development

```bash
# Watch logs
docker compose logs -f

# Rebuild after code changes
docker compose up -d --build

# Stop
docker compose down
```

The `config/` directory is mounted as a volume — your settings survive container rebuilds and updates.

---

## Security Notes

- The server binds to `0.0.0.0` — restrict access via your NAS firewall or reverse proxy
- The API token is stored in `config/settings.json` — this file is excluded from git via `.gitignore`
- Consider placing the service behind a reverse proxy with HTTPS and authentication if exposed beyond your local network

---

## Requirements

- Python 3.11+ (or Docker)
- Invoice Ninja v5 with API access
- `requests`, `reportlab` (installed via `requirements.txt`)

---

## License

MIT

# TikTok Farm

Automated TikTok account farming system: natural account growth, slideshow content creation, and affiliate post scheduling.

## Features

- **Proxy Manager** — Proxy CRUD with health checks, auto-rotation, CSV + DB sync
- **Account Manager** — Full account lifecycle (pending → warming → active → banned detection)
- **Browser Manager** — Camoufox/Playwright singleton with per-account fingerprint + proxy isolation
- **Farm Engine** — Human-like behavior scripts: scroll, like, comment, follow, watch video
- **Content Pipeline** — Pillow-based slideshow compositing with templates (product + brand + rating + review)
- **Post Engine** — TikTok slideshow upload with login, caption, hashtags, affiliate links
- **Scheduler** — APScheduler with time-slot randomization, priority queue, retry (max 3)
- **Health Monitor** — Periodic login/shadowban/rate-limit checks with Telegram alerts
- **Dashboard** — FastAPI + Chart.js real-time monitoring

## Architecture

```
tiktok-farm/
├── config/              # YAML configs + proxy CSV
├── src/                 # Core modules
│   ├── main.py          # FastAPI app entry point
│   ├── proxy_manager.py # Proxy CRUD + health
│   ├── account_manager.py # Account CRUD + SQLite
│   ├── browser_manager.py # Camoufox factory
│   ├── farm_engine.py   # Behavior scripts
│   ├── content_pipeline.py # Image compositing
│   ├── post_engine.py   # TikTok upload
│   ├── scheduler.py     # APScheduler jobs
│   ├── health_monitor.py # Account health checks
│   └── telegram_alert.py # Notifications
├── web/                 # FastAPI routes + HTML dashboard
├── data/                # SQLite DB + generated posts
├── content/             # Product images, brand logos
├── profiles/            # Browser fingerprints
└── logs/                # Application logs
```

## Quick Start

### 1. PostgreSQL (Docker)

```bash
cp .env.example .env
docker compose up -d
docker compose ps   # wait until healthy
```

Default: `postgresql://tiktok_farm:tiktok_farm_secret@localhost:5433/tiktok_farm`  
Schema is applied automatically from `docker/postgres/init/01-schema.sql`.

To use SQLite instead (no Docker), set in `config/settings.yaml`:

```yaml
database:
  driver: sqlite
  path: data/farm.db
```

### 2. Install dependencies

```bash
cd tiktok-farm
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure

Edit `config/settings.yaml`:
- `database.driver`: `postgresql` (Docker) or `sqlite` (local file)
- Proxy CSV, timing parameters, Telegram bot token

Add proxies to `config/proxies.csv`:
```csv
ip,port,protocol,username,password,status
1.2.3.4,8080,http,,,active
```

### 4. Add accounts

Via API or directly:
```bash
curl -X POST "http://localhost:8000/api/accounts?username=myaccount&proxy_id=1"
```

### 5. Run

With web dashboard:
```bash
python src/main.py
```

Headless mode (no web server):
```bash
python src/main.py --headless
```

The dashboard is at: http://localhost:8000/api/dashboard

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/accounts` | List all accounts |
| GET | `/api/accounts/{id}` | Get account details |
| GET | `/api/accounts/{id}/stats` | Account metrics + activity log |
| POST | `/api/accounts` | Create new account |
| PATCH | `/api/accounts/{id}` | Update account status/proxy |
| DELETE | `/api/accounts/{id}` | Delete account |
| GET | `/api/proxies` | List all proxies |
| POST | `/api/proxies/check` | Run proxy health check |
| GET | `/api/performance` | Aggregated stats |
| GET | `/api/health` | System health status |
| POST | `/api/actions/farm/{id}` | Trigger farm session |
| POST | `/api/actions/post/{id}` | Trigger post upload |
| POST | `/api/actions/check/{id}` | Run health check |
| GET | `/api/alerts` | List unresolved alerts |
| GET | `/api/dashboard` | Web dashboard UI |

## Content Pipeline

Place your assets in:
- `content/products/` — Product photos (jpg/png)
- `content/brands/` — Brand logos (png with transparency)
- `content/templates/` — Font files (optional)

Layout templates in `config/templates.yaml` control slide composition with:
- Product image placement
- Brand logo overlay
- Star rating + text
- Review text with word wrapping
- Price tag + CTA button
- Affiliate disclaimer

## Farm Behavior

Each farm session (10-15 min, 2-3/day) performs:
1. Scroll feed with random pauses (simulates browsing)
2. Watch videos for 15-60 seconds each
3. Like 2-5 videos
4. Follow 1-2 accounts (low rate to avoid detection)
5. Comment on 1-2 videos from a configurable pool
6. Final scroll session

All actions have random delays mimicking human behavior.

## Tech Stack

- **Python 3.10+** with async/await everywhere
- **FastAPI** + Uvicorn for web server
- **Playwright** for browser automation
- **Pillow** for image compositing
- **APScheduler** for job scheduling
- **PostgreSQL** (Docker, default) / **SQLite** (pilot, no Docker)
- **Chart.js** for dashboard visualizations
- **Camoufox** target for anti-detect (Firefox-based)

## License

MIT

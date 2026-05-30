# TikTok Farm - System Specification v1.1

## Tổng quan
Hệ thống tự động quản lý nhiều tài khoản TikTok: nuôi acc tự nhiên, tạo nội dung slideshow, đăng bài affiliate.
Pilot: 5 acc → Scale: 100 acc.

## Tech stack
- **Anti-Detect:** Camoufox (optional, `camoufox.use_camoufox: true`) hoặc Playwright Chromium (default)
- **Automation:** Playwright Python (async)
- **Image Processing:** Pillow (PIL)
- **Scheduler:** APScheduler + Redis job store (optional) hoặc MemoryJobStore
- **Dashboard:** FastAPI + Chart.js + HTML
- **Database:** SQLite (pilot) / PostgreSQL (scale, Docker)
- **Cache/Queue:** Redis 7 (Docker, optional)
- **Server:** Linux VPS Ubuntu 22.04+ / macOS dev

---

## I. Architecture

```
┌─────────────────────────────────────────────────────┐
│                     Main App                         │
│  (FastAPI + APScheduler + SQLite/PostgreSQL)         │
├─────────┬──────────┬──────────┬──────────────────────┤
│  Proxy   │ Content  │ Schedule  │ Farm                │
│  Mgr     │ Pipeline │  Engine   │ Behavior Engine     │
├─────────┼──────────┼──────────┼──────────────────────┤
│ Camoufox│ Pillow   │ Redis     │ Playwright +         │
│ /Chromium│ Template │ (optional)│ SessionService       │
└─────────┴──────────┴──────────┴──────────────────────┘
         SessionService ──► proxy check + cookie persist
         WarmupManager  ──► 7-day warm-up orchestration
```

---

## II. Modules

### 1. Proxy Manager (`proxy_manager.py`)
- Quản lý danh sách proxy (file CSV hoặc DB)
- Check proxy alive (`check_proxy`, `check_all_proxies`)
- Bind 1 proxy → 1 account (`proxy_id`)
- **Auto-rotate** khi fail (`ensure_proxy_for_account`, `rotate_proxy_for_account`)
- Sync CSV ↔ DB

### 2. Account Manager (`account_manager.py`)
- CRUD tài khoản TikTok
- Status: `pending`, `warming`, `active`, `banned`, `shadowbanned`, `paused`
- **Cookie persistence** (`cookie_data`, `save_cookies`)
- **Password** field (optional, cho web login)
- Import từ `config/accounts.yaml` (`load_accounts_from_yaml`)
- Warm-up: `complete_warming()` sau N ngày
- Posts, alerts, farm_activities, stats

### 3. Browser Profile Factory (`browser_manager.py`)
- Singleton quản lý browser instances
- `create_browser(account_id)` — alias của `create_context`
- Camoufox khi `use_camoufox: true`, fallback Chromium
- Proxy per context, `storage_state` per account trong `profiles/{id}/`
- Lifecycle: open, close, recycle

### 4. Session Service (`session_service.py`) — **mới v1.1**
- `prepare(account_id)`: check proxy, rotate nếu dead, trả credentials + proxy_url
- `save_cookies`: persist session sau login/upload

### 5. Warm-up Manager (`warmup_manager.py`) — **mới v1.1**
- `pending` → `warming` (khi có proxy)
- Hành vi farm scale theo ngày 1–7 (`WARMUP_DAY_PROFILES`)
- `warming` → `active` sau `warmup.days`
- Cron daily tick + chạy lúc startup

### 6. Farm Behavior Engine (`farm_engine.py`)
- `scroll_feed`, `like_videos`, `comment_random`, `follow_accounts`, `watch_video_full`
- `run_farm_session` — duration/actions config-driven
- Warm-up accounts dùng profile ngày tương ứng

### 7. Content Pipeline (`content_pipeline.py`)
- Slideshow từ product + brand + rating + review
- Output: `data/posts/{account_id}/{timestamp}/`
- `templates.yaml`

### 8. Post Engine (`post_engine.py`)
- Login: session / cookies / username+password
- **Persist cookies** + `storage_state` sau login
- Upload slideshow, caption, hashtags, affiliate link
- Trả `tiktok_post_id` khi detect được từ URL

### 9. Scheduler (`scheduler.py`)
- APScheduler async
- **Redis job store** khi `redis.enabled: true`
- 3 post/ngày, 2–3 farm/ngày, random time slots
- Retry max 3, exponential backoff
- **Priority:** farm > post (defer post nếu farm đang chạy)
- Per-account `asyncio.Lock`
- Tích hợp SessionService, WarmupManager

### 10. Health Monitor (`health_monitor.py`)
- Login check, shadowban (views vs followers), rate limit
- **Banned detection** (`check_account_banned`)
- Hashtag visibility check (best-effort, `check_hashtag_visibility`)
- Telegram alerts

### 11. Dashboard API (`web/api.py`, `web/templates/`)
- `GET /api/accounts`, `/accounts/{id}/stats`, `/performance`, `/health`
- `GET /api/alerts?resolved=0|1` — lọc resolved
- HTML + Chart.js dashboard

### 12. Database
- SQLite file hoặc PostgreSQL (Docker)
- Schema: `accounts`, `proxies`, `posts`, `farm_activities`, `alerts`
- `accounts.password` (PostgreSQL migration `02-alter-accounts.sql`)

---

## III. Cấu trúc thư mục

```
tiktok-farm/
├── config/
│   ├── settings.yaml
│   ├── proxies.csv
│   ├── templates.yaml
│   └── accounts.yaml
├── src/
│   ├── main.py
│   ├── database.py
│   ├── session_service.py      # v1.1
│   ├── warmup_manager.py       # v1.1
│   ├── proxy_manager.py
│   ├── account_manager.py
│   ├── browser_manager.py
│   ├── farm_engine.py
│   ├── content_pipeline.py
│   ├── post_engine.py
│   ├── scheduler.py
│   ├── health_monitor.py
│   └── telegram_alert.py
├── web/
│   ├── api.py
│   └── templates/index.html
├── docker/
│   └── postgres/init/
├── docker-compose.yml          # postgres + redis
├── data/
├── content/
├── profiles/
└── logs/
```

---

## IV. Config mẫu (`settings.yaml`)

```yaml
database:
  driver: postgresql   # hoặc sqlite
  host: localhost
  port: 5433
  name: tiktok_farm
  user: tiktok_farm
  password: tiktok_farm_secret

camoufox:
  use_camoufox: false
  headless: true
  profile_dir: "profiles/"

redis:
  enabled: false
  host: localhost
  port: 6379

warmup:
  days: 7
  auto_assign_proxy: true

accounts:
  yaml_path: "config/accounts.yaml"

content:
  default_affiliate_link: ""

scheduler:
  posts_per_day: 3
  farm_sessions_per_day: 3
  post_time_slots:
    - ["08:00", "11:00"]
    - ["14:00", "17:00"]
    - ["19:00", "22:00"]
```

---

## V. Docker services

```bash
cp .env.example .env
docker compose up -d    # postgres:5433, redis:6379
```

| Service | Image | Port mặc định |
|---------|-------|----------------|
| postgres | postgres:16-alpine | 5433 |
| redis | redis:7-alpine | 6379 |

---

## VI. Module dependencies

```
main.py
├── database.py
├── session_service.py → proxy_manager, account_manager
├── warmup_manager.py → account_manager, proxy_manager
├── scheduler.py → farm_engine, post_engine, session_service, warmup_manager
│   └── redis jobstore (optional)
├── browser_manager.py → Camoufox | Chromium
├── health_monitor.py
└── web/api.py
```

---

## VII. Pilot checklist (5 acc)

| # | Hạng mục | Trạng thái |
|---|----------|-----------|
| 1 | Proxy Manager + CSV + rotate | ✅ |
| 2 | Account Manager + DB + YAML import | ✅ |
| 3 | Browser Manager (Camoufox optional) | ⚠️ Chromium default; bật Camoufox khi cài package |
| 4 | Farm Engine | ✅ code; ⚠️ cần test TikTok thật |
| 5 | Content Pipeline | ✅ |
| 6 | Post Engine + cookie persist | ✅ code; ⚠️ cần test TikTok thật |
| 7 | Scheduler + Redis optional | ✅ |
| 8 | Health Monitor + Telegram | ✅ |
| 9 | Dashboard API + UI | ✅ |
| 10 | Warm-up 7 ngày orchestration | ✅ |
| 11 | Test full flow 1 account | ❌ manual QA |
| 12 | Posting thực tế | ❌ manual QA |

---

## VIII. Trạng thái implementation (v1.1)

### ✅ Hoàn thiện
- PostgreSQL + SQLite abstraction
- Proxy check + auto-rotate
- Session cookie/storage persist
- Warm-up manager (7 ngày, scale actions)
- Scheduler priority farm > post
- Redis job store (config flag)
- Alerts API filter resolved
- `tiktok_post_id` lưu sau upload
- Health: banned detection

### ⚠️ Cần verify trên TikTok production
- DOM selectors (login, upload, farm actions)
- Camoufox anti-detect hiệu quả
- Hashtag shadowban detection accuracy
- CAPTCHA / 2FA flows

### ❌ Chưa làm / ngoài scope v1.1
- React dashboard (dùng HTML + Chart.js)
- WebSocket real-time
- E2E automated test suite
- TikTok Shop API integration

---

## IX. Quy tắc coding

1. **Async-first** — asyncio cho I/O
2. **Error handling** — try/except + log, không crash app
3. **Graceful cleanup** — đóng browser khi shutdown
4. **Config-driven** — paths và timing từ `settings.yaml`
5. **Thread safety** — per-account lock, không share browser context giữa coroutines

---

## X. Chạy nhanh

```bash
docker compose up -d
pip install -r requirements.txt
playwright install chromium
python src/main.py
```

Dashboard: http://localhost:8000/api/dashboard

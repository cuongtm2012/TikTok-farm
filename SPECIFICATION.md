# TikTok Farm - System Specification v1.0

## Tổng quan
Hệ thống tự động quản lý nhiều tài khoản TikTok: nuôi acc tự nhiên, tạo nội dung slideshow, đăng bài affiliate.
Pilot: 5 acc → Scale: 100 acc.

## Tech stack
- **Anti-Detect:** Camoufox (Python bindings, Firefox-based, C++ stealth, free)
- **Automation:** Playwright Python (async)
- **Image Processing:** Pillow (PIL)
- **Scheduler:** APScheduler + Redis 
- **Dashboard:** FastAPI + Chart.js + SQLite (pilot), PostgreSQL (scale)
- **Database:** SQLite (pilot) / PostgreSQL (scale)
- **Server:** Linux VPS Ubuntu 22.04+

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
│ Camoufox│ Pillow   │ Queue    │ Playwright +         │
│ + Proxy │ Template │ (Redis)  │ Camoufox browser     │
└─────────┴──────────┴──────────┴──────────────────────┘
```

## II. Modules

### 1. Proxy Manager (`proxy_manager.py`)
- Quản lý danh sách proxy (file CSV hoặc DB)
- Check proxy alive trước khi dùng
- Bind 1 proxy → 1 account profile
- Auto-rotate proxy khi fail
- Storage format: `{"ip": "1.2.3.4", "port": 8080, "protocol": "http/socks5", "username": "", "password": "", "status": "active/banned/dead"}`

### 2. Account Manager (`account_manager.py`)
- CRUD tài khoản TikTok
- Mỗi acc = 1 profile Camoufox riêng biệt (fingerprint + proxy + cookie)
- Lưu trạng thái: active, warming, banned, shadowbanned, paused
- Auto warm-up sequence (7 ngày đầu)
- Storage: `account_id, username, proxy_id, status, follower_count, created_at, last_active, notes`

### 3. Browser Profile Factory (`browser_manager.py`)
- Singleton quản lý Camoufox instances
- Factory method: `create_browser(account_id, headless=True)` 
- Tự động apply fingerprint + proxy cho mỗi profile
- Quản lý lifecycle (open, close, recycle)
- Cơ chế reuse instance nếu đang chạy task kế tiếp

### 4. Farm Behavior Engine (`farm_engine.py`)
Behavior scripts chạy để nuôi acc như người thật:
- `scroll_feed(duration_minutes=10)` — scroll, random dừng, xem video
- `like_videos(count=5, topic="")` — like video theo chủ đề
- `comment_random(comment_pool=["Nice!", "Great content!"])` — comment tủ
- `follow_accounts(count=3)` — follow theo tỷ lệ (không follow ồ ạt)
- `watch_video_full(min_seconds=15, max_seconds=60)` — xem video hết
- Schedule mỗi phiên: 10-15 phút, 2-3 phiên/ngày, random giờ

### 5. Content Pipeline (`content_pipeline.py`)
Tạo slideshow ảnh tự động:
- Đầu vào: folder ảnh product + brand logo
- Layout template: `product_photo | rating_stars (4.5/5) | review_text | brand_logo`
- Output: folder `/posts/{account_id}/{timestamp}/` gồm 3-5 ảnh
- Config: `templates.yaml` để customize layout

### 6. Post Engine (`post_engine.py`)
Dùng Camoufox + Playwright để:
- Login TikTok web (giữ session)
- Upload slideshow (chọn nhiều ảnh)
- Điền caption + hashtag
- Tag sản phẩm affiliate (từ link TikTok Shop)
- Random giờ đăng trong khung cho phép

### 7. Scheduler (`scheduler.py`)
APScheduler-based:
- Queue jobs cho mỗi account
- 3 post/ngày/account, random giờ (sáng 8-11, chiều 14-17, tối 19-22)
- Farm sessions: 2-3/ngày, random
- Retry queue khi fail (tối đa 3 lần)
- Priority queue: ưu tiên farm behavior > post

### 8. Health Monitor (`health_monitor.py`)
Check định kỳ (1 lần/giờ):
- Account còn alive? (login check)
- Bị shadowban? (view count trên post gần nhất)
- Rate limit? (error response từ TikTok)
- Alert qua Telegram khi phát hiện bất thường

### 9. Dashboard API (`api/` và `web/`)
FastAPI backend + React/Chart.js frontend:
- `GET /api/accounts` — list accounts + status
- `GET /api/accounts/{id}/stats` — per-account metrics
- `GET /api/performance` — tổng hợp: total posts, total views, avg engagement
- `GET /api/health` — system health + alerts
- Web UI: real-time charts, account table, alert log

### 10. Database Schema
```sql
-- accounts
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    proxy_id INTEGER REFERENCES proxies(id),
    status TEXT DEFAULT 'pending',  -- pending, warming, active, banned, shadowbanned, paused
    followers INTEGER DEFAULT 0,
    following INTEGER DEFAULT 0,
    total_posts INTEGER DEFAULT 0,
    total_views INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,
    cookie_data TEXT,  -- JSON
    notes TEXT
);

-- proxies
CREATE TABLE proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    protocol TEXT DEFAULT 'http',
    username TEXT,
    password TEXT,
    status TEXT DEFAULT 'active',
    last_checked TIMESTAMP,
    fail_count INTEGER DEFAULT 0
);

-- posts
CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id),
    tiktok_post_id TEXT,
    content_path TEXT,
    caption TEXT,
    hashtags TEXT,
    affiliate_link TEXT,
    status TEXT DEFAULT 'pending',  -- pending, posted, failed, deleted
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    scheduled_at TIMESTAMP,
    posted_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- farm_activities
CREATE TABLE farm_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id),
    activity_type TEXT,  -- scroll, like, comment, follow, watch
    duration_seconds INTEGER,
    actions_count INTEGER,
    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- alerts
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id),
    alert_type TEXT,  -- shadowban, rate_limit, banned, login_fail
    message TEXT,
    resolved INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## III. Cấu trúc thư mục

```
tiktok-farm/
├── config/
│   ├── settings.yaml          # Main config: DB path, API keys, timing
│   ├── proxies.csv             # Danh sách proxy
│   ├── templates.yaml          # Content layout templates
│   └── accounts.yaml           # Danh sách account (hoặc qua DB)
├── src/
│   ├── main.py                 # Entry point (FastAPI app)
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
│   ├── api.py                  # FastAPI routes
│   ├── templates/              # HTML templates
│   └── static/                 # JS, CSS
├── data/
│   ├── farm.db                 # SQLite database
│   ├── proxies.csv
│   └── posts/                  # Generated content
├── content/
│   ├── products/               # Product images
│   ├── brands/                 # Brand logos
│   └── templates/              # Overlay templates
├── profiles/                   # Camoufox browser profiles
├── logs/
│   └── farm.log
├── requirements.txt
└── README.md
```

## IV. Config mẫu (settings.yaml)

```yaml
app:
  name: "TikTok Farm"
  version: "1.0.0"
  debug: false
  log_level: "INFO"

database:
  path: "data/farm.db"

proxies:
  csv_path: "config/proxies.csv"
  check_timeout: 5
  max_fail_before_disable: 3

scheduler:
  posts_per_day: 3
  farm_sessions_per_day: 3
  farm_session_minutes: 15
  post_time_slots:
    - ["08:00", "11:00"]
    - ["14:00", "17:00"]
    - ["19:00", "22:00"]

content:
  images_per_post: 5
  output_dir: "data/posts/"

camoufox:
  headless: true
  profile_dir: "profiles/"
  navigation_timeout: 30000

health_check:
  interval_minutes: 60
  alert_on_shadowban: true
  alert_on_rate_limit: true

telegram:
  enabled: false
  bot_token: ""
  chat_id: ""
```

## V. Module dependencies

```
main.py
├── scheduler.py → farm_engine.py, post_engine.py
│   ├── browser_manager.py → Camoufox
│   └── account_manager.py → DB
├── content_pipeline.py → Pillow
├── health_monitor.py → browser_manager, account_manager
├── web/api.py → account_manager, health_monitor
└── telegram_alert.py
```

## VI. Quy tắc coding

1. **Async-first** — sử dụng asyncio cho mọi I/O (browser calls, network)
2. **Error handling** — mọi module có try/except, log lỗi, không crash app
3. **Graceful cleanup** — đóng browser instances khi app shutdown
4. **No hardcoded paths** — tất cả path từ settings.yaml
5. **Config-driven** — behavior parameters trong settings.yaml, không hardcode
6. **Thread safety** — Camoufox instances không share giữa các coroutines

## VII. Pilot checklist (5 acc)

- [ ] Code Proxy Manager + import proxies từ CSV
- [ ] Code Account Manager + DB schema
- [ ] Code Browser Manager (Camoufox integration)
- [ ] Code Farm Engine (scroll, like, comment, watch, follow)
- [ ] Code Content Pipeline (Pillow template)
- [ ] Code Post Engine (login + upload + tag)
- [ ] Code Scheduler (APScheduler jobs)
- [ ] Code Health Monitor + Telegram alert
- [ ] Code Dashboard API + Web UI
- [ ] Test full flow với 1 account
- [ ] Warm-up 5 accounts trong 7 ngày
- [ ] Start posting thực tế

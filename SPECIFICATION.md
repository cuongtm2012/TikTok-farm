# TikTok Farm - System Specification v2.0

## Tổng quan
Hệ thống tự động quản lý nhiều tài khoản TikTok: nuôi acc tự nhiên, tạo nội dung slideshow, đăng bài affiliate.
Pilot: 5 acc → Scale: 100 acc.

**Mô hình kiếm tiền:** Affiliate 2 lớp
- **Lớp 1 (3-5 acc Real):** TikTok Shop Affiliate (CMND người thân) → nhận hoa hồng về ngân hàng
- **Lớp 2 (95-97 acc Farm):** Tương tác kích thích engagement + bio link affiliate ngoài

## Tech stack
- **Anti-Detect:** Camoufox (optional, `camoufox.use_camoufox: true`) hoặc Playwright Chromium (default)
- **Automation:** Playwright Python (async)
- **Image Processing:** Pillow (PIL)
- **Video Processing:** ffmpeg-python
- **Scheduler:** APScheduler + Redis job store (optional) hoặc MemoryJobStore
- **Dashboard:** FastAPI + Chart.js + HTML
- **Database:** SQLite (pilot) / PostgreSQL (scale, Docker)
- **Cache/Queue:** Redis 7 (Docker, optional)
- **Server:** Linux VPS Ubuntu 22.04+ / macOS dev

---

## I. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Main App                               │
│  (FastAPI + APScheduler + SQLite/PostgreSQL)                │
├────────┬──────────┬───────────┬──────────────┬──────────────┤
│  Proxy │  Content │ Schedule  │    Farm      │ Affiliate    │
│  Mgr   │ Pipeline │  Engine   │  Behavior    │ Pipeline     │
├────────┼──────────┼───────────┼──────────────┼──────────────┤
│Camo-   │ Pillow   │ Redis     │ Playwright + │ Scanner SP   │
│fox     │ ffmpeg   │(optional) │ SessionSvc   │ ↓ Download   │
│/Chrom  │ Template │           │              │ ↓ Edit video │
│        │          │           │              │ ↓ Upload     │
└────────┴──────────┴───────────┴──────────────┴──────────────┘
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

### 4. Session Service (`session_service.py`)
- `prepare(account_id)`: check proxy, rotate nếu dead, trả credentials + proxy_url
- `save_cookies`: persist session sau login/upload

### 5. Warm-up Manager (`warmup_manager.py`)
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

### 8. Post Engine (`post_engine.py`) — **MỚI: hỗ trợ video**
- Login: session / cookies / username+password
- **Persist cookies** + `storage_state` sau login
- **Upload video (mp4)** — mở rộng từ slideshow ảnh
- Upload slideshow, caption, hashtags, affiliate link
- Trả `tiktok_post_id` khi detect được từ URL

### 9. Affiliate Pipeline (`src/affiliate/`) — **MỚI v2.0**
- **Scanner SP Trending** (`scanner.py`):
  - Crawl TikTok Affiliate Marketplace tìm SP bán chạy, commission cao
  - Lọc theo tiêu chí: niche, giá, commission %, doanh số
- **Downloader** (`downloader.py`):
  - Tải video mẫu từ TikTok hoặc nguồn khác
  - Dùng yt-dlp / requests
- **Video Editor** (`editor.py`):
  - ffmpeg: cắt clip, ghép nhiều clip, thêm nhạc nền
  - Pillow: text overlay, link QR, caption
- **Uploader** (`uploader.py`):
  - Gọi PostEngine để đăng video + link affiliate
  - Lịch đăng riêng cho account Real vs Farm

### 10. Scheduler (`scheduler.py`)
- APScheduler async
- **Redis job store** khi `redis.enabled: true`
- 3 post/ngày (farm), 1-2 video/ngày (real), 2–3 farm/ngày, random time slots
- Retry max 3, exponential backoff
- **Priority:** farm > post (defer post nếu farm đang chạy)
- Per-account `asyncio.Lock`
- Tích hợp SessionService, WarmupManager, AffiliatePipeline

### 11. Health Monitor (`health_monitor.py`)
- Login check, shadowban (views vs followers), rate limit
- **Banned detection** (`check_account_banned`)
- Hashtag visibility check (best-effort, `check_hashtag_visibility`)
- Telegram alerts

### 12. Dashboard API (`web/api.py`, `web/templates/`)
- `GET /api/accounts`, `/accounts/{id}/stats`, `/performance`, `/health`
- `GET /api/alerts?resolved=0|1` — lọc resolved
- **Settings TikTok API** — ms_token input, save, test
- **TikTok public profile** (optional, [davidteather/TikTok-Api](https://github.com/davidteather/TikTok-Api)):
  - `GET /api/tiktok/profile/{username}` — tra cứu followers/videos (không ghi DB)
  - `POST /api/accounts/{id}/sync-profile` — đồng bộ stats vào DB
  - `POST /api/accounts/sync-profiles` — đồng bộ hàng loạt
  - Cần `TIKTOK_MS_TOKEN` (cookie `msToken` từ tiktok.com)
- HTML + Chart.js dashboard
- **MỚI:** Tab Settings (ms_token config, test API)

### 13. Database
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
│   ├── session_service.py
│   ├── warmup_manager.py
│   ├── proxy_manager.py
│   ├── account_manager.py
│   ├── browser_manager.py
│   ├── farm_engine.py
│   ├── content_pipeline.py
│   ├── post_engine.py
│   ├── scheduler.py
│   ├── health_monitor.py
│   ├── tiktok_profile.py       # optional TikTok-Api public stats
│   ├── telegram_alert.py
│   └── affiliate/              # MỚI v2.0
│       ├── __init__.py
│       ├── scanner.py          # Crawl TikTok Affiliate Marketplace
│       ├── downloader.py       # Download video mẫu
│       ├── editor.py           # ffmpeg + Pillow edit video
│       └── uploader.py         # Post video + link affiliate
├── web/
│   ├── api.py
│   ├── templates/index.html
│   └── static/
│       ├── css/dashboard.css
│       └── js/dashboard.js
├── docker/
│   └── postgres/init/
├── docker-compose.yml
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
  real_account_ids: [1, 2, 3]     # MỚI: account thật dùng TikTok Shop Affiliate

content:
  default_affiliate_link: ""

affiliate:                         # MỚI v2.0
  commission_min_pct: 10          # Lọc SP có commission >= 10%
  trending_refresh_hours: 6       # Quét lại SP trending mỗi 6h
  video_output_dir: "data/videos/"
  real_account_post_schedule:     # Lịch riêng cho account Real
    posts_per_day: 2
    time_slots: [["19:00", "22:00"]]  # Giờ vàng

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
├── affiliate/  (MỚI)
│   ├── scanner.py → web scraping
│   ├── downloader.py → yt-dlp/requests
│   ├── editor.py → ffmpeg, Pillow
│   └── uploader.py → post_engine
└── web/api.py
```

---

## VII. Pilot checklist (5 acc)

| # | Hạng mục | Trạng thái |
|---|----------|-----------|
| 1 | Proxy Manager + CSV + rotate | ✅ |
| 2 | Account Manager + DB + API import | ✅ (đã tắt YAML import, dùng API) |
| 3 | Browser Manager (Camoufox optional) | ⚠️ Chromium default; bật Camoufox khi cài package |
| 4 | Farm Engine (DOM selectors 2026) | ✅ fixed: like/comment/follow dùng aria-label |
| 5 | Content Pipeline | ✅ |
| 6 | Post Engine + cookie persist | ✅ login check dùng profile link |
| 7 | Scheduler + Redis optional | ✅ |
| 8 | Health Monitor + Telegram | ✅ login check dùng profile link |
| 9 | Dashboard API + UI | ✅ |
| 10 | Settings API (ms_token) | ✅ |
| 11 | Warm-up 7 ngày orchestration | ✅ |
| 12 | Posting thực tế | ❌ chưa test |
| **MỚI** | | |
| 13 | Scanner SP Trending | ✅ (Playwright + sample fallback) |
| 14 | Downloader video mẫu | ✅ (yt-dlp + direct URL) |
| 15 | Video Editor (ffmpeg) | ✅ |
| 16 | Upload video + link affiliate | ✅ (PostEngine.upload_video) |
| 17 | Lịch post riêng Real vs Farm | ✅ (`real_account_ids` + golden hours) |

---

## VIII. Trạng thái implementation (v2.0)

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
- Dashboard: Settings tab (ms_token config)
- Loading spinners + toast indicators

### 🔧 Cần test production
- Affiliate Scanner: crawl TikTok Shop (cần login / DOM thật)
- Video download từ TikTok URL (cần yt-dlp + token)
- Upload video mp4 trên TikTok (DOM upload 2026)

### ⚠️ Cần verify trên TikTok production
- DOM selectors (login, upload, farm actions)
- Camoufox anti-detect hiệu quả
- Hashtag shadowban detection accuracy
- CAPTCHA / 2FA flows
- Upload video (mp4) hoạt động

### ❌ Chưa làm / ngoài scope v2.0
- React dashboard (dùng HTML + Chart.js)
- WebSocket real-time
- E2E automated test suite
- TikTok Shop API chính thức (đang chờ approve)

---

## IX. Affiliate Pipeline Flow (MỚI v2.0)

```
1. Scanner SP Trending
   └── Crawl TikTok Affiliate Marketplace
       └── Lọc: commission >= 10%, doanh số cao, niche phù hợp
           └── Output: JSON {sp_id, name, price, commission%, video_urls[]}

2. Download Video Mẫu
   └── yt-dlp / requests
       └── Lưu: data/videos/{sp_id}/raw/

3. Edit Video
   └── ffmpeg: cắt clip ngắn (15-60s), ghép cảnh, nhạc nền
   └── Pillow: text overlay (giá, link), QR code
       └── Output: data/videos/{sp_id}/final/

4. Upload + Link Affiliate
   └── PostEngine (Playwright)
       └── Đăng lên TikTok: video + caption + hashtag
           └── Link affiliate ở bio hoặc comment đầu

5. Schedule
   └── Account Real: 1-2 video/ngày, giờ vàng 19-22h
   └── Account Farm: tương tác với video Real
```

---

## X. Quy tắc coding

1. **Async-first** — asyncio cho I/O
2. **Error handling** — try/except + log, không crash app
3. **Graceful cleanup** — đóng browser khi shutdown
4. **Config-driven** — paths và timing từ `settings.yaml`
5. **Thread safety** — per-account lock, không share browser context giữa coroutines
6. **Video processing** — dùng subprocess ffmpeg, không blocking event loop

---

## XI. Chạy nhanh

```bash
docker compose up -d
pip install -r requirements.txt
playwright install chromium
python src/main.py
```

Dashboard: http://localhost:8000/api/dashboard

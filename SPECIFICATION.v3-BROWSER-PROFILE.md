# TikTok Farm - Browser Profile Scanner v3.0

## Tổng quan
Thay thế TikTokApi (ms_token-based) bằng browser-based hoặc HTTP-based fetch profile.
Không cần ms_token, không cần API key.

### Vấn đề cũ
- TikTokApi (davidteather/TikTok-Api) cần ms_token từ browser cookies
- ms_token hết hạn sau vài giờ
- Datacenter IP bị TikTok block → session creation timeout
- Phụ thuộc vào API internal của TikTok — hay thay đổi, dễ hỏng

### Giải pháp mới — 2 tầng
**Tầng 1 (ưu tiên - Nhanh):** HTTP GET + parse hidden JSON từ SSR HTML
- Không cần browser, không cần proxy (nếu IP tốt)
- ~100ms/request — nhanh gấp 50x browser
- Dùng `<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">` — TikTok SSR data

**Tầng 2 (fallback - Chậm nhưng ổn định):** Playwright browser navigate
- Khi hidden JSON không có (TikTok thay đổi SSR)
- Khi IP bị block request HTTP (cần proxy + browser)
- Khi cần dữ liệu real-time hơn

---

## I. Phân tích từ drawrowfly/tiktok-scraper

### Phát hiện chính

Repo [drawrowfly/tiktok-scraper](https://github.com/drawrowfly/tiktok-scraper) (5k stars, Node.js/TypeScript) đã chứng minh:

1. **Profile data nằm trong SSR HTML** — TikTok render profile data ngay trong page load đầu tiên (Server-Side Rendering)
2. **Selector:** `//script[@id='__UNIVERSAL_DATA_FOR_REHYDRATION__']/text()`
3. **JSON path:** `__DEFAULT_SCOPE__` → `webapp.user-detail` → `userInfo`
4. **Không cần browser** — chỉ cần HTTP request với headers chuẩn
5. **Không cần sign URL** — profile page là public, không cần X-Bogus/X-Gnarly

Vấn đề của tiktok-scraper: 
- `getUserProfileInfo()` dùng endpoint `https://www.tiktok.com/node/share/user/` — endpoint này đã bị TikTok xoá từ 2021
- NHƯNG hidden JSON trong `<script>` tag vẫn hoạt động (được ScrapFly xác nhận 2024)
- Repo chưa được maintain, outdated từ 2023

### Hidden JSON format

```json
{
    "__DEFAULT_SCOPE__": {
        "webapp.user-detail": {
            "userInfo": {
                "user": {
                    "id": "123456789",
                    "uniqueId": "jack_farm1",
                    "nickname": "Jack Farm",
                    "avatarLarger": "https://...",
                    "signature": "My bio",
                    "verified": false,
                    "privateAccount": false,
                    "region": "US"
                },
                "stats": {
                    "followerCount": 1234,
                    "followingCount": 567,
                    "heartCount": 8910,
                    "videoCount": 42,
                    "diggCount": 500
                }
            }
        }
    }
}
```

---

## II. Module: profile_scanner.py (REWRITE — dual strategy)

### Strategy selection

```python
async def fetch_profile(self, username: str, proxy_url: str = None) -> dict:
    """
    2-tầng:
    1. FAST: HTTP GET + parse hidden JSON
    2. FALLBACK: Playwright browser navigate
    
    Returns unified ProfileResult.
    """
```

### Tầng 1: HTTP + Hidden JSON (Fast path)

```python
import httpx

FAST_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.tiktok.com/",
}

async def _fast_profile(self, username: str, proxy_url: str = None) -> dict | None:
    """
    HTTP GET tới https://www.tiktok.com/@{username}
    Parse <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">
    Extract webapp.user-detail → userInfo
    """
    try:
        async with httpx.AsyncClient(headers=FAST_HEADERS, 
                                     proxies=proxy_url, timeout=10) as client:
            resp = await client.get(f"https://www.tiktok.com/@{username}")
            
        if resp.status_code != 200:
            return None  # Fallback to browser
        
        selector = parsel.Selector(resp.text)
        script = selector.xpath("//script[@id='__UNIVERSAL_DATA_FOR_REHYDRATION__']/text()").get()
        
        if not script:
            return None  # Fallback to browser
        
        data = json.loads(script)
        user_info = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
        
        return self._parse_user_info(user_info)
        
    except (KeyError, json.JSONDecodeError, httpx.TimeoutException, httpx.HTTPError) as e:
        log.warning(f"Fast profile scan failed for @{username}: {e}")
        return None  # Fallback to browser
```

### Tầng 2: Playwright Browser (Slow fallback)

```python
async def _browser_profile(self, username: str, proxy_url: str = None) -> dict:
    """
    Fallback khi HTTP path fail.
    Dùng Playwright browser navigate tới profile page.
    Đọc DOM trực tiếp.
    """
    context = await self.browser.create_context(
        proxy=proxy_url if proxy_url else "direct"
    )
    page = await context.new_page()
    
    try:
        await page.goto(f"https://www.tiktok.com/@{username}", 
                        wait_until="networkidle", timeout=15000)
        
        # Thử hidden JSON trước (nếu browser render khác HTTP)
        script = await page.evaluate("""
            () => {
                const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
                return el ? el.textContent : null;
            }
        """)
        
        if script:
            data = json.loads(script)
            user_info = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo", {})
            if user_info:
                return self._parse_user_info(user_info)
        
        # Fallback: DOM scraping
        result = await self._dom_scrape(page, username)
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": self._classify_error(e),
            "username": username,
            "scanned_at": datetime.utcnow().isoformat()
        }
    finally:
        await context.close()
```

### DOM scraping fallback

```python
async def _dom_scrape(self, page, username: str) -> dict:
    """Đọc profile data từ DOM elements."""
    try:
        followers = await page.text_content(".count-infos .count") or "0"
        # Parse: "1.2K" → 1200, "1M" → 1000000
        followers = self._parse_count(followers)
        
        display_name = await page.text_content(".share-title") or username
        bio = await page.text_content(".share-desc") or ""
        
        return {
            "success": True,
            "username": username,
            "display_name": display_name,
            "bio": bio,
            "followers": followers,
            "following": 0,
            "likes": 0,
            "total_posts": 0,
            "verified": False,
            "private_account": False,
            "scanned_at": datetime.utcnow().isoformat(),
            "note": "DOM fallback — limited data"
        }
    except Exception as e:
        # Check for private account
        body = await page.text_content("body")
        if "private" in body.lower():
            return {"success": True, "private_account": True, ...}
        if "couldn't find this account" in body.lower():
            return {"success": False, "error_type": "not_found", ...}
        
        raise
```

### Dependencies

```
httpx>=0.27.0
parsel>=1.9.0    # Giống Scrapy selector — nhẹ, nhanh
```

---

## III. Error handling

### Fast path errors → auto fallback

| Error | Action |
|-------|--------|
| HTTP 403/429 | → Fallback to browser |
| HTTP timeout | → Fallback to browser |
| Hidden JSON key missing | → Fallback to browser |
| JSON decode error | → Fallback to browser |
| Proxy connection error | → Fallback to browser |

### Browser path errors

| Error | error_type | User message |
|-------|-----------|--------------|
| Timeout > 15s | `timeout` | "⏱ Timeout — TikTok không phản hồi. Kiểm tra proxy hoặc thử lại." |
| 403 / block | `blocked` | "🚫 TikTok chặn request từ IP này. Thử đổi proxy." |
| Not found | `not_found` | "❌ Account @{username} không tồn tại hoặc đã bị banned." |
| Private | `private` | "🔒 Account @{username} đang ở chế độ riêng tư." |
| Cloudflare | `captcha` | "🤖 TikTok yêu cầu captcha. Thử lại sau 30 giây." |

---

## IV. Parse utilities

```python
@staticmethod
def _parse_count(text: str) -> int:
    """Parse TikTok count format: '1.2K' → 1200, '1M' → 1000000"""
    text = text.strip().replace(",", "")
    multipliers = {"K": 1000, "M": 1000000, "B": 1000000000}
    suffix = text[-1].upper() if text else ""
    if suffix in multipliers:
        return int(float(text[:-1]) * multipliers[suffix])
    return int(text) if text.isdigit() else 0

@staticmethod
def _parse_user_info(user_info: dict) -> dict:
    """Parse unified userInfo JSON → ProfileResult dict"""
    user = user_info.get("user", {})
    stats = user_info.get("stats", {})
    return {
        "success": True,
        "username": user.get("uniqueId", ""),
        "display_name": user.get("nickname", ""),
        "bio": user.get("signature", ""),
        "avatar_url": user.get("avatarLarger", ""),
        "followers": stats.get("followerCount", 0),
        "following": stats.get("followingCount", 0),
        "likes": stats.get("heartCount", 0),  # TikTok "hearts" = total likes
        "total_posts": stats.get("videoCount", 0),
        "verified": user.get("verified", False),
        "private_account": user.get("privateAccount", False),
        "scanned_at": datetime.utcnow().isoformat(),
    }

@staticmethod
def _classify_error(e: Exception) -> str:
    err_str = str(e).lower()
    if "timeout" in err_str:
        return "timeout"
    if "403" in err_str or "block" in err_str:
        return "blocked"
    if "not found" in err_str or "404" in err_str:
        return "not_found"
    if "private" in err_str:
        return "private"
    return "unknown"
```

---

## V. Changes to existing modules

### 1. database migration
- Xoá `ms_token` khỏi config/settings nếu có.
- Không thay đổi DB schema — profile scanner không cần lưu token.

### 2. web/api.py — New endpoints

**Replace old TikTokApi endpoints:**

```
GET /api/settings/tiktok-api/status  →  REMOVED
POST /api/settings/tiktok-api/token  →  REMOVED
POST /api/settings/tiktok-api/test   →  REMOVED
```

**New endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/profile/scan/{username}` | Scan single profile (auto-detect best path) |
| POST | `/api/profile/scan/{username}/proxy/{proxy_id}` | Scan with specific proxy |
| GET | `/api/profile/status` | Check scanner availability |

### 3. web/api.py — Existing sync endpoints (updated)

`POST /api/accounts/{id}/sync-profile` — gọi ProfileScanner:

```python
async def sync_account_profile(account_id):
    account = account_manager.get_account(account_id)
    if not account:
        return {"success": False, "error": "Account not found"}
    
    scanner = ProfileScanner(browser_manager)
    result = await scanner.fetch_profile(
        account["username"],
        proxy_url=_get_proxy_url(account.get("proxy_id"))
    )
    
    if result["success"]:
        account_manager.update_account(account_id, {
            "followers": result["followers"],
            "following": result["following"],
            "total_posts": result["total_posts"],
            "total_views": result["likes"],
            "status": "active",
        })
    
    return result
```

### 4. web/templates/index.html — Settings tab

```
Before: ms_token input + Save/Test buttons + status indicator
After:  Profile Scanner status card
┌─────────────────────────────────────────────┐
│  Profile Scanner                             │
│                                              │
│  Status: ✅ Ready (Fast HTTP + Browser)     │
│                                              │
│  No configuration needed.                    │
│  Dual-mode: HTTP (~100ms) or Browser (~5s)   │
│                                              │
│  [Test Scan @tiktok]                         │
│  (scans TikTok's official account to test)   │
└─────────────────────────────────────────────┘
```

### 5. web/static/js/dashboard.js
- Xoá settings tab JavaScript cũ (ms_token form)
- Thêm status card (GET /api/profile/status)

### 6. src/main.py
- Xoá `tiktok_profile.py` import và khởi tạo TikTokApi
- Xoá `/api/settings/tiktok-api/*` routes

### 7. requirements.txt
- Thêm `httpx>=0.27.0`
- Thêm `parsel>=1.9.0`

---

## VI. Test Plan (local)

1. **Fast path test**: scan `@tiktok`, `@nike`, `@jack` → verify followers/likes match
2. **Fallback test**: scan khi hidden JSON không có (giả lập bằng cách xoá script tag)
3. **Browser test**: scan với proxy chậm → verify fallback to browser
4. **Error test**: scan account không tồn tại, private account
5. **Performance**: 10 profile scans → avg time < 500ms (fast path), < 10s (browser)

---

## VII. Pitfalls

- **Hidden JSON key có thể thay đổi** — TikTok có thể đổi `webapp.user-detail` thành key khác. Cần log + fallback ngay.
- **httpx proxy format** — `http://user:pass@host:port` (không phải `socks5://` vì httpx không support SOCKS mặc định)
- **Rate limit HTTP** — TikTok rate limit ~10 request/phút/IP cho HTTP GET. Nếu scan 100 accounts, dùng proxy rotation.
- **parsel vs BeautifulSoup** — parsel nhanh hơn 3x và nhẹ hơn. Dùng parsel.
- **Không cần asyncio.gather cho fast path** — httpx async client tự handle connection pooling.

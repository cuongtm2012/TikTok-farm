# TikTok Farm - Post Engine Upgrade v4.0

## Tổng quan
Cải tiến Post Engine dựa trên selectors + flow từ [wkaisertexas/tiktok-uploader](https://github.com/wkaisertexas/tiktok-uploader).
Kế thừa cookie auth, upload selectors, và schedule logic — thay vì tự reverse engineer TikTok upload page.

---

## I. Kế thừa từ tiktok-uploader

### Những gì kế thừa được

| Component | Mức độ | Cách dùng |
|-----------|--------|-----------|
| **Cookie auth (auth.py)** | ✅ Full | Parse cookies từ file/string/list → inject Playwright |
| **Upload selectors (config.toml)** | ✅ Full | XPATH cho upload page, cover, schedule |
| **Upload flow (upload.py)** | ✅ Pattern | Set video → cover → caption → interactivity → post |
| **Anti-detection (browsers.py)** | ✅ Partial | `--disable-blink-features=AutomationControlled`, init script |
| **Schedule validation** | ✅ Full | Min 20 phút, max 10 ngày, minute % 5 |
| **Cookie format (types.py)** | ✅ Full | Cookie dict với name/value/domain/path/expiry/sameSite |

### Những gì KHÔNG dùng

| Component | Lý do |
|-----------|-------|
| **sync_playwright** (sync API) | Project dùng asyncio — sync sẽ block event loop |
| **CLI** | Không cần CLI, chỉ API + Dashboard |
| **Pydantic models** | Overkill, dùng dict đơn giản hơn |
| **Login bằng user/pass** | Không khả thi — TikTok anti-bot mạnh, captcha |
| **Bulk upload logic** | Project có Post Manager riêng rồi |

---

## II. Cookie Manager Module (NEW — src/cookie_manager.py)

Kế thừa từ `auth.py` của tiktok-uploader.

### Class CookieManager

```python
class CookieManager:
    """
    Quản lý cookies cho TikTok accounts.
    Hỗ trợ nhiều format: cookies.txt, cookies_list, sessionid, cookie_string.
    """

    @staticmethod
    def parse_cookie_line(line: str) -> list[dict]:
        """Parse pipe-delimited account line → Playwright cookies.
        Input: "username|pass|email|email_pass|sessionid=abc;tt_chain=xyz|uid"
        Output: [{"name":"sessionid","value":"abc","domain":".tiktok.com","path":"/","expires":...}, ...]
        """

    @staticmethod
    def parse_netscape_file(path: str) -> list[dict]:
        """Parse cookies.txt Netscape format → Playwright cookies dict."""

    @staticmethod
    def parse_cookie_string(raw: str) -> list[dict]:
        """Parse 'name=value; name=value' string → Playwright cookies."""

    @staticmethod
    def to_playwright_format(cookies: list[dict]) -> list[dict]:
        """Fix key names: expiry→expires, remove invalid sameSite."""

    @staticmethod
    def save_to_storage_state(cookies: list[dict], output_path: str):
        """Write cookies as Playwright storage_state JSON."""

    @staticmethod
    def from_storage_state(path: str) -> list[dict]:
        """Read Playwright storage_state → cookies list."""
```

### Cookie format normalization

```python
# Input formats → unified Playwright cookies list
FORMATS = {
    "netscape_file": "domain\\tflag\\tpath\\tsecure\\texpiry\\tname\\tvalue",
    "cookie_string": "sessionid=abc123; tt_chain_token=xyz; ...",
    "cookie_list": [{"name": "sessionid", "value": "abc", ...}],
    "sessionid": "abc123...",  # single sessionid string
}

# Output (Playwright-compatible):
[
    {
        "name": "sessionid",
        "value": "abc123",
        "domain": ".tiktok.com",
        "path": "/",
        "expires": 2147483647,
        "sameSite": "Lax",   # hoặc bỏ nếu invalid value
        "httpOnly": True,
        "secure": True,
    }
]
```

### Cookie validation

```python
@staticmethod
def validate_session(cookies: list[dict]) -> bool:
    """Check if sessionid cookie exists and is valid."""
    return any(c.get("name") == "sessionid" for c in cookies)

@staticmethod
def get_session_id(cookies: list[dict]) -> str | None:
    """Extract sessionid value."""
```

---

## III. Post Engine Rewrite (src/post_engine.py)

### Lớp PostEngine mới

```python
class PostEngine:
    """
    Upload TikTok posts using Playwright.
    Kế thừa selectors + flow từ tiktok-uploader.
    """

    def __init__(self, browser_manager, cookie_manager=None):
        self.browser = browser_manager
        self.cookie_mgr = cookie_manager or CookieManager()

    async def upload_video(
        self,
        account: dict,
        media_path: str,
        caption: str = "",
        hashtags: str = "",
        schedule_dt: datetime | None = None,
        cover_path: str | None = None,
        product_id: str | None = None,
    ) -> dict:
        """
        Upload video/slideshow lên TikTok.
        
        1. Tạo browser context + inject cookies
        2. Navigate tới upload page
        3. Upload media
        4. Set caption + hashtags
        5. Set interactivity (comment/stitch/duet)
        6. Set cover (optional)
        7. Set schedule (optional)
        8. Post
        9. Verify success
        10. Cleanup
        """
```

### Upload flow (chi tiết)

```
async def upload_video(...):
    ┌─────────────────────────────────────────────┐
    │  1. CREATE BROWSER CONTEXT                   │
    │     - browser_manager.create_context(         │
    │         account_id, proxy, cookies)          │
    │     - inject cookies từ CookieManager        │
    │     - add_init_script (anti-detection)       │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  2. NAVIGATE TO UPLOAD PAGE                  │
    │     - page.goto(config.upload_url)           │
    │     - wait: creator center loaded            │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  3. SELECT FILE (slideshow hoặc video)       │
    │     - page.set_input_files(selector, path)   │
    │     - Nếu slideshow: chọn nhiều files        │
    │     - wait: upload complete (200s timeout)    │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  4. REMOVE SPLIT WINDOW (nếu có)             │
    │     - Click "Not now" nếu popup xuất hiện    │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  5. SET CAPTION + HASHTAGS                   │
    │     - Fill vào div[contenteditable=true]     │
    │     - Hashtag phải cách nhau bằng space      │
    │     - Xoá BMP-out chars (emojis...)          │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  6. SET INTERACTIVITY                        │
    │     - Comment: ON (default)                  │
    │     - Stitch: ON                             │
    │     - Duet: ON                               │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  7. SET COVER (optional)                     │
    │     - Click "Edit cover"                     │
    │     - Upload tab → chọn file                 │
    │     - Confirm                                │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  8. SET SCHEDULE (optional)                  │
    │     - Bật schedule switch                   │
    │     - Chọn date từ calendar                  │
    │     - Chọn time (h + min, multiple of 5)    │
    │     - Validate: 20min ahead, max 10 days     │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  9. POST                                     │
    │     - Click "Post" button                    │
    │     - Wait for confirmation message          │
    │     - Lấy post URL nếu có                    │
    └─────────────────┬───────────────────────────┘
                      ▼
    ┌─────────────────────────────────────────────┐
    │  10. CLEANUP + RESULT                        │
    │     - Save cookies mới (refreshed)          │
    │     - Close context                          │
    │     - Return {success, post_url, error}      │
    └─────────────────────────────────────────────┘
```

### Upload selectors (từ tiktok-uploader config.toml)

```python
UPLOAD_SELECTORS = {
    "upload_page": "https://www.tiktok.com/creator-center/upload?lang=en",
    "file_input": "//input[@type='file']",
    "upload_finished": "//div[contains(@class, 'btn-cancel')]",
    "split_window": "//button[./div[text()='Not now']]",
    "description": "//div[@contenteditable='true']",
    "mention_box": "//div[contains(@class, 'mention-list-popover')]",
    "comment_toggle": "//label[.='Comment']/following-sibling::div/input",
    "duet_toggle": "//label[.='Duet']/following-sibling::div/input",
    "stitch_toggle": "//label[.='Stitch']/following-sibling::div/input",
    "post_button": "//button[@data-e2e='post_video_button']",
    "post_confirmation": "//div[contains(text(), 'Your video has been uploaded') or contains(text(), 'Video published')]",
}
```

### Cover upload selectors

```python
COVER_SELECTORS = {
    "edit_cover_button": "//div[contains(@class, 'edit-container')]",
    "upload_cover_tab": "//div[contains(text(), 'Upload cover')]",
    "upload_cover_input": "//input[@type='file' and @accept='image/png, image/jpeg, image/jpg']",
    "confirm_cover": "//div[not(contains(@class, 'hide-panel'))]/div[contains(@class, 'cover-edit-footer')]/button[contains(@class, 'TUXButton--primary')]",
}
```

### Schedule selectors

```python
SCHEDULE_SELECTORS = {
    "switch": "//*[@id='tux-1']",
    "date_picker": "//div[contains(@class, 'date-picker-input')]",
    "calendar_month": "//span[contains(@class, 'month-title')]",
    "calendar_valid_days": "//div[@class='jsx-4172176419 days-wrapper']//span[contains(@class, 'day') and contains(@class, 'valid')]",
    "calendar_arrows": "//span[contains(@class, 'arrow')]",  # first=prev, second=next
    "time_picker": "//div[contains(@class, 'time-picker-input')]",
    "timepicker_hours": "//span[contains(@class, 'tiktok-timepicker-left')]",
    "timepicker_minutes": "//span[contains(@class, 'tiktok-timepicker-right')]",
}
```

---

## IV. Anti-detection improvements

Từ `browsers.py` của tiktok-uploader + cải tiến riêng:

```python
def setup_anti_detection(context):
    """Stealth config cho Playwright context."""
    # 1. Mask webdriver
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)

    # 2. Mask Chrome automation flags
    context.add_init_script("""
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """)

    # 3. Launch args
    # --disable-blink-features=AutomationControlled
    # --no-sandbox (trong container)
```

### Browser launch args

```python
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",  # Docker
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
    "--lang=en-US",
]
```

---

## V. Schedule validation

Từ `upload.py` của tiktok-uploader:

```python
def validate_schedule(dt: datetime) -> tuple[bool, str]:
    """
    Validate thời gian schedule cho TikTok.
    Returns: (is_valid, error_message)
    """
    now = datetime.utcnow()
    
    # Min 20 phút (15 + 5 margin)
    if dt < now + timedelta(minutes=20):
        return False, "Schedule must be at least 20 minutes in the future"
    
    # Max 10 ngày
    if dt > now + timedelta(days=10):
        return False, "Schedule cannot be more than 10 days in advance"
    
    # Minute must be multiple of 5
    if dt.minute % 5 != 0:
        return False, "Schedule minute must be a multiple of 5"
    
    return True, ""
```

---

## VI. Slideshow upload (improvement)

TikTok upload page hỗ trợ multiple files cho slideshow. Farm posts chủ yếu là slideshow:

```python
async def _upload_slideshow(self, page, media_dir: str):
    """Upload multiple images as slideshow."""
    image_files = sorted(Path(media_dir).glob("*.{png,jpg,jpeg}"))
    file_paths = [str(f.absolute()) for f in image_files]
    
    # Playwright set_input_files supports multiple files natively
    await page.set_input_files(UPLOAD_SELECTORS["file_input"], file_paths)
    
    # Wait for processing to complete
    await page.wait_for_selector(UPLOAD_SELECTORS["upload_finished"],
                                  timeout=UPLOAD_TIMEOUT)
```

---

## VII. Error handling

```python
class PostError(Exception):
    """Base class for post engine errors."""

class CookieExpiredError(PostError):
    """Cookies expired — need re-login."""

class UploadTimeoutError(PostError):
    """Media upload timed out (slow proxy)."""

class PostRejectedError(PostError):
    """TikTok rejected the post (rate limit, content violation)."""

class ScheduleInvalidError(PostError):
    """Schedule time invalid."""
```

### Retry logic

```python
async def upload_video(self, ..., max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            result = await self._do_upload(...)
            return result
        except CookieExpiredError:
            raise  # Không retry — cần cookies mới
        except UploadTimeoutError:
            if attempt < max_retries:
                await asyncio.sleep(10 * (attempt + 1))  # backoff
                continue
            return {"success": False, "error": "Upload timeout after retries", "post_url": None}
        except PostRejectedError as e:
            return {"success": False, "error": str(e), "post_url": None}
```

---

## VIII. API Endpoints (updated)

### POST /api/posts/{id}/publish — publish engine integration

```python
async def publish_post(post_id: int):
    """
    Gọi PostEngine.upload_video() để publish.
    Flow:
    1. Get post + account từ DB
    2. Load account cookies
    3. PostEngine.upload_video(account, media_path, caption, schedule)
    4. Update post status (posted/failed)
    5. Save post URL nếu success
    6. Gửi alert nếu fail
    """
```

### POST /api/posts — create draft (updated)

```python
async def create_post(account_id: int, caption: str, hashtags: str,
                      schedule_at: str = None, media_files: list = None):
    """
    Validate schedule time trước khi save:
    - schedule_at >= now + 20 phút
    - schedule_at <= now + 10 ngày
    - minute % 5 == 0
    """
```

---

## IX. Files changed

| File | Action | Description |
|------|--------|-------------|
| `src/cookie_manager.py` | **NEW** | Parse cookies từ nhiều format khác nhau |
| `src/post_engine.py` | **REWRITE** | Upload engine mới với selectors từ tiktok-uploader |
| `web/api.py` | **MODIFY** | Publish endpoint + schedule validation |
| `src/main.py` | **MODIFY** | Register CookieManager, update import |
| `config/selectors.yaml` | **NEW** | Upload/cover/schedule selectors (tách khỏi code) |
| `src/browser_manager.py` | **MODIFY** | Thêm anti-detection init script |

---

## X. Implementation Plan

### P0 — CookieManager + Selectors
1. `src/cookie_manager.py` — parse, normalize, validate cookies
2. `config/selectors.yaml` — extract upload/cover/schedule selectors từ tiktok-uploader
3. Test parse cookies từ nhiều format

### P1 — Post Engine rewrite
4. `src/post_engine.py` — viết lại với selectors mới
5. Upload flow: file → caption → interactivity → cover → schedule → post
6. Retry logic + error handling
7. Test upload video thật (local)

### P2 — Integration
8. API publish endpoint → gọi PostEngine
9. Schedule validation (20min/10days/5min)
10. Cookie manager tích hợp vào account import flow

### P3 — Anti-detection
11. Thêm init script cho browser context
12. Test với headless mode
13. Test với VPS IP

---

## XI. Pitfalls

- **Selectors thay đổi** — TikTok thường xuyên update UI. Cần theo dõi và cập nhật `config/selectors.yaml`.
- **Headless dễ bị detect** — TikTok phát hiện headless browser nhanh hơn headed. Nên dùng headed mode hoặc stealth patches.
- **Cookies hết hạn nhanh** — TikTok sessionid thường live vài ngày. Cần refresh cookies trước mỗi upload.
- **Rate limit upload** — TikTok giới hạn ~3-5 upload/ngày/account. Farm nên phân bố đều.
- **Video processing timeout** — Video > 5 phút có thể timeout upload. Nên giới hạn video dưới 3 phút cho farm.
- **slideshow không support schedule** — TikTok UI cho slideshow có thể không có schedule option (chỉ video mới có). Cần detect và fallback.
- **Cookie format khác nhau** — Seller bán account thường cho cookies string `key=value;key2=value2`. CookieManager phải handle format này.

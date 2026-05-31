# TikTok Farm v2.1 — SPECIFICATION

## Overview

Upgrade TikTok Farm system với 2 tính năng chính:
1. **Parser import account theo định dạng seller** — cho phép paste trực tiếp list account từ seller (USER|PASS|EMAIL|EMAIL_PASS|COOKIES|UID)
2. **Quản lý cookie gắn liền với account** — cookie là field bắt buộc, auto parse + lưu storage_state

---

## P1 — Seller Format Account Parser

### Vấn đề hiện tại
- `POST /api/accounts?username=xxx&proxy_id=N` — chỉ nhận username, không có cookies
- Không có cách import account kèm cookies
- Định dạng seller: `USER|PASS|EMAIL|EMAIL_PASS|COOKIES_STRING|UID`

### Giải pháp

#### 1. API endpoint mới: `POST /api/accounts/import/seller`

```
POST /api/accounts/import/seller
Body: { "accounts": "USER|PASS|EMAIL|EMAIL_PASS|COOKIES|UID\nUSER2|...", "proxy_id": 1 }
```

**Parser logic:**
```python
def parse_seller_line(line: str) -> dict:
    parts = line.strip().split("|")
    if len(parts) < 2:
        raise ValueError("Invalid format")
    username = parts[0]
    password = parts[1]
    email = parts[2] if len(parts) > 2 else ""
    email_pass = parts[3] if len(parts) > 3 else ""
    cookie_str = parts[4] if len(parts) > 4 else ""
    uid = parts[5] if len(parts) > 5 else ""

    # Parse cookie string → JSON array
    cookie_data = parse_cookie_string(cookie_str) if cookie_str else ""

    return {
        "username": username,
        "password": password,
        "email": email,
        "email_password": email_pass,
        "cookie_data": json.dumps(cookie_data),
        "uid": uid,
    }

def parse_cookie_string(cookie_str: str) -> list:
    """'name=value; name=value' → [{'name': 'x', 'value': 'y', 'domain': '.tiktok.com', 'path': '/'}, ...]"""
    cookies = []
    for part in cookie_str.split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".tiktok.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            })
    return cookies
```

#### 2. Sửa `AccountCreateBody` — thêm field cookie_data

```python
class AccountCreateBody(BaseModel):
    username: str
    proxy_id: int = 0
    password: str = ""
    cookie_data: str = ""       # <-- THÊM
    email: str = ""             # <-- THÊM
    email_password: str = ""    # <-- THÊM
    notes: str = ""
    status: Optional[str] = None
```

#### 3. Sửa `_create_account_from_body` — auto parse + save cookies

```python
def _create_account_from_body(state, body):
    account = state.account_manager.add_account(
        username=body.username.strip(),
        proxy_id=body.proxy_id,
        notes=body.notes,
        password=body.password,
    )
    # Auto-save cookies nếu có
    if body.cookie_data:
        try:
            cookies = json.loads(body.cookie_data) if isinstance(body.cookie_data, str) else body.cookie_data
            if isinstance(cookies, list) and len(cookies) > 0:
                state.account_manager.save_cookies(account.id, cookies)
                # Create storage_state file
                _create_storage_state(account.id, cookies)
        except Exception as e:
            logger.warning(f"Failed to save cookies for acc {account.id}: {e}")
    # Save email info vào notes
    if body.email:
        extra = f"email={body.email}"
        if body.email_password:
            extra += f"|email_pass={body.email_password[:4]}..."
        old_notes = account.notes or ""
        state.account_manager.update_account(account.id, notes=f"{old_notes}; {extra}" if old_notes else extra)
    ...
```

#### 4. `_create_storage_state` — helper

```python
def _create_storage_state(account_id: int, cookies: list):
    """Tạo Playwright storage_state.json từ cookies list."""
    import json, os
    from pathlib import Path
    storage_dir = Path(f"profiles/{account_id}")
    storage_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "cookies": cookies,
        "origins": []
    }
    with open(storage_dir / "storage_state.json", "w") as f:
        json.dump(state, f)
    logger.info(f"Created storage_state for account {account_id} ({len(cookies)} cookies)")
```

---

## P2 — Cookie Management Integrated

### Vấn đề hiện tại
- cookie_data có trong DB schema nhưng không được hiển thị/management qua API
- Account response không bao gồm cookie status (có/không/số lượng)
- Không có endpoint để lấy/xoá/cập nhật cookies riêng

### Giải pháp

#### 1. Account response — thêm cookie info

```python
class Account:
    def to_dict(self):
        cookie_count = 0
        has_cookies = False
        cookie_expired = False
        if self.cookie_data:
            try:
                cookies = json.loads(self.cookie_data)
                if isinstance(cookies, list):
                    cookie_count = len(cookies)
                    has_cookies = True
                    # Check if sessionid exists (critical cookie)
                    has_session = any(c.get("name") == "sessionid" for c in cookies)
                    if not has_session:
                        cookie_expired = True
            except Exception:
                pass

        return {
            "id": self.id,
            "username": self.username,
            ...
            "cookie_status": {
                "has_cookies": has_cookies,
                "cookie_count": cookie_count,
                "expired": cookie_expired,
            }
        }
```

#### 2. API endpoint: `PATCH /api/accounts/{id}/cookies`

```
PATCH /api/accounts/1/cookies
Body: { "cookie_data": "name=value; name=value; ..." }
Hoặc: { "cookie_data": [{"name": "...", "value": "..."}, ...] }
```

Tự động:
- Parse cookie string → JSON array (nếu là string)
- Lưu vào DB (cookie_data field)
- Tạo/tái tạo storage_state.json

#### 3. API endpoint: `GET /api/accounts/{id}/cookies` (admin)

Trả về cookies list để debug (chỉ available khi debug=true trong config).

#### 4. API endpoint: `DELETE /api/accounts/{id}/cookies`

Xoá cookie_data + storage_state.

#### 5. Dashboard UI — Account detail card

Thêm badge/hiển thị:
- 🍪 `Cookies: 33 (OK)` — xanh nếu có sessionid
- 🍪 `Cookies: 0` — đỏ nếu chưa có
- Nút "Paste cookies" mở modal textarea

#### 6. Dashboard UI — Bulk import form

Tab Accounts có thêm section:
```
📥 Import từ Seller Format

Paste list accounts (USER|PASS|EMAIL|EMAIL_PASS|COOKIES|UID):

<textarea rows="10" cols="80">
user1|pass1|email1@hm.com|passmail1|cookie1...|uid1
user2|pass2|email2@hm.com|passmail2|cookie2...|uid2
</textarea>

[Proxy mặc định: ▼ Select proxy] [Import Accounts]
```

---

## P3 — API Changes Summary

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/api/accounts` | POST | ✅ UPDATE | Thêm `cookie_data`, `email`, `email_password` fields |
| `/api/accounts/import/seller` | POST | 🆕 NEW | Import seller format (USER\|PASS\|...) |
| `/api/accounts/:id` | GET | ✅ UPDATE | Response thêm `cookie_status` |
| `/api/accounts/:id/cookies` | PATCH | 🆕 NEW | Update cookies cho 1 account |
| `/api/accounts/:id/cookies` | GET | 🆕 NEW | Get cookies (debug mode) |
| `/api/accounts/:id/cookies` | DELETE | 🆕 NEW | Xoá cookies + storage_state |
| `/api/accounts/import` | POST | ✅ UPDATE | Thêm cookie_data field trong normalize |

---

## P4 — Dashboard UI Changes

### Account Row — thêm cột "Cookies"

| Username | Proxy | Status | Cookies | Followers | Actions |
|----------|-------|--------|---------|-----------|---------|
| user1 | 1:42.96.x | warming | 🍪 33 | 0 | [Farm] [Post]... |

### Account Detail Modal

Khi click vào account, mở modal với:
- Thông tin account
- **Section: Cookies** — số lượng, có sessionid không, expired?
- Nút "Update Cookies" → paste cookie string
- Nút "Delete Cookies" → clear

### Bulk Import Section

Trên đầu tab Accounts:
```
┌──────────────────────────────────────────┐
│ 📥 Fast Import (Seller Format)           │
│                                          │
│ Paste accounts:                          │
│ ┌──────────────────────────────────────┐ │
│ │ user1\|pass1\|...                    │ │
│ │ user2\|pass2\|...                    │ │
│ └──────────────────────────────────────┘ │
│ Proxy: [▼ Select proxy or auto]          │
│ [Import]                                 │
└──────────────────────────────────────────┘
```

---

## P5 — Cookie Lifecycle

```
Tạo account (API/Import)
    │
    ▼
Có cookie_data? ──yes──→ Parse cookies string → JSON array
    │                          │
    no                         ▼
    │                   Lưu vào DB (cookie_data)
    │                   Tạo storage_state.json
    ▼                   Cập nhật cookie_status
Tạo account trống
    │
    ▼
Farm session chạy ──→ Gọi save_cookies() → update DB + storage_state
Post bài chạy ─────→ _persist_session() → update DB + storage_state
Health check ──────→ Inject cookies từ DB → check login pass
```

---

## P6 — Priority & Timeline

| # | Feature | File(s) | Priority |
|---|---------|---------|----------|
| 1 | `AccountCreateBody` thêm `cookie_data`, `email`, `email_password` | `web/api.py:25-31` | P0 |
| 2 | `_create_account_from_body` auto-save cookies + storage_state | `web/api.py:129-141` | P0 |
| 3 | `parse_seller_format` + `POST /api/accounts/import/seller` | `src/import_utils.py`, `web/api.py` | P0 |
| 4 | Account `to_dict()` thêm `cookie_status` | `src/account_manager.py` | P1 |
| 5 | `PATCH/GET/DELETE /api/accounts/:id/cookies` | `web/api.py` | P1 |
| 6 | Dashboard UI — cookie badge + bulk import form | `web/templates/index.html`, `web/static/js/dashboard.js` | P2 |
| 7 | Dashboard UI — Account detail modal with cookie management | `web/templates/index.html`, `web/static/js/dashboard.js` | P2 |

---

## Files to Modify

| File | Changes |
|------|---------|
| `web/api.py` | AccountCreateBody (thêm fields), _create_account_from_body (auto cookies), import/seller endpoint, cookies CRUD endpoints |
| `src/import_utils.py` | Thêm parse_seller_line(), parse_cookie_string(), cập nhật normalize_account_row() |
| `src/account_manager.py` | Account.to_dict() thêm cookie_status, save_cookies_from_string() |
| `src/browser_manager.py` | Thêm create_storage_state(account_id, cookies) method |
| `web/templates/index.html` | Thêm bulk import section, cookie badge |
| `web/static/js/dashboard.js` | Thêm cookie management functions |
| `SPECIFICATION.md` | Update |

---

## Implementation status (v2.1)

| # | Feature | Status |
|---|---------|--------|
| 1 | `AccountCreateBody` + cookie/email fields | ✅ |
| 2 | `_create_account_from_body` auto cookies + storage_state | ✅ |
| 3 | `POST /api/accounts/import/seller` | ✅ |
| 4 | `cookie_status` in `Account.to_dict()` | ✅ |
| 5 | `PATCH/GET/DELETE /api/accounts/:id/cookies` | ✅ |
| 6 | Dashboard cookie badge + seller import panel | ✅ |
| 7 | Account detail modal + cookie management | ✅ |
| 8 | Cookie required on seller import (`require_cookies_on_seller_import`) | ✅ |
| 9 | Auto-assign proxy + proxy dropdown in UI | ✅ |
| 10 | P5 lifecycle: farm/health refresh cookies + storage_state | ✅ |
| 11 | Parser supports `\|` inside cookie field (6+ fields) | ✅ |

---

## P7 — Config (`settings.yaml`)

```yaml
accounts:
  require_cookies_on_seller_import: true
  status_with_cookies: warming   # auto status after import with cookies
```

Seller import body:
```json
{
  "accounts": "USER|PASS|...",
  "proxy_id": 0,
  "skip_existing": true,
  "require_cookies": true,
  "auto_assign_proxy": true
}
```

---

## Appendix — v2.0 (Affiliate pipeline)

Still supported in codebase (`src/affiliate/`, `PostEngine.upload_video`, Real vs Farm scheduler).
See git history / `SPECIFICATION` v2.0 commit `2214e34` for full affiliate checklist.

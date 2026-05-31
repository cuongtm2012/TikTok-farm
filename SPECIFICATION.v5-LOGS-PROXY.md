# TikTok Farm — Real-time Logs & Proxy Management v5.0

## Tổng quan
Hai cải tiến chính:
1. **Real-time logs cho từng account** — không chỉ farm session mà còn health check, sync profile, post, error events
2. **Proxy management đúng** — chỉ 2 proxy (4 endpoints) active, xoá rác, health check thật

---

## I. Vấn đề hiện tại

### 1. Real-time logs

**SPEC.WS.md đã có WebSocket cho farm session** nhưng mới chỉ cho Farm. Thiếu:
- Logs không persist — khi reload dashboard mất hết
- Chỉ có farm, thiếu health check logs, sync profile logs
- Không có log history cho từng account riêng
- Không thể xem logs của account A mà không click Farm

### 2. Proxy management

Trong DB có **6 proxies** nhưng thực tế chỉ **4 proxy thật** (2 server IP):
```
DB: 6 rows (3,4,5,6,7,8)
CSV: 4 dòng (2 server IP x http/socks5)
```

**2 proxy cũ (ID=3, 42.96.10.203:8241 và ID=4, 103.57.128.248:8768) là rác** — còn trong DB nhưng không có trong CSV, không ai dùng (Account 1 đang trỏ vào proxy ID=3 cũ).

**Proxies trong DB phải sync đúng với proxies.csv** — không thừa, không thiếu.

---

## II. Module: Log Manager (src/log_manager.py) — NEW

### Kiến trúc

```
┌──────────────────────────────────────────────────────────┐
│                    LogManager                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ In-memory   │  │ SQLite      │  │ WebSocket        │ │
│  │ Buffer      │  │ Persist     │  │ Broadcast        │ │
│  │ (ring, 500) │  │ (logs table)│  │ (live push)      │ │
│  └─────────────┘  └──────────────┘  └──────────────────┘ │
└──────────────────────────────────────────────────────────┘
         ↑             ↑              ↑
    FarmEngine    HealthMonitor   ProfileScanner
    PostEngine    Scheduler       Any module
```

### Lớp LogManager

```python
class LogManager:
    """Central log collector — persist + broadcast logs."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ring: list[dict] = []  # In-memory 500 entries
        self._ws_clients: dict[int, set[WebSocket]] = {}  # account_id → {ws}
        self._ensure_table()

    def _ensure_table(self):
        """Create logs table if not exists."""
        CREATE TABLE IF NOT EXISTS account_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            log_type TEXT NOT NULL,
                -- 'farm', 'health', 'sync', 'post', 'system', 'error'
            level TEXT DEFAULT 'INFO',
                -- 'INFO', 'WARNING', 'ERROR', 'SUCCESS'
            message TEXT NOT NULL,
            details TEXT,           -- JSON — extra data (stats, error details)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX idx_logs_account ON account_logs(account_id, created_at);

    def log(self, account_id: int, log_type: str, level: str, message: str, details: dict = None):
        """Ghi log + broadcast WebSocket + persist SQLite."""
        entry = {
            "account_id": account_id,
            "log_type": log_type,
            "level": level,
            "message": message,
            "details": details or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        # 1. Persist to SQLite
        self._insert_db(entry)
        # 2. Add to ring buffer
        self._ring.append(entry)
        if len(self._ring) > 500:
            self._ring.pop(0)
        # 3. Broadcast to WebSocket clients
        asyncio.ensure_future(self._broadcast(account_id, entry))

    def get_logs(self, account_id: int = None, limit: int = 50,
                 log_type: str = None, level: str = None) -> list[dict]:
        """Query logs — filter by account, type, level."""
        query = "SELECT * FROM account_logs WHERE 1=1"
        params = []
        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)
        if log_type:
            query += " AND log_type = ?"
            params.append(log_type)
        if level:
            query += " AND level = ?"
            params.append(level)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        # ... execute SQL

    def get_recent(self, account_id: int = None, limit: int = 20) -> list[dict]:
        """Get recent logs (from ring buffer if available, fallback DB)."""
        if not account_id:
            return self._ring[-limit:]
        return [e for e in self._ring if e["account_id"] == account_id][-limit:]

    async def subscribe(self, account_id: int, ws: WebSocket):
        """Register WebSocket client for real-time push."""
        if account_id not in self._ws_clients:
            self._ws_clients[account_id] = set()
        self._ws_clients[account_id].add(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive ping/pong
        except Exception:
            pass
        finally:
            self._ws_clients[account_id].discard(ws)

    async def _broadcast(self, account_id: int, entry: dict):
        """Push log entry to all subscribed clients."""
        clients = self._ws_clients.get(account_id, set())
        dead = set()
        for ws in clients:
            try:
                await ws.send_json({"type": "log", "data": entry})
            except Exception:
                dead.add(ws)
        clients -= dead
```

### Event Types

| log_type | Khi nào ghi | Ví dụ message |
|----------|-------------|---------------|
| `farm` | Farm session start/action/complete | "Farm session started (15 min)", "Scrolled 42x", "Liked 3 videos" |
| `health` | Health check chạy | "Health check: login OK", "Health check: NOT LOGGED IN" |
| `sync` | Sync profile | "Profile scanned: 1,234 followers, 8,910 likes" |
| `post` | Post engine | "Upload started", "Post published: 2,340 views" |
| `system` | System events | "Browser reconnected", "Scheduler job ran" |
| `error` | Lỗi nghiêm trọng | "Browser crash, reconnecting...", "Proxy timeout after 30s" |

---

## III. Dashboard UI — Account Logs Panel

### Mỗi account row trong Accounts tab có nút mới

```
┌─────────────────────────────────────────────────────────────┐
│  ID │ Username         │ Status    │ Actions                │
│  1  │ @user167307...   │ warming   │ [▶ Farm] [📤 Post]    │
│     │                  │           │ [🔍 Sync] [📋 Logs]   │ ← NEW
│  2  │ @user435910...   │ warming   │ [▶ Farm] [📤 Post]    │
│     │                  │           │ [🔍 Sync] [📋 Logs]   │
└─────────────────────────────────────────────────────────────┘
```

### Click [📋 Logs] → modal/slide-out panel

```
┌─────────────────────────────────────────────────────────────┐
│  📋 Account Logs — @user1673074451623          [✕ Close]   │
├─────────────────────────────────────────────────────────────┤
│  Filter: [All] [Farm] [Health] [Sync] [Post] [Error]       │
│                                                             │
│  💚 14:20:01 │ Farm started (15 min)                        │
│  💚 14:20:03 │ Navigating to TikTok For You...              │
│  ✅ 14:20:08 │ Feed loaded!                                  │
│  🔄 14:20:15 │ Scrolled 5x, 6 videos seen                  │
│  ✅ 14:20:22 │ Liked 1 video                                │
│  ❌ 14:20:41 │ Comment failed (rate limited)                 │
│  💚 14:20:45 │ Session complete: 42 scrolls, 3 likes        │
│  🔵 14:12:25 │ Health check: NOT LOGGED IN (cookies hết hạn)│
│  💚 14:10:50 │ Farm started (15 min)                        │
│  🔴 14:10:52 │ ERROR: Navigation failed: EMPTY_RESPONSE     │
│  🔴 14:10:53 │ Browser reconnecting...                      │
│                                                             │
│  [Auto-refresh: ON]  [Export CSV]  [Clear]                  │
├─────────────────────────────────────────────────────────────┤
│  ▲ 24 logs (last 1 hour)                                    │
└─────────────────────────────────────────────────────────────┘
```

### Color coding

| Level | Icon | Meaning |
|-------|------|---------|
| SUCCESS | ✅ | Action thành công (like, follow, post, sync OK) |
| INFO | 💚 | Thông tin thường (farm start, scroll progress) |
| WARNING | 🔵 ⚠️ | Cảnh báo (login fail, rate limit, browser reconnect) |
| ERROR | 🔴 | Lỗi (navigation fail, crash, timeout) |

### WebSocket auto-update

Khi panel đang mở, WebSocket push log mới real-time:
```javascript
function openLogPanel(accountId) {
    // 1. Fetch recent logs from REST
    fetch(`/api/accounts/${accountId}/logs?limit=50`)
        .then(r => r.json())
        .then(logs => renderLogs(logs));
    
    // 2. Subscribe to WebSocket for real-time
    const ws = new WebSocket(`ws://${location.host}/api/logs/${accountId}`);
    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'log') {
            prependLog(msg.data);  // Thêm lên đầu
        }
    };
}
```

---

## IV. API Endpoints (mới + sửa)

### Logs endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/accounts/{id}/logs` | Get logs for account (query: limit, type, level) |
| GET | `/api/logs/recent` | Get recent logs across all accounts (limit=50) |
| WS | `/api/logs/{account_id}` | WebSocket — real-time log stream per account |
| DELETE | `/api/accounts/{id}/logs` | Clear logs for account |

### Proxy endpoints (sửa)

**Vấn đề hiện tại:** `GET /api/proxies` trả về 6 proxy active nhưng thực tế chỉ 2 IP có 4 endpoints.

**Sửa:** proxy health check phải test thật, không chỉ đọc status từ DB:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/proxies` | List proxies với health status thật |
| POST | `/api/proxies` | Create proxy — chỉ lưu vào DB |
| POST | `/api/proxies/import` | Bulk import từ CSV — REPLACE toàn bộ |
| DELETE | `/api/proxies/{id}` | Delete single proxy |
| POST | `/api/proxies/{id}/check` | Check 1 proxy (thử HTTP request) |
| POST | `/api/proxies/check-all` | Check ALL proxies — update status thật |
| POST | `/api/proxies/sync` | **NEW** — Sync DB từ CSV, xoá proxy không có trong CSV, thêm proxy mới |

### Proxy status logic mới

```
POST /api/proxies/check-all → test từng proxy:
  - HTTP GET https://www.tiktok.com (timeout 10s)
  - Nếu 200 → status = 'active'
  - Nếu timeout/error → status = 'inactive', fail_count += 1
  - Nếu fail_count >= 3 → status = 'dead'

GET /api/proxies → trả về:
[
  {
    "id": 5,
    "ip": "14.225.43.178",
    "port": 6666,
    "protocol": "http",
    "status": "active",    // ← từ health check thật
    "last_checked": "...",
    "fail_count": 0,
    "used_by": ["user1673074451623"],  // ← account nào đang dùng
  },
  ...
]
```

---

## V. Proxy Import/Re-sync Flow

### Cách import proxy đúng

Hiện tại proxy được import từ `config/proxies.csv` khi container start.
**Vấn đề:** ID không match → account trỏ vào proxy cũ đã xoá.

**Giải pháp — sync_proxies_to_db() rewrite:**

```python
def sync_proxies_to_db(csv_path, db_path):
    """
    1. Đọc proxies.csv → list[Proxy]
    2. So sánh với DB:
       - Proxy nào trong CSV nhưng không trong DB → INSERT
       - Proxy nào trong DB nhưng không trong CSV → DELETE (nếu không account nào dùng)
         hoặc → set status='deprecated' (nếu có account đang dùng)
       - Proxy nào trong CSV và trong DB → UPDATE status='active'
    3. Với account đang dùng proxy bị deprecated: 
       - Tự động gán proxy đầu tiên cùng IP
    """
```

### Xoá proxy rác (ID=3 và ID=4)

Hai proxy cũ `42.96.10.203:8241` và `103.57.128.248:8768` không có trong CSV.
Account 1 (ID=1) đang dùng proxy ID=3 (42.96.10.203) — cần chuyển sang proxy thật.

**Luồng xoá:**
1. `UPDATE accounts SET proxy_id = 5 WHERE proxy_id = 3` (chuyển account 1 sang proxy 14.225.43.178:6666)
2. `DELETE FROM proxies WHERE id IN (3,4)` (xoá proxy rác)
3. `UPDATE proxies SET status='active' WHERE id IN (5,6,7,8)` (set đúng 4 proxy)

---

## VI. Dashboard UI — Proxies Tab (cập nhật)

### Table mới

```
Proxies Tab:
┌──────┬────────────────────┬──────┬──────────┬──────────┬──────────┬───────────┐
│  ID  │  Proxy Server      │ Port │ Protocol │ Status   │ Accounts │ Action    │
├──────┼────────────────────┼──────┼──────────┼──────────┼──────────┼───────────┤
│  5   │ 14.225.43.178      │ 6666 │ HTTP     │ ✅ Live  │ Acc #1   │ [Check]   │
│  6   │ 14.225.43.178      │ 7777 │ SOCKS5   │ ✅ Live  │ —        │ [Check]   │
│  7   │ 14.225.48.219      │ 62341│ HTTP     │ ✅ Live  │ —        │ [Check]   │
│  8   │ 14.225.48.219      │ 62342│ SOCKS5   │ ✅ Live  │ Acc #2   │ [Check]   │
└──────┴────────────────────┴──────┴──────────┴──────────┴──────────┴───────────┘
```

- **Status** = màu xanh/đỏ dựa trên health check thật, không phải DB status
- **Accounts** = account nào đang dùng proxy này
- **[Check]** = test 1 proxy, update real-time

### Nút chức năng mới

```
[🔍 Check All]  — check tất cả proxy, update status thật
[📥 Sync CSV]   — re-sync từ proxies.csv, xoá proxy rác
[➕ Add Proxy]  — thêm thủ công
```

---

## VII. Files changed

| File | Action | Description |
|------|--------|-------------|
| `src/log_manager.py` | **NEW** | Central log collector: ring buffer + SQLite + WebSocket |
| `src/farm_engine.py` | **MODIFY** | Replace `log.info()` calls → `LogManager.log()` |
| `src/health_monitor.py` | **MODIFY** | Ghi log vào LogManager thay vì chỉ print |
| `src/profile_scanner.py` | **MODIFY** | Ghi log scan result vào LogManager |
| `src/proxy_manager.py` | **REWRITE** | Sync từ CSV, health check thật, auto-migrate account |
| `web/api.py` | **MODIFY** | Thêm logs endpoints, WS, proxy sync/check endpoints |
| `web/templates/index.html` | **MODIFY** | Account Logs panel, Proxy health badges |
| `web/static/js/dashboard.js` | **MODIFY** | WebSocket client, log rendering, proxy check UI |
| `src/database.py` | **MODIFY** | Add account_logs table |

---

## VIII. Implementation Order

### P0 — Xoá proxy rác + fix account proxy (ngay lập tức)
1. Chuyển Account 1 từ proxy ID=3 → ID=5
2. Xoá proxy ID=3,4 khỏi DB
3. Fix `sync_proxies_to_db()` để không tạo proxy trùng/dư

### P1 — LogManager core
4. `src/log_manager.py` — ring buffer + SQLite + _broadcast()
5. `account_logs` table trong database.py
6. API: GET logs, WS endpoint

### P2 — Inject logs vào modules
7. Patch `farm_engine.py` → LogManager thay print
8. Patch `health_monitor.py` → LogManager thay print
9. Patch `profile_scanner.py` → LogManager

### P3 — Proxy health check thật
10. `POST /api/proxies/check-all` — test HTTP real
11. `POST /api/proxies/sync` — CSV sync, xoá rác

### P4 — Dashboard UI
12. Account Logs button → modal/panel
13. WebSocket auto-update logs
14. Proxy tab status badges từ health check thật

---

## IX. DB Migration

```sql
-- New table
CREATE TABLE account_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    log_type TEXT NOT NULL,     -- 'farm', 'health', 'sync', 'post', 'system', 'error'
    level TEXT DEFAULT 'INFO',  -- 'INFO', 'WARNING', 'ERROR', 'SUCCESS'
    message TEXT NOT NULL,
    details TEXT,               -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast query by account
CREATE INDEX idx_logs_account ON account_logs(account_id, created_at);

-- Clean proxy rác
DELETE FROM accounts WHERE id = 3 AND username LIKE '42.96%';
-- Thực tế: UPDATE accounts SET proxy_id = 5 WHERE proxy_id = 3
--         DELETE FROM proxies WHERE id IN (3,4)
```

---

## X. Pitfalls

- **LogManager singleton** — phải là module-level instance, inject vào các module qua `AppState`
- **WebSocket giữ connection** — cần heartbeat + auto-reconnect client-side
- **WebSocket trong Docker** — cần expose thêm port hoặc dùng path-based WS (FastAPI tự handle)
- **Proxy check thật có thể mất thời gian** — dùng asyncio.gather để check parallel, timeout 10s mỗi proxy
- **CSV sync không được xoá proxy đang có account dùng** — chỉ set status='deprecated', cho user migrate sau
- **Không lưu `password` vào logs** — filter field trước khi broadcast hoặc persist
- **Logs table có thể lớn** — nên có cron job delete logs > 7 ngày

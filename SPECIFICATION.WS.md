# TikTok Farm — Real-time Farm Monitor SPECIFICATION v1.0
## WebSocket-based live streaming cho Dashboard

---

## 1. Mục tiêu

Dashboard hiện chỉ có POST API trả về 200 OK khi click "Farm", không cho user thấy:
- Farm đã bắt đầu chưa?
- Browser đang navigate đến đâu?
- Scroll được bao nhiêu video?
- Like/comment/follow thành công hay fail?
- Session kết thúc với kết quả gì?

=> Thêm **WebSocket real-time event stream** + UI log viewer + progress indicator.

---

## 2. Architecture

```
User Click → POST /api/actions/farm/{id}
                │
                ▼
     FarmEngine.run_farm_session()
         │  emits farm:event  │  
         ▼                    ▼
    EventBus ──WebSocket──▶ Dashboard UI
    (asyncio.Queue)         (live log panel
        ↑                     + progress bar
    log_manager.py            + status badge)
```

- **EventBus** = singleton in-memory, mỗi farm session publish events vào queue
- **WebSocket** = FastAPI WebSocket endpoint `/api/ws/{session_id}`, client subscribe
- **FarmEngine** emit events tại mỗi bước (start, navigate, scroll, like, comment, follow, end, error)

---

## 3. Event Protocol

Mỗi event là JSON string:

```json
{
  "type": "farm:start" | "farm:progress" | "farm:action" | "farm:log" | "farm:error" | "farm:complete",
  "account_id": 1,
  "timestamp": "2026-05-31T14:20:00Z",
  "data": { ... }
}
```

### Event Types

| type | Khi nào emit | data example |
|------|-------------|-------------|
| `farm:start` | Session bắt đầu | `{duration: 15, actions: {scroll: true, like: 3, ...}}` |
| `farm:progress` | Mỗi 30s trong scroll | `{elapsed_sec: 45, scrolls: 12, videos_seen: 15}` |
| `farm:action` | Mỗi action (like/comment) | `{action: "like", status: "ok", video_id: "..."}` |
| `farm:log` | Log message thường | `{level: "INFO", message: "Navigating to TikTok feed..."}` |
| `farm:error` | Lỗi xảy ra | `{message: "Scroll failed: timeout", recoverable: true}` |
| `farm:complete` | Session kết thúc | `{stats: {scrolls: 42, likes: 3, ...}, duration: 900}` |

---

## 4. Backend Implementation

### 4.1 EventBus (event_bus.py)

```python
class FarmEventBus:
    """Singleton event bus for real-time farm session events."""
    
    _instance = None
    
    def __init__(self):
        self._queues: Dict[str, asyncio.Queue] = {}
    
    def create_session(self, session_id: str):
        self._queues[session_id] = asyncio.Queue(maxsize=500)
    
    def emit(self, session_id: str, event: dict):
        q = self._queues.get(session_id)
        if q and not q.full():
            q.put_nowait(event)
    
    async def subscribe(self, session_id: str) -> AsyncGenerator[dict, None]:
        q = self._queues.get(session_id)
        if not q:
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield event
                except asyncio.TimeoutError:
                    yield {"type": "farm:ping"}
        except asyncio.CancelledError:
            pass
        finally:
            self._queues.pop(session_id, None)
    
    def cleanup_session(self, session_id: str):
        self._queues.pop(session_id, None)
```

### 4.2 FarmEngine emit events

Trong `farm_engine.py`, inject event bus:

```python
class FarmEngine:
    def __init__(self, browser_manager, account_manager=None, event_bus=None):
        self.event_bus = event_bus or FarmEventBus.get_instance()
        ...
    
    async def scroll_feed(self, account_id, proxy_url=None, duration_minutes=10, session_id=None):
        self._emit(session_id, "farm:log", {"level": "INFO", "message": "Starting scroll feed..."})
        try:
            page = await self.browser.get_page(account_id, proxy_url)
            success = await self.browser.navigate_safe(page, target_url)
            self._emit(session_id, "farm:action", {"action": "navigate", "status": "ok" if success else "fail"})
            ...
            if scroll_count % 5 == 0:
                self._emit(session_id, "farm:progress", {"scrolls": scroll_count, "videos_seen": stats["videos_viewed"]})
        except Exception as e:
            self._emit(session_id, "farm:error", {"message": str(e), "recoverable": True})
    
    def _emit(self, session_id, event_type, data):
        if session_id:
            self.event_bus.emit(session_id, {
                "type": event_type,
                "account_id": self._current_account,
                "timestamp": datetime.now().isoformat(),
                "data": data
            })
    
    async def run_farm_session(self, account_id, ...):
        session_id = f"farm_{account_id}_{int(time.time())}"
        self.event_bus.create_session(session_id)
        self._emit(session_id, "farm:start", {"duration": duration_minutes, "actions": actions})
        ...
        self._emit(session_id, "farm:complete", {"stats": final_stats})
```

### 4.3 WebSocket endpoint (web/api.py)

```python
@router.websocket("/ws/{session_id}")
async def farm_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    event_bus = FarmEventBus.get_instance()
    try:
        async for event in event_bus.subscribe(session_id):
            await websocket.send_json(event)
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
```

### 4.4 API trả về session_id

Khi POST farm, trả về session_id để client kết nối WebSocket:

```json
{
  "success": true,
  "message": "Farm session started",
  "session_id": "farm_1_1717165200",
  "ws_url": "/api/ws/farm_1_1717165200"
}
```

---

## 5. Dashboard Frontend

### 5.1 Live Log Panel (HTML)

```
┌────────────────────────────────────────────────┐
│ 🟢 Farm Session Active       [2:45 / 15:00]    │
├────────────────────────────────────────────────┤
│ ℹ️ 14:20:01 → Starting farm session...         │
│ ℹ️ 14:20:03 → Navigating to TikTok For You...  │
│ ✅ 14:20:08 → Feed loaded!                      │
│ 🔄 14:20:15 → Scrolling (#5, 6 videos seen)   │
│ ✅ 14:20:22 → Liked 1 video                     │
│ 🔄 14:20:35 → Scrolling (#10, 12 videos seen)  │
│ ❌ 14:20:41 → Comment failed (rate limited)     │
│ ℹ️ 14:20:45 → Session complete: 42 scrolls...  │
├────────────────────────────────────────────────┤
│ [   ████████████░░░░░░░░░░░░░░░░░   ] 45%      │
│ Stats: 42 scrolls · 3 likes · 1 follow · 1 cmt │
└────────────────────────────────────────────────┘
```

### 5.2 WebSocket Client (JavaScript)

```javascript
function startFarmSession(accountId) {
    fetch(`/api/actions/farm/${accountId}?duration=15`)
        .then(r => r.json())
        .then(data => {
            if (!data.session_id) return;
            showLogPanel();
            const ws = new WebSocket(`ws://${location.host}/api/ws/${data.session_id}`);
            
            ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                
                if (msg.type === 'farm:ping') return;
                if (msg.type === 'farm:start') {
                    updateProgressBar(0, msg.data.duration);
                }
                if (msg.type === 'farm:progress') {
                    appendLog(`🔄 Scrolled ${msg.data.scrolls}x, ${msg.data.videos_seen} videos`);
                    updateProgressBar(msg.data.elapsed_sec, totalDuration);
                }
                if (msg.type === 'farm:action') {
                    const icon = msg.data.status === 'ok' ? '✅' : '❌';
                    appendLog(`${icon} ${msg.data.action}: ${msg.data.status}`);
                }
                if (msg.type === 'farm:log') {
                    appendLog(`ℹ️ ${msg.data.message}`);
                }
                if (msg.type === 'farm:error') {
                    appendLog(`🔴 ${msg.data.message}`);
                }
                if (msg.type === 'farm:complete') {
                    appendLog(`✅ Session complete! ${JSON.stringify(msg.data.stats)}`);
                    hideLogPanel();
                }
            };
        });
}
```

### 5.3 UI State Machine

```
[IDLE] → click "Farm" → [CONNECTING] → WS connected → [STREAMING]
                                                   ↓
                                          [WS error] → reconnect ×3
                                                   ↓
                                          [FAILED] → show retry button
```

- **IDLE**: Nút "Farm" hiển thị bình thường
- **CONNECTING**: Nút disabled + spinner
- **STREAMING**: Log panel hiện, progress bar chạy
- **COMPLETE**: Sau 5s, log panel collapse gọn lại, nút Farm enable lại

### 5.4 UI Elements cần thêm vào dashboard

- `#live-log-panel` — hidden by default, show khi có farm session
  - `#live-log-header` — "🟢 Farm Session Active [elapsed / total]"
  - `#live-log-content` — scrollable log area (max 100 lines, auto-scroll bottom)
  - `#farm-progress-bar` — progress bar + timer
  - `#farm-stats` — live stats counters (scrolls, likes, follows, comments)

---

## 6. Files to Modify

| File | Changes |
|------|---------|
| `src/event_bus.py` | **New file** — FarmEventBus singleton |
| `src/farm_engine.py` | Inject event_bus, emit events at each step, pass session_id through to scroll_feed/like_videos/comment_random/follow_accounts |
| `src/main.py` | Init FarmEventBus in AppState |
| `web/api.py` | WebSocket endpoint `/api/ws/{session_id}`, return session_id in farm POST |
| `web/templates/index.html` | Live log panel HTML + WebSocket JS |
| `requirements.txt` | Ensure `websockets` is available (FastAPI ships with it) |

---

## 7. Implementation Order

1. **`src/event_bus.py`** — standalone, test với script ngắn
2. **Patch `src/farm_engine.py`** — inject event_bus, emit events, pass session_id
3. **Patch `web/api.py`** — WebSocket endpoint, return session_id
4. **Patch `web/templates/index.html`** — UI panel + JS WebSocket client
5. **Test locally** → push GitHub → rebuild server

---

## 8. Edge Cases

| Case | Behavior |
|------|----------|
| User click Farm 2 lần | Cancel session cũ (send `farm:cancel` event), start mới |
| WebSocket disconnect mạng | Auto-reconnect ×3 (1s, 2s, 4s backoff), nếu hết → show "Connection lost" banner, nút Retry |
| Farm session kết thúc trước khi WS connect | Queue vẫn giữ events ~5s, gửi tất cả khi WS connect |
| Server restart mid-farm | WebSocket disconnect → dashboard show "Farm interrupted" → user click Farm lại |
| Browser crash (auto-reconnect) | Emit `farm:error` "Browser reconnecting..." + `farm:log` "Browser reconnected" khi thành công |

---

## 9. Future Ideas (v2)

- **Persist events** to SQLite (`farm_events` table) — replay history trên dashboard reload
- **Multi-account tab** — xem nhiều account farm cùng lúc trong tabs
- **Telegram notify** — farm session start/complete gửi Telegram real-time (đã có telegram_alert.py, chỉ cần emit thêm)
- **Session recording** — lưu full session log để debug sau

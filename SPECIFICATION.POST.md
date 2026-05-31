# TikTok Farm — Post Manager SPECIFICATION v1.0
## Dashboard UI cho Slideshow & Video Upload

---

## 1. Mục tiêu

Backend đã có đầy đủ:
- `POST /api/actions/post/{account_id}` — generate content + upload slideshow
- `POST /api/actions/upload/video/{account_id}` — upload mp4 video
- `content_pipeline.py` — tạo ảnh slideshow từ template + Pillow
- `post_engine.py` — login maintain + file upload + caption/hashtag

Nhưng dashboard frontend **không có nút Post nào cả**. User phải dùng curl để gọi API.

=> Thêm **Post Manager UI** vào dashboard: compose post, preview, upload, track history.

---

## 2. User Flow

```
[Accounts tab]
    │
    ├── Account row ── [Farm] [Post ▼] [Check]
    │                         │
    │                         ▼
    │              ┌─────────────────────┐
    │              │  Post Composer      │
    │              │                     │
    │              │  Post type:         │
    │              │  ○ Slideshow (auto) │
    │              │  ○ Video (manual)   │
    │              │                     │
    │              │  Caption: [........]│
    │              │  Hashtags: [.......]│
    │              │  Affiliate link: [] │
    │              │                     │
    │              │  Preview:           │
    │              │  [img1] [img2] [img3]│
    │              │                     │
    │              │  [🔄 Generate] [⬆️ Post]│
    │              └─────────────────────┘
    │
    ├── Posts tab ── Lịch sử post
                        ├─ Account 1 | 2 phút trước | ✅ posted | 0 views
                        ├─ Account 2 | 1 giờ trước  | ❌ failed | timeout
                        └─ Account 1 | 3 giờ trước  | ✅ posted | 142 views
```

**Steps:**
1. User click **Post ▼** dropdown trên account row
2. Chọn **Compose Post** → mở Post Composer modal
3. Chọn post type (slideshow hoặc video)
4. Edit caption + hashtags + affiliate link (optional)
5. Click **🔄 Generate** → xem preview ảnh slideshow
6. Click **⬆️ Post** → gọi API, show progress + kết quả
7. Kết quả tự động lưu vào lịch sử Posts

---

## 3. Backend Endpoints

### Existing (đã có trên server)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/actions/post/{account_id}` | Generate slideshow content + upload |
| POST | `/api/actions/upload/video/{account_id}` | Upload từ video file path |

### Cần thêm

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/posts?account_id=N&limit=20` | Lịch sử posts |
| GET | `/api/posts/{post_id}/stats` | Fetch stats từ TikTok API |
| POST | `/api/actions/preview/{account_id}` | Generate content chỉ để preview (không upload) |
| GET | `/api/post-templates` | Danh sách content templates |

### POST /api/actions/preview/{account_id} (new)

Tạo slideshow preview images, trả về URLs mà không upload:

```json
{
  "success": true,
  "preview_id": "preview_1_1717165200",
  "images": [
    "/api/preview/preview_1_1717165200/slide_1.png",
    "/api/preview/preview_1_1717165200/slide_2.png",
    "/api/preview/preview_1_1717165200/slide_3.png"
  ],
  "caption_preview": "Amazing quality! Highly recommend.\n\n#fyp #viral #review",
  "rating": 4.5,
  "price": "$29.99"
}
```

### GET /api/posts (new)

```json
{
  "success": true,
  "count": 15,
  "posts": [
    {
      "id": 42,
      "account_id": 1,
      "username": "user1673074451623",
      "tiktok_post_id": "7345678901234567890",
      "media_type": "slideshow",
      "caption": "Check this out! 🔥",
      "hashtags": "fyp foryou",
      "status": "posted",
      "views": 142,
      "likes": 12,
      "comments": 3,
      "shares": 1,
      "scheduled_at": "2026-06-01T08:00:00",
      "posted_at": "2026-06-01T08:02:15",
      "created_at": "2026-05-31T12:00:00"
    }
  ]
}
```

---

## 4. Dashboard Frontend

### 4.1 Post Composer Modal

```
┌──────────────────────────────────────────────────┐
│  Post Composer · Account 1                 [✕]  │
├──────────────────────────────────────────────────┤
│                                                 │
│  Post Type                                       │
│  [● Slideshow (auto-generate)]                   │
│  [○ Video (upload existing)]                     │
│                                                 │
│  ── Video path ── (only for Video type)         │
│  [path/to/video.mp4.........................]    │
│                                                 │
│  Template                                        │
│  [▼ Product Review ]                             │
│                                                 │
│  Caption                                         │
│  [............................................] │
│  [Check this out! 🔥..........................] │
│  [............................................] │
│                                                 │
│  Hashtags (space separated, no #)                │
│  [fyp foryou viral tiktok....................]   │
│                                                 │
│  Affiliate Link (optional)                       │
│  [https://shop.tiktok.com/product/......]        │
│                                                 │
│  ── Preview ──                                   │
│  [img1] [img2] [img3] [img4] [img5]              │
│  ⭐ Rating: 4.5 · 💰 Price: $29.99              │
│                                                 │
│  Schedule (optional)                             │
│  [● Post now]  [○ Schedule: [📅] [⏰] ]         │
│                                                 │
│  [🔄 Generate Preview]   [⬆️ Post to TikTok]   │
│                                                 │
└──────────────────────────────────────────────────┘
```

### 4.2 Posts History Tab

```
  ┌─────────────────────────────────────────────────────┐
  │  Posts                         [📥 Refresh] [Filter ▼]│
  ├──────────┬────────┬──────────┬─────────┬───────────┤
  │ Account  │ Media  │ Caption  │ Status  │ Views     │
  ├──────────┼────────┼──────────┼─────────┼───────────┤
  │ @user1   │ 🖼️ 5  │ "Amazing"│ ✅ done │ 142 👁     │
  │ @user2   │ 🎬     │ "Check"  │ ❌ fail │ -         │
  │ @user1   │ 🖼️ 3  │ "Best"   │ ⏳ prog │ -         │
  │ @user2   │ 🖼️ 5  │ "Fast"   │ ✅ done │ 89 👁      │
  ├──────────┴────────┴──────────┴─────────┴───────────┤
  │ [< 1 2 3 4 5 ... >]                                │
  └─────────────────────────────────────────────────────┘
  ```

### 4.3 Account Row — Post Button

Mỗi account row trong Accounts tab thêm dropdown:

```
┌────────────────────────────────────────────────────────────┐
│ @user1673074451623  warming  proxy#3  Cookies: ✅ 35       │
│ [Farm] [Post ▼] [🔄 Sync] [🧹 Clear] [✕ Delete]         │
│         ├─ Compose Post                                    │
│         ├─ Quick Post Slideshow                            │
│         └─ Upload Video...                                 │
├────────────────────────────────────────────────────────────┤
```

- **Compose Post** — mở modal composer
- **Quick Post Slideshow** — auto-generate + upload ngay (caption default, hashtags từ config)
- **Upload Video...** — mở modal với video path input

### 4.4 Post Progress Indicator

Khi post đang chạy:

```
┌──────────────────────────────────┐
│ ⬆️ Posting...                    │
│ [████████░░░░░░░░░░░░] 45%      │
│ ℹ️ Generating slides...          │
│ ℹ️ Logging in to TikTok...       │
│ ℹ️ Uploading 5 images...         │
│ ✅ Post successful! (0:45)       │
└──────────────────────────────────┘
```

Real-time progress dùng WebSocket tương tự Farm Monitor SPEC.

---

## 5. API Integration Details

### Quick Post Slideshow

```
POST /api/actions/post/{account_id}
Body:
{
  "caption": "Check this out! 🔥",
  "hashtags": "fyp foryou viral",
  "affiliate_link": "https://shop.tiktok.com/..."
}
→ {"success": true, "result": {...}}
```

### Preview (generate không upload)

```
POST /api/actions/preview/{account_id}
Body:
{
  "rating": 4.5,
  "review": "Amazing quality!",
  "price": "$29.99"
}
→ {"success": true, "preview_id": "...", "images": [...]}
```

### Upload Video

```
POST /api/actions/upload/video/{account_id}
Body:
{
  "video_path": "/app/data/videos/video_001.mp4",
  "caption": "New video! #fyp",
  "hashtags": "fyp foryou",
  "affiliate_link": ""
}
→ {"success": true, "result": {...}}
```

---

## 6. Files to Modify

| File | Changes |
|------|---------|
| `web/api.py` | Thêm `preview` endpoint, `GET /api/posts` history, fix `trigger_post` dùng body params thay vì hardcode |
| `src/main.py` | Mount `/api/preview/` static file serving cho preview images |
| `web/templates/index.html` | Thêm Posts section, Post Composer Modal, update Account rows |
| `web/static/css/dashboard.css` | Modal styles, post progress bar, history table styles |

---

## 7. Implementation Order

1. **Backend:** `GET /api/posts`, `POST /api/actions/preview/{id}`, body params cho trigger_post
2. **Frontend:** Posts History tab
3. **Frontend:** Post Composer Modal
4. **Frontend:** Account row Post dropdown
5. **Integration:** Connect all buttons → API calls + toast notifications
6. **Test:** Account farm → Compose Post → Generate Preview → Post → Check history

---

## 8. Edge Cases

| Case | Behavior |
|------|----------|
| No content templates | Disable "Generate Preview" button, show "No templates found" |
| Account not logged in | Auto-attempt login with cookies, nếu fail → show error "Login required" |
| Duplicate post detection | Check `posts` table trước khi upload, cảnh báo nếu caption giống post gần nhất |
| Video path invalid | Validate path tồn tại trước khi gửi API |
| TikTok rate limit | Catch rate limit error, show "Try again in 30 min", suggest schedule post |
| Post scheduled in future | Lưu vào DB với status='scheduled', FarmScheduler xử lý khi đến giờ |
| Preview không có ảnh product | Tự động fallback sinh ảnh text-only với nền gradient |
| Multiple tabs open | WS events vẫn hoạt động bình thường (mỗi tab subscribe riêng) |

---

## 9. Future Ideas (v2)

- **Bulk post** — chọn nhiều account, post cùng content lên nhiều acc
- **Post analytics** — views/likes/shares chart theo thời gian
- **A/B test captions** — post cùng ảnh với 3 caption khác nhau lên 3 account
- **Auto-scheduler** — farm session xong → auto post affiliate content
- **Content library** — lưu ảnh/video đã dùng, reuse cho nhiều account

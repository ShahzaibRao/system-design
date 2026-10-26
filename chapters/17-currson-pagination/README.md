# Chapter 17 — Cursor Pagination (Feed Pages That Scale)

## What this chapter builds on, and what it unlocks next

- Builds on Chapter 09 (feed page, `created_at` index, limit 20) and Chapter 12/13 (feed response shape, authenticated view).
- Unlocks Chapter 18 (caching): a paginated feed gives the cache one key per page, and invalidation rules per page.
- Followers + followed-only feed (last chapter, a feature) will be a filtered paginated feed — the same cursor logic with one extra filter.

## The problem, in one line

The feed always returned the newest 20 posts and nothing else. There was no way to reach post 21.

## Step 1 — Baseline (and why the first measurement was wrong, then fixed)

First baseline, before seeding:

- Total posts: **17**
- Feed length: **17** (all posts visible)
- First post: `6ade086ab1def89dd62e4a45`, ali, `2026-10-25T13:47:22.689000`
- Last post: `6add9ea8a7f1673c638b8636`, ali, `2026-10-25T06:16:08.255000`
- `get_feed` code (`apps/social-lab/backend/app/main.py`, lines 427-432):

```python
@app.get("/api/v1/feed")
def get_feed():
    posts = posts_collection.find().sort("created_at", -1).limit(20)
    result = [post_to_dict(post) for post in posts]
    for item in result:
        item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
    return result
```

WHY this was a misleading baseline: with 17 posts, no post is unreachable yet. The limit-20 cap is a **latent** problem — it only breaks the day posts exceed 20. The assistant first predicted more than 20 posts; the real count was 17, and that wrong prediction was owned plainly instead of being covered over.

Fix: seed real volume, then re-measure. The user suggested seeding 2,000–4,000 posts so the concept could be understood at scale. 2,000 was chosen: it makes 100 pages of 20, inserts and deletes in seconds, and 4,000 would teach nothing extra.

Seed script (direct Mongo insert, test data setup only — not app behaviour):

```python
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
db = MongoClient('mongodb://localhost:27017')['social_lab']
now = datetime.now(timezone.utc).replace(tzinfo=None)
users = ['ali', 'bob', 'raoshahzaib']
docs = []
for i in range(2000):
    docs.append({
        'username': users[i % 3],
        'content': 'seed post ' + str(i),
        'created_at': now - timedelta(minutes=i),
        'likes_count': 0,
        'liked_by': [],
        'comments': [],
        'views': 0,
    })
result = db.posts.insert_many(docs)
```

Seed result:

- Inserted: **2000**, total posts: **2017**, seed posts: **2000**
- Feed length: still **20** — first `6adedb0857909d69d411a8d6` (ali, `2026-10-26T04:46:00.214000`), 20th `6adedb0857909d69d411a8e9` (bob, `2026-10-26T04:27:00.214000`)
- Conclusion, now true: **1997 posts are unreachable** no matter how many times the feed reloads.

## Concept — Offset vs cursor pagination

Offset pagination: `skip(page * size)`. To serve page 100, the database must scan and throw away the first 2,000 rows, then return 20. Cost grows with the page number. Worse, if a new post arrives while the user is paging, every page number shifts and posts get duplicated or skipped.

Cursor pagination: the client sends back a marker — the last item it already saw. The database seeks straight to that marker with an index range query and returns the next 20. Cost is the same on page 1 and page 100, and new posts at the top do not shift the pages already walked.

Cursor format here: `<created_at>_<id>`, for example `2026-10-26T04:46:00.214000_<ObjectId>`. Time alone is not enough, because two posts can share one timestamp — time + id together make the marker unique and the order deterministic.

## Step 2 — Backend patch

File: `apps/social-lab/backend/app/main.py`

BEFORE (flag + health anchor):

```python
@app.get("/health")
```

AFTER:

```python
CURSOR_PAGINATION_ENABLED = os.getenv("CURSOR_PAGINATION_ENABLED", "true").lower() == "true"


@app.get("/health")
```

BEFORE (`get_feed`, full function):

```python
@app.get("/api/v1/feed")
def get_feed():
    posts = posts_collection.find().sort("created_at", -1).limit(20)
    result = [post_to_dict(post) for post in posts]
    for item in result:
        item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
    return result
```

AFTER (new helpers + new `get_feed`):

```python
def _make_cursor(post):
    return post["created_at"].isoformat() + "_" + str(post["_id"])


def _parse_cursor(cursor):
    created_str, post_id = cursor.rsplit("_", 1)
    created_at = datetime.fromisoformat(created_str)
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    return created_at, ObjectId(post_id)


@app.get("/api/v1/feed")
def get_feed(limit: int = 20, cursor: str | None = None):
    if not CURSOR_PAGINATION_ENABLED:
        posts = posts_collection.find().sort("created_at", -1).limit(20)
        result = [post_to_dict(post) for post in posts]
        for item in result:
            item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
        return result
    limit = max(1, min(limit, 100))
    query = {}
    if cursor:
        try:
            cursor_time, cursor_id = _parse_cursor(cursor)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid cursor")
        query = {"$or": [{"created_at": {"$lt": cursor_time}}, {"created_at": cursor_time, "_id": {"$lt": cursor_id}}]}
    docs = list(posts_collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(limit + 1))
    has_more = len(docs) > limit
    docs = docs[:limit]
    result = [post_to_dict(post) for post in docs]
    for item in result:
        item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
    next_cursor = _make_cursor(docs[-1]) if has_more and docs else None
    return {"posts": result, "next_cursor": next_cursor}
```

WHY each part exists:

- **Sort `created_at` desc + `_id` desc:** the tie-breaker makes order deterministic even when two posts share a timestamp. The old sort (created_at only) left ties in undefined order.
- **Query `created_at < cursor_time` OR (`created_at == cursor_time` AND `_id < cursor_id`):** this is the seek. Nothing is skipped, nothing is repeated, even on timestamp ties.
- **Fetch `limit + 1`:** the extra row is the "is there another page?" signal. If it exists, `next_cursor` is the last returned post's cursor. If not, `next_cursor` is null and the frontend hides Load More.
- **Response shape change (breaking, stated plainly):** the feed used to return a bare list. With the flag on it returns `{"posts": [...], "next_cursor": ...}`. The only in-repo consumer is the frontend, updated in the same patch, so nothing else breaks. With the flag off, the old list shape returns.
- **Invalid cursor returns 400**, not 500. A crafted cursor can only read the public feed — it grants access to no private data, because the feed has no private posts.
- **Limit clamped to 1–100:** a client cannot ask for a million rows in one call. Production default stays 20; tests used limit 5 and limit 20.

Backend verification (3 pages, limit 5):

- Page1: 5 posts, next true; page2: 5 posts, next true; overlap page1/page2: **0**
- Page2 first post older than page1 last post: **True**
- Page3: 5 posts, next true; overlap page2/page3: **0**

## Step 2 — Frontend patch

Files: `apps/social-lab/frontend/feed.js`, `apps/social-lab/frontend/feed.html`

BEFORE (`loadFeed`):

```javascript
function loadFeed(incrementViews) {
  fetch('/api/v1/feed')
    .then(function (res) { return res.json(); })
    .then(function (posts) {
      const postsDiv = document.getElementById('posts');
      postsDiv.textContent = '';
      if (posts.length === 0) {
        postsDiv.textContent = 'No posts yet';
        return;
      }
      posts.forEach(function (post) {
        renderPost(post, postsDiv, incrementViews);
      });
    });
}
```

AFTER (new state + `loadFeed` + `loadMore`):

```javascript
let nextCursor = null;

function updateLoadMoreButton() {
  var button = document.getElementById('load-more-button');
  if (button) {
    button.hidden = !nextCursor;
  }
}

function loadFeed(incrementViews) {
  fetch('/api/v1/feed')
    .then(function (res) { return res.json(); })
    .then(function (data) {
      const postsDiv = document.getElementById('posts');
      postsDiv.textContent = '';
      var posts = Array.isArray(data) ? data : data.posts;
      nextCursor = Array.isArray(data) ? null : data.next_cursor;
      if (posts.length === 0) {
        postsDiv.textContent = 'No posts yet';
      } else {
        posts.forEach(function (post) {
          renderPost(post, postsDiv, incrementViews);
        });
      }
      updateLoadMoreButton();
    });
}

function loadMore() {
  if (!nextCursor) {
    return;
  }
  fetch('/api/v1/feed?cursor=' + encodeURIComponent(nextCursor))
    .then(function (res) { return res.json(); })
    .then(function (data) {
      const postsDiv = document.getElementById('posts');
      var posts = Array.isArray(data) ? data : data.posts;
      nextCursor = Array.isArray(data) ? null : data.next_cursor;
      posts.forEach(function (post) {
        renderPost(post, postsDiv, true);
      });
      updateLoadMoreButton();
    });
}
```

And at the end of `feed.js`:

```javascript
document.getElementById('load-more-button').addEventListener('click', loadMore);
```

And in `feed.html`, after the posts div:

```html
<button id="load-more-button" hidden>Load More</button>
```

WHY each part exists:

- **`loadFeed` always restarts from page 1** (clears the list, resets the cursor). After a like, comment, or new post, the list is never half old page / half new page.
- **`loadMore` appends, never clears.** Newly shown posts get their views incremented (same definition as the initial load: a shown post counts as a view).
- **Button visibility follows `next_cursor`, not a guessed page count.** Cursor present = button shows; null = button hides.
- **Button hidden via the `hidden` attribute, not a style attribute**, so Chapter 15's CSP `style-src 'self'` cannot interfere.
- **`Array.isArray(data)` check:** flag off returns the old list shape, and the frontend still works on it — cursor null, button hidden, feed shows 20 posts as before. This backward compatibility is deliberate.

Frontend verification:

- `node --check` OK; grep shows `nextCursor`, `loadMore`, button line 22 in `feed.html`, listener line 229 in `feed.js`.
- Browser: Load More button appears; after clicks the post count grows 20 → 40 → 60 without clearing earlier posts; on the last post the button disappears. The last post reached was `seed post 1999` (bob, `2026-10-24T19:27:00.214000`), the oldest post in the whole list. The Chapter 16 own-post Delete button still appears only on the user's own posts.

## Step 3 — Full walk, offset vs cursor, flag test, cleanup

Full walk (limit 20, follow `next_cursor` until null):

- Pages: **101** (100 × 20 = 2000, last page 17)
- Unique posts seen: **2017**
- Final next_cursor: **None** — no duplicates, no missed posts.

Offset vs cursor, measured on the same data (last page: skip 2000 vs cursor after post 2000):

| Query | Docs examined | Time (3 runs) |
|---|---|---|
| Offset: `sort(created_at -1, _id -1).skip(2000).limit(20)` | **2017** | 3.84 / 4.93 / 4.46 ms (avg ~4.4) |
| Cursor: range query + `limit(20)` | **18** | 1.53 / 1.19 / 1.12 ms (avg ~1.3) |

The offset explain shows three stages: `COLLSCAN` (all 2017 docs) → `SORT` (in memory) → `SKIP` (throw away 2000) → 17 returned. The cursor query examined 18 docs. On a local machine the time gap is small; the examined-docs gap is the scaling proof: at 2 million posts, offset examines 2 million, cursor still examines about 20. Interview line: "offset cost grows with the page number; cursor cost is flat."

Flag test:

- `CURSOR_PAGINATION_ENABLED=false` → feed returns a **list**, length **20**, no `next_cursor`. Old behaviour restored.
- Normal restart → feed returns a **dict** with `posts` + `next_cursor`.

Cleanup (seed posts were test-only, by agreement before seeding):

- `delete_many({'content': {'$regex': '^seed post'}})` → deleted **2000**, total posts back to **17**, seed posts left **0**.
- A second delete run deleted **0** — proof the cleanup was complete, not assumed.
- Final feed check after normal restart: feed posts **17**, next_cursor **None** (17 < 20, one page).
- One trap, honestly recorded: the first post-cleanup feed check failed with `TypeError: list indices must be integers` because the server was still running flag-off at that moment and returned a list while the check expected a dict. That was a stale server, not an app bug — a normal restart fixed it. Lesson repeated from earlier chapters: a flag test is only valid against the server whose command line actually carries the flag, and the next check must use the restarted server.

## Comparison table

| | Before (limit 20, no pages) | After (cursor pagination) |
|---|---|---|
| Reachable posts | 20 of 2017 | All 2017, in 101 pages |
| Cost of last page | Scan + sort + skip 2000 (2017 docs examined) | Range seek (18 docs examined) |
| New post during paging | Page numbers shift; duplicates/skips possible | Cursor unaffected; new post appears on next full reload |
| Cost growth | Grows with page number | Flat, every page costs ~one page |
| Old behaviour available | — | Flag off restores list shape, limit 20 |
| Price paid | — | Response shape changed (list → object); client must follow `next_cursor`; no random page jump ("go to page 47" is not possible with cursors) |

## Limitations (stated, not hidden)

- Cursor pagination has no random access: "jump to page 47" does not exist. If a UI needs page numbers, offset is the tool — at the cost above.
- The cursor is opaque but unsigned. Tampering with it cannot leak private data here, because the feed is public; on a private feed, cursors should be validated or signed.
- This chapter does not change the Chapter 16 follow-up: deleting a post still leaves its Redis HLL key `uv:post:<id>` behind (invisible once the post leaves the feed, but the key remains).
- Seed posts were deleted before close; the 2017-post measurements above are recorded history, not current database state. The current database holds the original 17 posts.

## CLOSED

Code + docs + tag + Notion page, fetch-verified in chat order.

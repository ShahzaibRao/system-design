# Chapter 18 — Caching (Feed Cache, Cache-Aside + Write Invalidation)

## What this chapter builds on, and what it unlocks next

- Builds on Chapter 13 (Redis already in the stack, fail-open pattern: Redis down must never break the app) and Chapter 17 (paginated feed — every page gets its own cache key).
- Unlocks the Followers chapter (last chapter, a feature): a followed-only feed will reuse this same cache pattern, one key per page, invalidated on write.

## Concept — Cache-aside, TTL, invalidation

Cache-aside means the application, not the database, manages the cache. On a request the app first looks in Redis:

- Found → return it directly (HIT). Mongo is never touched.
- Not found → load from Mongo, store the answer in Redis, then return it (MISS). The next identical request is a HIT.

Two more mechanisms keep the cache honest:

- **TTL (time to live):** every cached answer carries an expiry (here 60 seconds by default). Even if invalidation misses something somewhere, no answer can stay stale longer than the TTL. TTL is the safety net, not the main freshness mechanism.
- **Invalidation on write:** when data changes, the app deletes the affected cache keys immediately, so the next read is fresh. Without this, a newly created post would stay invisible until the TTL expired.

Deliberate tradeoff, decided before the patch: **views are allowed to go stale for up to the TTL, but create/like/comment/delete invalidate immediately.** Views change on every feed load — invalidating on every view would empty the cache on every load and the cache would never produce a HIT. A views number lagging by up to 60 seconds is acceptable; a new post staying invisible, a like count staying wrong, or a deleted post staying visible is not.

## Step 1 — Baseline (no cache)

Seed, same proven pattern as Chapter 17 (direct Mongo insert, test data only, deleted before close):

- Inserted: 2000, total posts: 2017
- Seed shape: users cycle ali/bob/raoshahzaib, created_at one minute apart, likes 0, views 0.

Baseline latency, 50 requests to /api/v1/feed?limit=20, no cache:

- Requests: 50, avg 13.01 ms, p50 12.2 ms, p95 14.14 ms, min 11.16 ms, max 46.76 ms, posts in response: 20

WHY the baseline is not just "one Mongo query": every feed request runs the Mongo query AND one Redis PFCOUNT per post for unique views (20 PFCOUNTs for a 20-post page, Chapter 13). The cache skips both.

Patch anchors found in Step 1: Response was already imported (fastapi.responses, line 11), redis_client is a module-level global (lines 163–166, decode_responses=True, socket_connect_timeout=2), json was NOT imported (patch adds it). Endpoints: create_post 412, get_feed 442, like_post 468, comment_post 479, delete_post 496, view_post 507.

## Step 2 — Backend patch

File: apps/social-lab/backend/app/main.py. Frontend needed no change — the response shape is unchanged, and the X-Cache header is diagnostic only.

BEFORE (imports):

```python
import hashlib
```

AFTER:

```python
import hashlib
import json
```

BEFORE (flags, after CSP_POLICY):

```python
CSP_POLICY = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
```

AFTER:

```python
CSP_POLICY = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
FEED_CACHE_ENABLED = os.environ.get("FEED_CACHE_ENABLED", "true").lower() == "true"
FEED_CACHE_TTL_SECONDS = int(os.environ.get("FEED_CACHE_TTL_SECONDS", "60"))
```

New helpers (before get_feed):

```python
def _feed_cache_key(limit, cursor):
    return "feed:page:" + str(limit) + ":" + (cursor if cursor else "first")


def invalidate_feed_cache():
    if redis_client is None:
        return
    try:
        for key in redis_client.scan_iter("feed:page:*"):
            redis_client.delete(key)
    except Exception:
        pass
```

WHY the key shape: one key per page (limit + cursor). Page 1 and page 2 are different cache entries, so Load More pages are cached independently. The first page key ends in "first" instead of an empty cursor.

WHY scan-and-delete invalidation: the number of feed pages is small, so deleting every feed:page:* key on write is cheap and simple. A generational key (version number in the key) would avoid the scan, but is harder to reason about at this stage — recorded as a future idea, not built.

BEFORE (get_feed, Chapter 17 version):

```python
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

AFTER (cached get_feed):

```python
@app.get("/api/v1/feed")
def get_feed(response: Response, limit: int = 20, cursor: str | None = None):
    if not CURSOR_PAGINATION_ENABLED:
        posts = posts_collection.find().sort("created_at", -1).limit(20)
        result = [post_to_dict(post) for post in posts]
        for item in result:
            item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
        response.headers["X-Cache"] = "BYPASS"
        return result
    limit = max(1, min(limit, 100))
    cache_key = _feed_cache_key(limit, cursor)
    if FEED_CACHE_ENABLED and redis_client is not None:
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            return json.loads(cached)
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
    payload = {"posts": result, "next_cursor": next_cursor}
    if FEED_CACHE_ENABLED and redis_client is not None:
        try:
            redis_client.setex(cache_key, FEED_CACHE_TTL_SECONDS, json.dumps(payload))
        except Exception:
            pass
    response.headers["X-Cache"] = "MISS"
    return payload
```

WHY each part exists:

- **HIT path returns before any Mongo or PFCOUNT call.** That is the whole saving.
- **The cached value is the complete response payload** (posts + next_cursor), including the unique_views snapshot at cache time. So unique_views also lag up to the TTL — same accepted tradeoff as views.
- **Every Redis operation is wrapped in try/except (fail-open).** Redis down, or a cache call failing, degrades to a normal Mongo request. The cache can never break the feed. This is the Chapter 13 pattern, reused.
- **X-Cache header (HIT / MISS / BYPASS)** is the measurement instrument: it proves from outside whether Mongo was touched, without trusting timing alone. BYPASS means the pagination flag is off (old list path); a cache-flag-off request reports MISS, not BYPASS — see the flag test below.
- **response: Response parameter:** FastAPI recognises a Response-typed parameter as the response object, not a query parameter. No new import was needed because Response was already imported.

Invalidation calls (one line each, before the success return, in create_post, like_post, comment_post, delete_post):

```python
    invalidate_feed_cache()
    return post_to_dict(post)
```

```python
    invalidate_feed_cache()
    return {"message": "post deleted", "id": post_id}
```

WHY before the success return, and only there: the cache is cleared after the database write has happened, and only on success. A failed delete (403, not your post) does not clear the cache — there is nothing new to show. view_post was deliberately NOT touched, for the reason in the tradeoff above.

## Two patch bugs, honestly recorded (both were the assistant's, both caught before close)

Bug 1 — broken indentation and wrong insertion target, caught by py_compile:

- The first patch script picked the farther decorator instead of the nearest top-level definition when finding a function's block end. Result: invalidate_feed_cache() landed inside _feed_cache_key (breaking it), create_post got no invalidation at all, and every inserted line had 4 extra spaces (call at column 0, return at 8 spaces).
- py_compile reported: IndentationError, expected an indented block after function definition on line 444.
- Fix: restored the pre-patch file from /tmp/main.py.ch18.bak (diff confirmed empty), then re-ran a fixed script that takes the NEAREST top-level def/decorator as the block end and inserts exactly "    invalidate_feed_cache()" with no extra spaces. Re-verified: syntax OK, grep showed 5 lines (1 def + 4 call sites, all 4-space indented), _feed_cache_key body clean, create_post call present.

Bug 2 — dropped next_cursor line, caught by the missing header (runtime error, not syntax):

- The fixed patch's new get_feed built payload from next_cursor but dropped the line that computes it. Every MISS request raised NameError, returned 500, and set no X-Cache header. Evidence that pointed straight at it: health OK, create worked ("Post created" shown), but both feed curls printed no X-Cache line at all, and the browser sat on "Loading feed..." forever (loadFeed has no error handler).
- py_compile cannot catch this — it is a runtime error, not a syntax error. Lesson: a green syntax check is not a working endpoint; the header check is the real proof.
- Fix: one line restored before payload (line 491: next_cursor = _make_cursor(docs[-1]) if has_more and docs else None). Re-verified: syntax OK, MISS then HIT, posts 20, next_cursor present True, browser feed loads with the newest post on top.

## Step 3 — Measurements

Cache on, same 50-request script as baseline:

- Requests: 50, avg 3.48 ms, p50 2.76 ms, p95 3.19 ms, min 2.51 ms, max 35.97 ms, posts in response: 20

Comparison table (same script, same data, 2017 posts):

| | No cache (baseline) | Cache on (1 MISS + 49 HIT) |
|---|---|---|
| Avg | 13.01 ms | 3.48 ms (~3.7x faster) |
| p50 | 12.2 ms | 2.76 ms |
| p95 | 14.14 ms | 3.19 ms (~4.4x faster) |
| Max | 46.76 ms | 35.97 ms (outlier; the MISS request does the full Mongo + PFCOUNT work) |

Reading the numbers honestly: the average is HIT-dominated (49 of 50 requests never touched Mongo), so it measures HIT latency, not "the feed is 3.7x faster for every request". A single MISS still costs the full baseline price. The cache wins when the same page is requested repeatedly — which is exactly what a feed is.

Header verification (same URL, limit 20):

- First request: x-cache: MISS
- Second request: x-cache: HIT

TTL expiry test (server run with FEED_CACHE_TTL_SECONDS=5):

- First request: MISS, second request: HIT, after sleep 6: MISS again — the entry expired on its own and the next request went back to Mongo. TTL is a working safety net, not just a configured number.

Flag test (server run with FEED_CACHE_ENABLED=false):

- Both requests: x-cache: MISS, never HIT — the flag disables both cache reads and writes. Note precisely: flag-off reports MISS, not BYPASS. BYPASS only appears when the pagination flag (CURSOR_PAGINATION_ENABLED) is off and the old list path runs.

Browser verification:

- New post created → appears at the top of the feed immediately, no 60-second wait (create invalidation works).
- Like works → likes count updates on reload immediately (like invalidation works).
- Load More pages are separate cache keys, so paging after caching still returns the correct next pages.

Cleanup (seed posts were test-only, by agreement before seeding):

- delete_many with content regex ^seed post executed; seeds deleted (user-confirmed). After cleanup the feed shows only the user's real posts — browser test posts created during this chapter were kept, because they are the user's own data and were not part of the seed agreement.

## Comparison table

| | Before (no feed cache) | After (cache-aside + invalidation) |
|---|---|---|
| Feed request cost | Mongo query + 20 PFCOUNTs every time | One Redis GET on HIT |
| 50-request avg / p95 | 13.01 / 14.14 ms | 3.48 / 3.19 ms |
| Freshness of new post / like / comment / delete | Always fresh (no cache) | Fresh, via immediate invalidation |
| Freshness of views / unique views | Always fresh | Lags up to TTL (60 s) — accepted tradeoff |
| Redis down | Feed unaffected (no cache existed) | Feed unaffected (fail-open to Mongo) |
| Price paid | — | Extra Redis storage per page; invalidation scans feed:page:* keys on every write; cached answers can be up to TTL old |

## Limitations (stated, not hidden)

- Invalidation deletes by scanning feed:page:* keys. Fine while pages are few; at very large page counts a generational key or per-page tracking would scale better.
- The cached payload includes the unique_views snapshot, so unique views also lag up to the TTL, not just total views.
- X-Cache is a diagnostic header, not a security boundary — it only reports what happened.
- TTL 60 s and the scan-based invalidation are demo-scale choices; production tuning would measure real hit rates and write rates first.
- This chapter does not change the Chapter 16 follow-up: deleting a post still leaves its Redis HLL key uv:post:<id> behind.

## CLOSED

Code + docs + tag + Notion page, fetch-verified in chat order.

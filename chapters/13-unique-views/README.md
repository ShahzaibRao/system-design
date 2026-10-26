# Chapter 13 — Unique Views with Redis HyperLogLog

## What this chapter demonstrates

The feed used to show only total views. Total views count every view, so one user
reloading the feed inflates the number. This chapter adds unique views beside
total views, using a Redis HyperLogLog sketch, so a recruiter can see both numbers
live: how many times a post was viewed, and how many different people viewed it.

Total views are kept, not replaced. The two numbers answer different questions,
and showing them side by side is the point.

## Why Redis, and why not Mongo only

Redis is an in-memory data store: fast because it lives in RAM, unlike MongoDB,
which lives on disk and is permanent. Redis is not a replacement for the main
database and it does not copy the whole database automatically. The application
code decides what to put in Redis, when to put it, and when to throw it away.

What belongs in Redis: hot, small, derived, or temporary data — counters,
sketches, sessions with an expiry, rate-limit windows, caches. What stays in
MongoDB: the source of truth — users, posts, passwords, and here, total views.

A 100 GB database does not need 100 GB of RAM for Redis. Only the hot, small
part goes to Redis. Here that part is one HyperLogLog sketch per post, about
12 KB each, no matter whether 100 or 100,000 people viewed the post. For 1,000
posts that is roughly 12 MB, not gigabytes.

Redis has no tables, rows, columns, SQL, joins, or enforced schema. Its design
is key plus data structure: String, Hash, Set, Sorted Set, HyperLogLog, and
others. A Hash can look like a row (fields inside one key), but there is no
query language over fields and no foreign keys. "Database design" in Redis
means choosing key names, structures, and expiries — here, `uv:post:<post_id>`
with a HyperLogLog structure.

Two Redis patterns, kept distinct in this chapter:

- Cache-aside (fetch): check Redis first (hit, return it), on a miss read MongoDB,
  then store the result in Redis with a TTL. The hit/miss logic lives in
  application code; Redis never fetches from MongoDB by itself.
- Native store (this chapter's unique views): the sketch itself lives in Redis.
  A view runs `PFADD`, a feed read runs `PFCOUNT`. There is no MongoDB copy of
  the sketch, and no cache-aside miss path for it. MongoDB holds total views
  separately, by its own write.

HyperLogLog does not store names. It stores a small sketch of hashes, so it can
estimate how many distinct items were added, with about 0.81% error, but it
cannot list who they were. That is both its memory win and its limit. An earlier
measurement in the linkshortener project showed the same tradeoff: an estimate
of 18,521 against an actual 18,516 (0.027% error), in 14,392 bytes against
731,184 bytes for an exact set (about 51x less).

## Step 1 — Baseline: total views inflate on repeat views

Before any patch, the same user viewing the same post repeatedly was measured:

```
login ali
GET /api/v1/feed, first post -> views 8
POST /api/v1/posts/<id>/view, 5 times, same user ali
GET /api/v1/feed, same post -> views 13
```

Result: 8 to 13, plus 5, same post id, same user. Total views cannot tell one
person viewing five times apart from five people viewing once. That is the
problem unique views solves, and the baseline the enhanced version is measured
against.

## Step 1b — Redis hands-on before the patch

Redis was new to this project in practice: it had been used through code in the
linkshortener project (`PFADD` / `PFCOUNT`), but never set up or driven by hand.
So before the patch, Redis was driven by hand in a throwaway container
(`social-lab-redis-demo`, removed before the real container was started, so its
port 6379 would not clash):

```
docker run -d --name social-lab-redis-demo -p 6379:6379 redis:7
docker exec social-lab-redis-demo redis-cli ping        # PONG
docker exec social-lab-redis-demo redis-cli SET greeting "hello redis"  # OK
docker exec social-lab-redis-demo redis-cli GET greeting # hello redis
docker exec social-lab-redis-demo redis-cli PFADD demo:uv ali          # 1
docker exec social-lab-redis-demo redis-cli PFADD demo:uv ali ali rao  # 1
docker exec social-lab-redis-demo redis-cli PFCOUNT demo:uv            # 2
```

Reading: the second `PFADD` returns 1 because `rao` is new, even though `ali`
was added again. `PFCOUNT` returns 2, not 4: `ali` added three times still counts
once. A duplicate does not change the sketch, because the same name hashes to
the same sketch marks.

## Step 2 — The patch

### Design

- Every view does **two writes**, not one:
  1. MongoDB: `$inc` total `views` by 1 (permanent, exact).
  2. Redis: `PFADD uv:post:<post_id> <username>` (sketch, fast, approximate).
- `PFADD` alone never writes to MongoDB. If only `PFADD` ran, MongoDB would
  never learn a view happened and total views would freeze. The dual write is
  what keeps both numbers true.
- The feed reads MongoDB posts, then enriches each post with `PFCOUNT` as
  `unique_views`.
- The view endpoint is no longer public. It now requires a session cookie and
  a CSRF header, because it now needs an identity (whose view is this?) and
  because Chapter 12's rule is that an authenticated POST needs a CSRF header.
  The Chapter 12 view exemption ended exactly when the view stopped being
  public. A view without a header now returns 403 `csrf token missing`.
- Feature flag: `UNIQUE_VIEWS_ENABLED` (default true). When false, the feed
  returns `unique_views: null` and the frontend hides the unique part, showing
  only total views — the simple version, toggleable live.
- Fail-open: if Redis is down, a view still succeeds (MongoDB total increments,
  the `PFADD` is skipped), and the feed returns `unique_views: null` instead of
  breaking or showing a misleading 0. A missed unique view undercounts slightly;
  total views stay correct.

### Docker Compose — `apps/social-lab/docker-compose.yml`

A `redis` service was added beside mongo, prometheus, and grafana, with
append-only persistence so a container restart reloads the sketch from disk:

```yaml
  redis:
    image: redis:7
    container_name: social-lab-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: ["redis-server", "--appendonly", "yes"]
    restart: unless-stopped
```

And `redis_data:` was added under `volumes:`. The demo container from Step 1b
had to be stopped and removed first, otherwise the new container could not bind
port 6379.

### Backend — `apps/social-lab/backend/app/main.py`

Requirements gained `redis` (`backend/requirements.txt`, installed into
`backend/.venv` with pip — the file and the install were both updated, so a
fresh machine gets it too).

Redis setup and helpers, placed before `/health`, fail-safe at startup (if
Redis is unreachable at import time, `redis_client` is `None` and the app still
starts):

```python
import redis

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
UNIQUE_VIEWS_ENABLED = os.getenv("UNIQUE_VIEWS_ENABLED", "true").lower() == "true"
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2)
    redis_client.ping()
except Exception:
    redis_client = None


def track_unique_view(post_id, username):
    if not UNIQUE_VIEWS_ENABLED or redis_client is None:
        return
    try:
        redis_client.pfadd("uv:post:" + post_id, username)
    except Exception:
        pass


def get_unique_views(post_id):
    if not UNIQUE_VIEWS_ENABLED or redis_client is None:
        return None
    try:
        return redis_client.pfcount("uv:post:" + post_id)
    except Exception:
        return None
```

Feed enrichment (before, then after):

```python
# BEFORE
def get_feed():
    posts = posts_collection.find().sort("created_at", -1).limit(20)
    return [post_to_dict(post) for post in posts]

# AFTER
def get_feed():
    posts = posts_collection.find().sort("created_at", -1).limit(20)
    result = [post_to_dict(post) for post in posts]
    for item in result:
        item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
    return result
```

View endpoint (before, then after). Note the order: authenticate, then CSRF,
then MongoDB increment, then Redis `PFADD`, so total views are never lost
because Redis failed:

```python
# BEFORE
def view_post(post_id: str):
    post = get_post_or_404(post_id)
    posts_collection.update_one({"_id": post["_id"]}, {"$inc": {"views": 1}})
    post = posts_collection.find_one({"_id": post["_id"]})
    return post_to_dict(post)

# AFTER
def view_post(post_id: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    posts_collection.update_one({"_id": post["_id"]}, {"$inc": {"views": 1}})
    track_unique_view(post_id, username)
    post = posts_collection.find_one({"_id": post["_id"]})
    result = post_to_dict(post)
    result["unique_views"] = get_unique_views(post_id) if UNIQUE_VIEWS_ENABLED else None
    return result
```

The patch was applied with exactly-once patch scripts (backend
`/tmp/ch13_step02_backend_patch.py`, backup `/tmp/main.py.ch13.bak`; compose
`/tmp/ch13_compose_patch.py`, backup `/tmp/docker-compose.ch13.bak`). The full
`git diff` was verified and `py_compile` reported `syntax OK`.

### Frontend — `apps/social-lab/frontend/feed.js`

Render (line 40-41 after the patch, string concatenation only, no template
literals, per the Chapter 08 frontend rule):

```javascript
var uniqueText = (post.unique_views !== null && post.unique_views !== undefined) ? (' | ' + post.unique_views + ' unique views') : '';
metaDiv.textContent = post.likes_count + ' likes | ' + post.views + ' views' + uniqueText + ' | ' + post.comments.length + ' comments';
```

When `unique_views` is `null` (flag off, or Redis down), the unique part is
hidden and only total views show. View fetch (line 93 after the patch):

```javascript
fetch('/api/v1/posts/' + post.id + '/view', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': getCsrfToken() } });
```

The frontend patch was applied with an exactly-once script
(`/tmp/ch13_step02_frontend_patch.py`, backup `/tmp/feed.js.ch13.bak`), verified
by grep: render unique_views found 1, view fetch auth csrf found 1.

### Backend curl verification

Post under test: `6ade0898b1def89dd62e4a4b`, ali, "with csrf test". In order:

```
login ali -> {"message":"login successful","username":"ali"}
feed, first post -> id 6ade0898b1def89dd62e4a4b, views 14, unique_views 0
view, no header -> {"detail":"csrf token missing"}
view, correct header -> views 15, unique_views 1
feed -> views 15, unique_views 1
view again, same user ali, correct header -> views 16, unique_views 1
feed -> views 16, unique_views 1
```

Reading: total went 14 to 15 to 16 (plus 1 per view), unique went 0 to 1 and
stayed 1 on the repeat view by the same user. The 0 before the first patched
view is expected: `PFCOUNT` on a key that does not exist yet returns 0.

### Browser verification

After a hard refresh (Ctrl+Shift+R, the Chapter 11 cache lesson again), the feed
showed, for the same post:

```
0 likes | 40 views | 3 unique views | 0 comments
```

The user logged in as three different users and refreshed: total views kept
climbing to 40, unique views stayed at 3. Three different people, the rest
repeat views — exactly the distinction total views alone cannot make.

## Step 3 — Flag test and comparison

Flag off, uvicorn restarted from the backend folder with
`UNIQUE_VIEWS_ENABLED=false`:

```
curl feed, first post -> id 6ade0898b1def89dd62e4a4b, views 49, unique_views None
```

Browser: the unique part disappeared from the feed, only total views showed.
Normal restart (flag on, the default) brought unique views back — the Redis
data was only hidden by the flag, not deleted by it.

### Comparison

| Check | Before (total only) | After (total + unique) |
|---|---|---|
| Same user, 5 views (baseline) | Total 8 to 13, no unique number at all | Total still plus 1 per view, unique plus 1 only on the first view |
| Same user, 2 curl views | Total plus 2 | Total 14 to 16, unique 0 to 1, then still 1 |
| 3 different users, many refreshes (browser) | Total 40, no unique number | Total 40, unique 3 |
| View without CSRF header | Succeeded (public endpoint) | 403 `csrf token missing` |
| Flag off (`UNIQUE_VIEWS_ENABLED=false`) | Not applicable | `unique_views: null`, unique part hidden, only total shows |
| Flag on again | Not applicable | Unique views return, Redis data intact |
| Redis down | Not applicable | View still succeeds, total increments, unique returns `null` |

## Limitations (honest)

- Unique views are an **estimate**, not an exact count (HyperLogLog error about
  0.81%). The UI shows the estimate; this README states the error.
- If Redis data is lost without persistence (for example, the volume is
  deleted), unique counts restart from 0 and only rebuild as people view again.
  People who do not re-view are never re-counted. Append-only persistence
  protects against a container restart, not against volume loss. Total views
  in MongoDB are unaffected by any Redis loss.
- A future improvement, not in this chapter: a periodic job that snapshots
  `PFCOUNT` into MongoDB, so a Redis loss costs only the views since the last
  snapshot, not the whole history.
- Total views still inflate on refresh, by definition. That is not fixed here
  and is not meant to be: total and unique are shown together precisely so the
  inflation is visible instead of hidden.
- The view endpoint is now authenticated. Anonymous view counting, if ever
  wanted, would need a different identity (for example, an IP-based sketch,
  with its own accuracy problems behind shared networks) and is out of scope.

## Troubleshooting notes from this chapter

- A `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` from a curl
  piped to Python means the server returned an empty response, not bad JSON.
  `curl -s` hides connection errors, so check with `curl -i` and the server log
  first. In this chapter the cause was uvicorn started from the wrong folder:
  `ModuleNotFoundError: No module named 'app'`, because `app.main:app` only
  resolves when the current folder is `backend`. Kill the process, `cd` to
  `backend`, restart uvicorn, and `/health` returns 200 again. The patch itself
  was already correct; the diff proved it before the restart.
- A demo Redis container holding port 6379 blocks the Compose Redis container
  from binding the same port. Stop and remove the demo container before
  `docker compose up -d redis`.
- After any frontend change, hard-refresh (Ctrl+Shift+R). A cached old script
  calling the view endpoint without a CSRF header looks exactly like a broken
  patch.
- Deliver files and scripts as commands (for example a heredoc command), not as
  a bare code block with no creation command. That delivery preference was
  corrected during Chapter 12 and applied throughout this chapter.

## Next steps (candidates, not started)

- Followers plus a followed-only feed.
- Refresh tokens with a shorter session lifetime.
- CSP headers.
- A periodic unique-views snapshot from Redis into MongoDB, so Redis loss costs
  only the interval since the last snapshot.

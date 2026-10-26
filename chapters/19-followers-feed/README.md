# Chapter 19: Followers + Followed-Only Feed

**Track ki aakhri chapter.** Ye aik feature chapter hai, security lesson nahi. Is ka aik system-design concept hai: **fan-out** — pull (read par) vs push (write par).

**Builds on:** Ch10 (sessions/auth), Ch12 (CSRF), Ch13 (Redis + unique views), Ch17 (cursor pagination), Ch18 (feed cache).

---

## Step 1 — Baseline (verified)

- `grep -n "follow" app/main.py` → koi match nahi.
- `POST /api/v1/users/bob/follow` → **404** (route hi nahi thi).
- `follows` collection maujood nahi thi.
- Posts: ali **7**, bob **0**, raoshahzaib **8**, total **22**.

Matlab: follow ka koi tasawwur app me nahi tha — sab kuch naya banana tha.

---

## Step 2a — Backend: follow endpoints + followed feed (pull fan-out)

### Flag

```python
FOLLOW_ENABLED = os.environ.get("FOLLOW_ENABLED", "true").lower() == "true"
```

Flag off ho to chaaron follow endpoints 404 dete hain (refresh-token flag wala pattern).

### Collection + unique index

```python
follows_collection = posts_collection.database["follows"]
try:
    follows_collection.create_index([("follower", 1), ("following", 1)], unique=True)
except Exception:
    pass
```

Unique index ka matlab: aik user aik user ko do dafa follow nahi kar sakta, database level par. Index import par try/except me hai taake Mongo aik lamhe ke liye unreachable ho to app crash na kare — upsert filter waise bhi idempotent hai.

### Helper

```python
def _get_following(username):
    return [doc["following"] for doc in follows_collection.find({"follower": username})]
```

### Endpoints (full code)

```python
@app.post("/api/v1/users/{username}/follow")
def follow_user(username: str, request: Request):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(request)
    if current is None:
        raise HTTPException(status_code=401, detail="not logged in")
    validate_csrf(request)
    if username == current:
        raise HTTPException(status_code=400, detail="cannot follow yourself")
    if users_collection.find_one({"username": username}) is None:
        raise HTTPException(status_code=404, detail="user not found")
    follows_collection.update_one(
        {"follower": current, "following": username},
        {"$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True,
    )
    invalidate_feed_cache()
    return {"following": username, "status": "following"}


@app.post("/api/v1/users/{username}/unfollow")
def unfollow_user(username: str, request: Request):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(request)
    if current is None:
        raise HTTPException(status_code=401, detail="not logged in")
    validate_csrf(request)
    if username == current:
        raise HTTPException(status_code=400, detail="cannot follow yourself")
    if users_collection.find_one({"username": username}) is None:
        raise HTTPException(status_code=404, detail="user not found")
    follows_collection.delete_one({"follower": current, "following": username})
    invalidate_feed_cache()
    return {"following": username, "status": "not_following"}


@app.get("/api/v1/following")
def list_following(request: Request):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(request)
    if current is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return {"following": _get_following(current)}
```

**IDOR note:** `/api/v1/following` me koi user parameter nahi hai — username sirf cookie se aata hai, is liye koi doosre ki following list nahi dekh sakta.

### Followed feed — pull fan-out (full code, concept ka core)

```python
@app.get("/api/v1/feed/following")
def get_following_feed(request: Request, response: Response, limit: int = 20, cursor: str = None):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(request)
    if current is None:
        raise HTTPException(status_code=401, detail="not logged in")
    following = _get_following(current)
    cache_key = "feed:following:" + current + ":" + str(limit) + ":" + (cursor or "first")
    if FEED_CACHE_ENABLED:
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            return json.loads(cached)
    query = {"username": {"$in": following}}
    if CURSOR_PAGINATION_ENABLED:
        limit = max(1, min(limit, 100))
        if cursor:
            try:
                cursor_time_str, cursor_id_str = cursor.rsplit("_", 1)
                cursor_time = datetime.fromisoformat(cursor_time_str)
                cursor_id = ObjectId(cursor_id_str)
            except Exception:
                raise HTTPException(status_code=400, detail="invalid cursor")
            query = {"$and": [query, {"$or": [
                {"created_at": {"$lt": cursor_time}},
                {"created_at": cursor_time, "_id": {"$lt": cursor_id}},
            ]}]}
        docs = list(posts_collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(limit + 1))
        has_more = len(docs) > limit
        docs = docs[:limit]
        next_cursor = _make_cursor(docs[-1]) if has_more and docs else None
        payload = {"posts": [post_to_dict(d) for d in docs], "next_cursor": next_cursor}
        for post in payload["posts"]:
            post["unique_views"] = get_unique_views(str(post["id"])) if UNIQUE_VIEWS_ENABLED else None
        if FEED_CACHE_ENABLED:
            try:
                redis_client.setex(cache_key, FEED_CACHE_TTL_SECONDS, json.dumps(payload))
            except Exception:
                pass
            response.headers["X-Cache"] = "MISS"
        return payload
    docs = list(posts_collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(20))
    response.headers["X-Cache"] = "BYPASS"
    return [post_to_dict(d) for d in docs]
```

**WHY pull:** query read ke waqt chalti hai — `username IN following`. Write sasta hai (sirf aik follows row), read ki cost following count + posts ke saath barhti hai. Push (fan-out on write) is ka ulta hota: har nayi post har follower ke inbox me copy hoti — read tez, write mehnga + storage + background workers. Chhote/medium app ke liye pull sahi hai; Twitter scale par push jeet-ta hai (comparison table neeche).

**Cache key me username is liye hai** taake aik user ki followed feed doosre user ko kabhi na mile — per-user key, warna ye aik privacy bug hota.

**Invalidation widened (Ch18 change):** `scan_iter("feed:page:*")` → `scan_iter("feed:*")`, taake followed-feed keys bhi clear hon. `uv:post:*` keys is se mutasir nahi hoti (alag prefix).

### Step 2a test (verified, curl)

Test posts seed kiye (direct Mongo, test-data setup): bob ×2, ali ×1. Phir ali login:

- Follow bob → `{"following":"bob","status":"following"}`; dobara follow → wahi jawab (koi duplicate nahi).
- `GET /api/v1/following` → `{"following":["bob"]}`.
- Followed feed → **2 posts, authors sirf `['bob']`** — ali ki apni `followtest` post maujood nahi thi, filtering proven.
- Self-follow → 400 `cannot follow yourself`; `ghostuser` → 404 `user not found`.
- Unfollow bob → `not_following`; followed feed → **0 posts**.

---

## Step 2b — Frontend: mode toggle + Follow button

### State

```javascript
let feedMode = 'all';
let followingSet = {};
```

### loadFollowingSet + mode-aware loadFeed/loadMore

```javascript
function loadFollowingSet() {
  return fetch('/api/v1/following', { credentials: 'same-origin' })
    .then(function (res) { return res.ok ? res.json() : { following: [] }; })
    .then(function (data) {
      followingSet = {};
      (data.following || []).forEach(function (u) { followingSet[u] = true; });
      return loadFeed(true);
    })
    .catch(function () { followingSet = {}; return loadFeed(true); });
}
```

`loadFeed` aur `loadMore` ab mode dekhte hain: `feedMode === 'following'` ho to URL `/api/v1/feed/following` hota hai, warna `/api/v1/feed`. Dono me `credentials: 'same-origin'` explicit hai (followed feed ko login cookie chahiye; public feed par koi behaviour change nahi).

### Follow button (renderPost me, Like ke baad)

Sirf tab dikhta hai jab `post.username !== currentUsername` (apni post par Follow ka koi matlab nahi). Text `followingSet` se: `Unfollow` agar already following, warna `Follow`.

### Toggle buttons (feed.html, message div ke baad)

```html
<button id="all-feed-button">All Posts</button>
<button id="following-feed-button">Following</button>
```

`initFeed` ab seedha `loadFeed(true)` ki bajaye `loadFollowingSet()` call karta hai (taake button ke text pehle se durust hon).

### Step 2b browser test (verified)

ali login: Following mode me followed users ki posts, All mode me sab ki posts (apni bhi), Follow dabate hi Unfollow ho jata hai. "sb feature kaam krty hain."

---

## Do observations + un ki wajah (koi fix nahi tha, sirf explanation)

**1. Follow/unfollow par views barh jaate the.** Follow handler ke baad `loadFollowingSet()` → `loadFeed(true)` chalta tha, aur `renderPost` har rendered post par `/view` POST bhejta hai jab `incrementViews` true ho. To poora visible page +1 view le leta tha. Unique views nahi hilte the (HLL, username se dedup). Is app me "views" ka matlab hai "pehli-type load par kitni dafa dikhi", distinct readers nahi.

**2. Load More ke baad follow karne par feed page 1 par wapas aa jati thi.** `loadFeed` Ch17 design se hamesha list clear karke cursor page 1 par reset karta hai (taake list aadhi purani aadhi nayi na rahe). Like/comment/delete bhi `loadFeed(false)` se wahi reset karte the — sirf view counting ka farq tha.

Dono observations ki jar aik hi thi: full reload. Is liye Step 2c bana.

---

## Step 2c — Targeted in-place updates (frontend only, backend untouched)

### BEFORE (har action full reload karta tha)

```javascript
// Like:
}).then(function () { loadFeed(false); });
// Delete:
}).then(function () { loadFeed(false); });
// Comment:
}).then(function () { loadFeed(false); });
// Follow:
}).then(function () { loadFollowingSet(); });
```

### AFTER

**Post ko pehchanne ke liye id + comments class (renderPost):**

```javascript
postDiv.id = 'post-' + post.id;
commentsDiv.className = 'post-comments';
```

**Meta update helper (naya function, renderPost se pehle):**

```javascript
function updatePostMeta(post) {
  var el = document.getElementById('post-' + post.id);
  if (!el) { return; }
  var meta = el.querySelector('.post-meta');
  if (!meta) { return; }
  var uniqueText = '';
  if (post.unique_views !== null && post.unique_views !== undefined) {
    uniqueText = ' | ' + post.unique_views + ' unique views';
  } else {
    var m = meta.textContent.match(/ \| [0-9]+ unique views/);
    if (m) { uniqueText = m[0]; }
  }
  meta.textContent = post.likes_count + ' likes | ' + post.views + ' views' + uniqueText + ' | ' + post.comments.length + ' comments';
}
```

WHY ye guard: Like ka jawab me `unique_views` nahi hota (wo sirf feed enrich karti hai). Baghair guard ke Like dabate hi unique views meta se ghaib ho jate — aik naya bug hota. Helper purani meta se unique wala hissa bacha leta hai.

**Like — sirf meta update:**

```javascript
}).then(function (res) { return res.ok ? res.json() : null; }).then(function (updated) { if (updated) { updatePostMeta(updated); } });
```

**Delete — sirf wo div ghaib:**

```javascript
}).then(function () { var el = document.getElementById('post-' + post.id); if (el) { el.remove(); } });
```

**Comment — meta update + comment append + input clear:**

```javascript
}).then(function (res) { return res.ok ? res.json() : null; }).then(function (updated) {
  if (!updated) { return; }
  updatePostMeta(updated);
  var box = document.getElementById('post-' + post.id).querySelector('.post-comments');
  var commentP = document.createElement('p');
  commentP.textContent = currentUsername + ': ' + text;
  box.appendChild(commentP);
  commentInput.value = '';
});
```

**Follow — button flip + Following mode me author ki posts remove:**

```javascript
}).then(function () {
  var wasFollowing = followingSet[post.username];
  followingSet[post.username] = !wasFollowing;
  followButton.textContent = followingSet[post.username] ? 'Unfollow' : 'Follow';
  if (feedMode === 'following' && !followingSet[post.username]) {
    var allPosts = postsDiv.querySelectorAll('.post');
    allPosts.forEach(function (el) {
      var a = el.querySelector('.post-author');
      if (a && a.textContent === post.username) { el.remove(); }
    });
  }
});
```

WHY ye safe hai: server ka following set unfollow par update ho chuka hai, is liye agla Load More (jo server se fresh set leta hai) unfollowed author ko wapas nahi layega. Cursor bhi valid rehta hai (cursor-based, removal se mutasir nahi).

**Full reload ab sirf** page kholne aur All/Following mode badalne par hota hai — wahan views barhna durust hai (pehli dafa dikh rahi hain).

### Step 2c verification (patch + browser)

- Patch: saaton `must()` found: 1, `node --check` OK, backup `/tmp/feed.js.ch19c.bak`.
- Browser: Load More ke baad Like/Comment/Follow/Unfollow/Delete par feed page 1 par wapas **nahi** jaati; views action par **nahi** barhte (pehle barhte the); Following mode me Unfollow par us author ki posts wahi se ghaib hoti hain.

---

## Step 3 — Final verification

### 3a. HLL cleanup on delete (Chapter 16 ka recorded follow-up, ab band)

Delete par Redis HLL key reh jati thi. Patch (delete_post me, `delete_one` ke baad):

```python
try:
    if redis_client is not None:
        redis_client.delete("uv:post:" + post_id)
except Exception:
    pass
```

Fail-open pattern (Ch13 wala): Redis down ho to delete phir bhi kamyab ho.

Test: post banayi, view ki (views 1, unique 1) → uv key exists **1** → delete → uv key exists **0**. Verified.

### 3b. Followed-feed cache + invalidation sequence (verified)

- Unfollow bob → followed feed: **MISS**, dobara: **HIT** (khaali feed cache ho gayi).
- Follow bob → followed feed: **MISS** (invalidation ne khaali wali cache saaf kar di — warna purani khaali HIT milti, aur bob ki posts nazar na aati).
- Posts: **7**, authors: bob, frontendtest1, rao — ye bug nahi hai: ali browser tests me in teenon ko follow kar chuka tha, aur ali ki apni posts list me nahi thi, to filtering durust hai.

### 3c. Flag test (verified)

`FOLLOW_ENABLED=false` server par: follow → 404, `/api/v1/following` → 404, `/api/v1/feed/following` → 404 — teenon `follows disabled`. Phir normal restart.

### 3d. Followed-feed explain — agle topic ke liye naap (fix ABHI nahi)

```python
db.posts.find({'username': {'$in': ['bob']}}).sort([('created_at', -1), ('_id', -1)]).limit(20).explain()
```

Result (cleanup se pehle): **docs examined 23, returned 2** — poora collection scan, kyun ke `username` par koi index nahi hai (sirf `created_at` par hai). Ye Ch19 me jaan kar nahi fix kiya gaya: yehi agle topic (DB indexing) ka measured starting problem hai.

### 3e. Cleanup (verified)

- `delete_many` content regex `^followtest` → deleted **3**, left **0**.
- Total posts: **20**. Follows docs: **4** — follows user data hai, is liye rakhe gaye hain (unfollow UI se kabhi bhi kiya ja sakta hai).
- `git status`: sirf 3 files modified — `backend/app/main.py`, `frontend/feed.js`, `frontend/feed.html`. Koi test file, seed ya jar commit nahi ho rahi.

---

## Comparison

### Pull (fan-out on read) vs Push (fan-out on write)

| | Pull (ye chapter) | Push (Twitter-scale) |
|---|---|---|
| Write cost | Sasta: sirf aik follows row | Mehnga: har follower ke inbox me copy |
| Read cost | Following + posts ke saath barhta hai | Tez: inbox pehle se tayyar |
| Storage | Sirf original post | Har follower ke liye copy |
| Freshness | Hamesha fresh (read par query) | Inbox update par depend |
| Extra system | Koi nahi | Background workers / queue (Kafka wala topic) |
| Kab jeet-ta hai | Chhote/medium app, normal following count | Celebrity: 100k followers, har post par |
| Is app me | ✅ Built | Concept table me |

### Full reload vs Targeted update (Step 2c)

| | Full reload (pehle) | Targeted update (ab) |
|---|---|---|
| Page position | Load More ke baad reset → page 1 | Barkarar |
| Views | Har action par poora page +1 | Sirf pehli load / mode switch par |
| Network | Poora page dobara | Sirf action ka jawab |
| Freshness | Poori list fresh | Sirf mutasra post fresh (mode switch par full reload ab bhi hai) |

---

## Limitations (imaandari se)

- **Nayi posts ka button nahi bana** (X-style "N new posts"): jaan kar skip kiya gaya — app refresh par nayi posts dikha deti hai, aur ye polish hai, concept nahi. Agar kabhi bana to X wala button hi banana hai, auto-insert nahi (auto-insert parhte hue content ko neeche dhakel deta hai — isi liye X ne button chuna).
- **Followed feed COLLSCAN karta hai** (23 examined, 2 returned) — username par index nahi hai. Ye agle topic (indexing) me fix hoga, yahan sirf naapa gaya hai.
- **Cursor opaque lekin unsigned hai** — public feed ke liye safe hai (koi private data nahi).
- **Random page jump nahi hai** ("page 47 par jao" cursors se mumkin nahi — Ch17 wali limitation).
- **Views/unique views cache TTL tak lag kar sakte hain** (Ch18 wali tradeoff, yahan bhi laagu).
- **Follows rows test state me hain** (4 docs) — user data samajh kar rakhe gaye hain.
- **Stray JS in `style.css` (lines 102–141)** Ch08 se wahi hai — non-executable, is chapter ka hissa nahi.

---

## Close

- Code: `backend/app/main.py`, `frontend/feed.js`, `frontend/feed.html`.
- Tag: `ch19-followers-feed` (docs commit par).
- Commit/tag/Notion hashes: Notion page par (fetch-verified).
- **Is ke saath decided track mukammal: Chapters 00–19 sab closed.** Agla topic: DB indexing deep-dive (compound/covered/explain), jis ka starting problem isi chapter me naapa gaya hai.

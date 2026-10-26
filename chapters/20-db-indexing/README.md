# Chapter 20: DB Indexing Deep-Dive

**Concept:** index query ko poori collection chhaanne se bachata hai — lekin sirf tab jab index ka order query ke filter + sort se mile.

**Builds on:** Ch03 (unique/TTL indexes), Ch09 (posts + `created_at` index), Ch17 (COLLSCAN vs index, explain), Ch19 (followed-feed query `$in` + sort — isi chapter ka naap is chapter ka starting problem tha).

**Unlocks:** replication + read/write split, phir sharding — shard key aur read scaling samajhne se pehle ye samajhna zaroori hai ke query asal me data tak kaise pahunchti hai.

---

## Step 1 — Baseline (verified)

### Indexes code me kahan bante hain

```python
users_collection.create_index("username", unique=True)
users_collection.create_index("email", unique=True)
posts_collection.create_index([("created_at", -1)])
sessions_collection.create_index("token_hash", unique=True)
sessions_collection.create_index("expires_at", expireAfterSeconds=0)
refresh_tokens_collection.create_index("token_hash", unique=True)
refresh_tokens_collection.create_index("expires_at", expireAfterSeconds=0)
used_refresh_tokens_collection.create_index("token_hash", unique=True)
used_refresh_tokens_collection.create_index("expires_at", expireAfterSeconds=0)
follows_collection.create_index([("follower", 1), ("following", 1)], unique=True)
```

WHY ye pattern achha hai: indexes app code me bante hain (startup par, idempotent `create_index`), is liye rebuild-proof hain — naya index bhi wahin lagna chahiye, haath se shell me nahi.

### Baseline explain (15 documents par)

```
posts indexes: ['_id_', 'created_at_-1']
followed feed - stage: SORT -> COLLSCAN | examined: 15 returned: 0
global feed - stage: SORT -> COLLSCAN | examined: 15 returned: 15
```

Do baatein:

1. **Followed feed COLLSCAN** — `username` par koi index nahi, to poori collection chhaanni parti hai, chahe kuch mile ya nahi (bob ke 0 posts the, phir bhi 15 examine hue).
2. **Global feed bhi COLLSCAN** — ye surprise tha: `created_at` index maujood hone ke bawajood. Wajah: sort **do fields** ka hai (`created_at`, `_id`) aur index **aik field** ka hai. Index sort tabhi bachata hai jab sort keys index ka **prefix** hon — yahan `_id` index me nahi, is liye planner ko scan + memory sort karna para.

Nuqta: 15 documents par planner waise bhi COLLSCAN chun sakta hai (chhoti collection par index ki cost faida se zyada hai). Is liye scale par naapna zaroori tha.

---

## Step 2 — Seed, baseline-at-scale, compound index, dobara naap

### 2a. Seed (test-only, 2000 posts)

```python
from pymongo import MongoClient
from datetime import datetime, timedelta
db = MongoClient('mongodb://localhost:27017')['social_lab']
users = ['ali', 'bob', 'raoshahzaib']
now = datetime.utcnow()
docs = []
for i in range(2000):
    docs.append({'username': users[i % 3], 'content': 'seed post %d' % i, 'created_at': now - timedelta(minutes=i), 'likes_count': 0, 'liked_by': [], 'comments': [], 'views': 0})
db.posts.insert_many(docs)
print('inserted:', len(docs), 'total:', db.posts.count_documents({}))
```

Output: `inserted: 2000 total: 2015`.

### 2b. Measure script (pehle aur baad, wahi script)

```python
from pymongo import MongoClient
db = MongoClient('mongodb://localhost:27017')['social_lab']
def show(label, cur):
    q = cur.explain()
    wp = q['queryPlanner']['winningPlan']
    stages = [wp.get('stage')]
    s = wp.get('inputStage')
    while s:
        stages.append(s.get('stage'))
        s = s.get('inputStage')
    print(label, '->', ' -> '.join(stages), '| examined:', q['executionStats'].get('totalDocsExamined'), 'returned:', q['executionStats'].get('nReturned'))
show('followed', db.posts.find({'username': {'$in': ['bob']}}).sort([('created_at', -1), ('_id', -1)]).limit(20))
show('global', db.posts.find({}).sort([('created_at', -1), ('_id', -1)]).limit(20))
show('covered', db.posts.find({'username': {'$in': ['bob']}}, {'username': 1, 'created_at': 1}).sort([('created_at', -1), ('_id', -1)]).limit(20))
```

### 2c. Baseline-at-scale (patch se pehle)

```
followed -> SORT -> COLLSCAN | examined: 2015 returned: 20
global -> SORT -> COLLSCAN | examined: 2015 returned: 20
covered -> PROJECTION_SIMPLE -> SORT -> COLLSCAN | examined: 2015 returned: 20
```

Projection se bhi koi faida nahi hua — jab index hi nahi, to projection bhi scan nahi bacha sakta.

### 2d. Compound index patch — BEFORE/AFTER

BEFORE (line 50):

```python
posts_collection.create_index([("created_at", -1)])
```

AFTER:

```python
posts_collection.create_index([("username", 1), ("created_at", -1), ("_id", -1)])
posts_collection.create_index([("created_at", -1), ("_id", -1)])
```

WHY ye order — ESR rule: **Equality pehle, phir Sort, phir Range.** Followed feed me `username` equality (`$in`) hai, phir sort (`created_at`, `_id`) usi order me — index ka order query ke order se milna chahiye.

WHY purana single-field index hata diya: wo naye compound ka **prefix** hai — compound index aik-field query bhi serve kar leta hai. Do alag index rakhna har write par double maintenance + storage hota.

Patch verify: `single index line found: 1`, `syntax OK`, backup `/tmp/main.py.ch20.bak`.

Note: uvicorn `--reload` mode me file change par app khud dobara load hui, aur `create_index` startup par chala — is liye indexes manual restart ke baghair ban gaye.

### 2e. Purana index drop + dobara naap

```
db.posts.drop_index('created_at_-1')
posts indexes now: ['_id_', 'username_1_created_at_-1__id_-1', 'created_at_-1__id_-1']
followed -> LIMIT -> FETCH -> IXSCAN | examined: 20 returned: 20
global -> LIMIT -> FETCH -> IXSCAN | examined: 20 returned: 20
covered -> LIMIT -> PROJECTION_COVERED -> IXSCAN | examined: 0 returned: 20
```

---

## Comparison

| Query | Pehle | Examined | Baad | Examined |
|---|---|---|---|---|
| Followed feed | SORT → COLLSCAN | 2015 | LIMIT → FETCH → IXSCAN | 20 |
| Global feed | SORT → COLLSCAN | 2015 | LIMIT → FETCH → IXSCAN | 20 |
| Covered query | PROJECTION_SIMPLE → SORT → COLLSCAN | 2015 | LIMIT → PROJECTION_COVERED → IXSCAN | 0 |

**Teen cheezein:**

1. **SORT ghaib:** index ka order sort ke order se mil gaya, to memory me sort ki zaroorat khatam.
2. **LIMIT upar:** pehle sort ke liye saare 2015 docs chahiye the, tab ja kar 20 kat-te the. Ab index order me chalte hue 20 milte hi ruk jata hai — isi liye examined 2015 se 20 hua (taqreeban 100x kam).
3. **FETCH vs covered:** followed/global ko poora document chahiye (content, likes...) — index sirf location deta hai, phir doc parhna parta hai, ye normal hai. Covered query me projection ke fields (`username`, `created_at`, `_id`) sab index me maujood hain, to document khole baghair hi jawab mil gaya — **index-only**, is liye examined 0.

**Index ki keemat (tradeoff):** har index har write ko mehnga karta hai (insert/update par index bhi update hota hai) + storage + memory. Is liye har field par index nahi lagate — sirf un queries par jo asal me chalti hain, aur unhi ke order me.

---

## Cleanup (verified)

```
deleted: 1999
seed left: 0
total posts now: 15
posts indexes: ['_id_', 'username_1_created_at_-1__id_-1', 'created_at_-1__id_-1']
```

Note: 2000 seed kiye the, 1999 delete hue — 1 seed post cleanup se pehle hi ja chuki thi (browser testing ke dauran delete hui). `seed left: 0` + total wapas 15 = cleanup mukammal. Indexes reh gaye (wo code ka hissa hain, test data nahi).

`git status`: sirf `M app/main.py` — koi test file ya seed commit nahi ho rahi.

---

## Limitations (imaandari se)

- Naap 2015 documents par hai (local laptop) — production scale par farq aur bhi bara hota hai, lekin pattern wahi hai.
- `create_index` default foreground build hai — 2000 docs par foran ban gaya; bari collection par ye writes rok sakta hai, wahan background/rolling build ka tareeqa hota hai (is chapter ka hissa nahi).
- Chhoti collection (15 docs) par planner COLLSCAN bhi chun sakta hai — index hona har dafa index use hone ki guarantee nahi hai, planner cost dekh kar faisla karta hai.
- Covered query sirf tab index-only hoti hai jab projection ke **saare** fields index me hon — aik field bhi bahar hua to FETCH wapas aa jayega.

---

## Close

- Code: `backend/app/main.py` (2 compound indexes, 1 single-field hata).
- Tag: `ch20-indexing` (docs commit par).
- Commit/tag/Notion hashes: Notion page par (fetch-verified).
- Agla topic: replication + read/write split.


# Chapter 22: App-level read/write split (poori tafseel — har command)

**Maqsad:** ye document aisa ho ke dobara kabhi yehi kaam karna ho to isi se ho jaye — har command, har output, sara code, chat ki tartib me, kuch missing nahi.

**Concept:** Ch21 me replication dikhayi thi (mongosh se), lekin app abhi bhi sirf aik mongod se jurti thi. Is chapter me app khud container me aayi, replicaSet wali URI par chali, aur driver ne reads/writes baantne shuru kiye — aur isi dauran aik asli bug mila (stale read → 500) jo measurement se pakra gaya aur fix hua.

**Builds on:** Ch21 (replica set: mongo1/mongo2/mongo3, PRIMARY/SECONDARY/ARBITER), Ch18 (feed cache), Ch13 (Redis), Ch12 (CSRF).

**Unlocks:** sharding (writes scale karna — is chapter ne sabit kiya ke writes primary par hi rehte hain).

## Pehle ke sawaal-jawab (is chapter ki bunyad)

Ch21 close ke baad ye confusion thi, jo isi chat me saaf hui:

- **App ko kaise pata chalta hai ke read kis DB par aur write kis DB par bhejni hai?** Replication ye faisla nahi karti. Faisla **app ka driver (PyMongo)** karta hai: operation ki qism se (find = read, insert/update/delete = write) aur **readPreference** setting se. Write hamesha primary par jata hai; read kahan se aaye, ye setting tay karti hai (primary / secondary / secondaryPreferred).
- **Replication kyun ki?** Donon ke liye: failover (aik DB band ho to doosri jagah le le, downtime kam) aur data redundancy (aik machine kharab ho to data doosri copy me zinda). Dono aik hi cheez ke do rukh hain.
- **Replication backup nahi hai.** Agar koi ghalti se posts delete kar de to delete bhi secondaries par copy ho jayega. Replication sirf machine kharab hone se bachata hai, ghalti ya corruption se nahi — us ke liye backup chahiye.
- **Read/write alag kyun?** Aksar sites par 90% reads aur 10% writes hoti hain. Saari reads aik primary se hon to us par load barhega aur response slow hoga. Reads secondaries par baant do to primary halka ho jata hai, app tez jawab deti hai.
- **Lekin imaandari wali baat:** Ch21 me app me read/write alag **nahi** hue the — sirf mongosh se dikhaya gaya tha. App ka connection sirf `localhost:27017` tha, baghair replicaSet ke. Is chapter ne wohi kami poori ki.

---

## Step 1 — Connection lines (koi code patch nahi chahiye tha)

`apps/social-lab/backend` se:

```
grep -n "MongoClient\|Redis\|redis\|StaticFiles\|mount\|27017\|6379" app/main.py
4:import redis
13:from fastapi.staticfiles import StaticFiles
16:from pymongo import MongoClient
38:MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
44:client = MongoClient(MONGO_URI)
67:app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
165:REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
168:    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2)
169:    redis_client.ping()
171:    redis_client = None
175:    if not UNIQUE_VIEWS_ENABLED or redis_client is None:
178:        redis_client.pfadd("uv:post:" + post_id, username)
184:    if not UNIQUE_VIEWS_ENABLED or redis_client is None:
187:        return redis_client.pfcount("uv:post:" + post_id)
452:    if redis_client is None:
455:        for key in redis_client.scan_iter("feed:*"):
456:            redis_client.delete(key)
472:    if FEED_CACHE_ENABLED and redis_client is not None:
474:            cached = redis_client.get(cache_key)
495:    if FEED_CACHE_ENABLED and redis_client is not None:
497:            redis_client.setex(cache_key, FEED_CACHE_TTL_SECONDS, json.dumps(payload))
543:        if redis_client is not None:
544:            redis_client.delete("uv:post:" + post_id)
617:    if FEED_CACHE_ENABLED and redis_client is not None:
619:            cached = redis_client.get(cache_key)
640:    if FEED_CACHE_ENABLED and redis_client is not None:
642:            redis_client.setex(cache_key, FEED_CACHE_TTL_SECONDS, json.dumps(payload))
```

```
grep -n "REDIS_HOST\|FRONTEND_DIR" app/main.py
66:FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
67:app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
164:REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
168:    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2)
210:    return FileResponse(FRONTEND_DIR / "signup.html")
```

Natija: Mongo aur Redis dono pehle se env se aate hain — is liye connection ke liye `main.py` me koi patch nahi chahiye tha, sirf compose me env vars set karni thi. `FRONTEND_DIR` = `parents[2] / "frontend"` — container me layout `/code/backend/app/main.py` + `/code/frontend` hona chahiye.

---

## Step 2 — App containerize + replicaSet URI

Pehle host par chalta hua uvicorn band kiya (Ctrl+C) — warna port 8000 par takrao hota. Phir `apps/social-lab` se:

`Dockerfile` (nayi file, poora code):

```dockerfile
FROM python:3.12-slim
WORKDIR /code
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
WORKDIR /code/backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

WHY bind mount sirf `backend/app` ka hai (poora backend nahi): host `.venv` image me nahi aana chahiye. `--reload` ke saath edit → reload wala tareeqa zinda rehta hai.

Compose patch script (poora code, `/tmp/ch21_app_patch.py`):

```python
p = "docker-compose.yml"
s = open(p).read()
open("/tmp/docker-compose.yml.ch21app.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must("  app:\n", 0)
must("  prometheus:\n", 1)
app_block = '''  app:
    build: .
    container_name: social-lab-app
    ports:
      - "8000:8000"
    volumes:
      - ./backend/app:/code/backend/app
      - ./frontend:/code/frontend
    environment:
      - MONGO_URI=mongodb://social-lab-mongo:27017,social-lab-mongo2:27017,social-lab-mongo3:27017/?replicaSet=rs0
      - REDIS_HOST=redis
    depends_on:
      - mongo
      - mongo2
      - mongo3
      - redis
    restart: unless-stopped

'''
s = s.replace("  prometheus:\n", app_block + "  prometheus:\n", 1)
open(p, "w").write(s)
print("app service added")
```

Patch chalane + build + verify ke commands:

```
python3 /tmp/ch21_app_patch.py
docker compose config --quiet && echo "compose syntax OK"
docker compose up -d --build app
docker ps --format "{{.Names}} {{.Status}}" | grep app
sleep 5
curl -s http://localhost:8000/health
curl -s -o /dev/null -w "feed http code: %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
```

Expectation: app container Up, health OK, feed 200 — app ab replicaSet wali URI par chal rahi hai. (Pehli build me pip install ki wajah se thora waqt lagta hai.)

**App-level failover proof (Ch21 ke 500 ke muqable me):**

```
docker stop social-lab-mongo
sleep 10
curl -s -o /dev/null -w "feed http code (mongo1 down): %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
feed http code (mongo1 down): 200
docker start social-lab-mongo
sleep 10
curl -s -o /dev/null -w "feed http code (mongo1 back): %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
feed http code (mongo1 back): 200
```

Ch21 me primary band hone par feed 500 hui thi (app ko failover ka pata nahi tha). Ab driver ne khud naya primary (mongo2) dhoond liya — app ka code wahi ka wahi. Yehi farq hai replicaSet wali URI ka.

---

## Step 3 — Baseline: primary-only reads (cache OFF)

Cache flag + k6 check (`backend` se):

```
grep -n "FEED_CACHE_ENABLED" app/main.py | head -5
26:FEED_CACHE_ENABLED = os.environ.get("FEED_CACHE_ENABLED", "true").lower() == "true"
472:    if FEED_CACHE_ENABLED and redis_client is not None:
495:    if FEED_CACHE_ENABLED and redis_client is not None:
617:    if FEED_CACHE_ENABLED and redis_client is not None:
640:    if FEED_CACHE_ENABLED and redis_client is not None:
k6 version
k6 v2.3.0 (commit/e088784614, go1.26.8, linux/amd64)
```

Load test ke liye `FEED_CACHE_ENABLED=false` lagaya — warna Redis hi jawab deta aur Mongo tak request nahi pahunchti. Patch script (poora code, `/tmp/ch21_cacheoff_patch.py`):

```python
p = "docker-compose.yml"
s = open(p).read()
open("/tmp/docker-compose.yml.ch21cache.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must("FEED_CACHE_ENABLED", 0)
must("      - REDIS_HOST=redis", 1)
s = s.replace("      - REDIS_HOST=redis\n", "      - REDIS_HOST=redis\n      - FEED_CACHE_ENABLED=false\n", 1)
open(p, "w").write(s)
print("cache off env added")
```

Cache OFF karne + verify ke commands:

```
python3 /tmp/ch21_cacheoff_patch.py
docker compose up -d app
sleep 5
curl -s -D - -o /dev/null "http://localhost:8000/api/v1/feed?limit=5" | grep -i "x-cache"
curl -s -D - -o /dev/null "http://localhost:8000/api/v1/feed?limit=5" | grep -i "x-cache"
```

Expectation: dono dafa `X-Cache: MISS` (kabhi HIT nahi) — matlab cache band hai, har request Mongo tak ja rahi hai.

Phir current primary + pehle counters:

```
docker exec social-lab-mongo mongosh --quiet --eval 'rs.status().members.forEach(function (m) { print(m.name, m.stateStr); })'
social-lab-mongo:27017 SECONDARY
social-lab-mongo2:27017 PRIMARY
social-lab-mongo3:27017 ARBITER
docker exec social-lab-mongo mongosh --quiet --eval 'print("mongo1 queries:", db.serverStatus().opcounters.query)'
mongo1 queries: Long('36')
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("mongo2 queries:", db.serverStatus().opcounters.query)'
mongo2 queries: Long('2028')
```

k6 script (poora code, `/tmp/ch21_read_load.js`):

```js
import http from 'k6/http';
export const options = { vus: 10, duration: '30s' };
export default function () {
  http.get('http://localhost:8000/api/v1/feed?limit=20');
}
```

```
k6 run /tmp/ch21_read_load.js
```

k6 summary (poora):

```
  █ TOTAL RESULTS

    HTTP
    http_req_duration..............: avg=78.81ms min=66.23µs med=77.3ms  max=188.45ms p(90)=93.63ms p(95)=101ms
      { expected_response:true }...: avg=78.81ms min=66.23µs med=77.3ms  max=188.45ms p(90)=93.63ms p(95)=101ms
    http_req_failed................: 0.00%  0 out of 3792
    http_reqs......................: 3792   130.361395/s

    EXECUTION
    iteration_duration.............: avg=79.15ms min=44.44ms med=77.45ms max=188.55ms p(90)=93.85ms p(95)=101.17ms
    iterations.....................: 3792   130.361395/s
    vus............................: 10     min=10        max=10
    vus_max........................: 10     min=10        max=10

    NETWORK
    data_received..................: 19 MB  648 kB/s
    data_sent......................: 341 kB 12 kB/s

running (0m29.1s), 00/10 VUs, 3792 complete and 0 interrupted iterations
```

Baad ke counters:

```
docker exec social-lab-mongo mongosh --quiet --eval 'print("mongo1 queries:", db.serverStatus().opcounters.query)'
mongo1 queries: Long('75')
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("mongo2 queries:", db.serverStatus().opcounters.query)'
mongo2 queries: Long('5881')
```

Baseline natija: primary (mongo2) **+3853**, secondary (mongo1) **+39**. Taqreeban tamam reads primary par gayin (default readPreference `primary`).

---

## Step 4 — Secondary reads (wahi test dobara)

Patch script (poora code, `/tmp/ch21_secondary_patch.py`):

```python
p = "docker-compose.yml"
s = open(p).read()
open("/tmp/docker-compose.yml.ch21sec.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must("secondaryPreferred", 0)
must("?replicaSet=rs0", 1)
s = s.replace("?replicaSet=rs0", "?replicaSet=rs0&readPreference=secondaryPreferred", 1)
open(p, "w").write(s)
print("secondaryPreferred added")
```

Patch + test ke commands:

```
python3 /tmp/ch21_secondary_patch.py
docker compose up -d app
sleep 5
curl -s -o /dev/null -w "feed http code: %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
docker exec social-lab-mongo mongosh --quiet --eval 'print("mongo1 queries:", db.serverStatus().opcounters.query)'
mongo1 queries: Long('77')
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("mongo2 queries:", db.serverStatus().opcounters.query)'
mongo2 queries: Long('5882')
k6 run /tmp/ch21_read_load.js
```

k6 summary (poora):

```
  █ TOTAL RESULTS

    HTTP
    http_req_duration..............: avg=77.1ms  min=180.75µs med=76.77ms max=172.62ms p(90)=89.52ms p(95)=93.92ms
      { expected_response:true }...: avg=77.1ms  min=180.75µs med=76.77ms max=172.62ms p(90)=89.52ms p(95)=93.92ms
    http_req_failed................: 0.00%  0 out of 3878
    http_reqs......................: 3878   133.504968/s

    EXECUTION
    iteration_duration.............: avg=77.42ms min=40.52ms  med=76.9ms  max=172.76ms p(90)=89.74ms p(95)=94.06ms
    iterations.....................: 3878   133.504968/s
    vus............................: 10     min=10        max=10
    vus_max........................: 10     min=10        max=10

    NETWORK
    data_received..................: 19 MB  664 kB/s
    data_sent......................: 349 kB 12 kB/s

running (0m29.0s), 00/10 VUs, 3878 complete and 0 interrupted iterations
```

Baad ke counters:

```
docker exec social-lab-mongo mongosh --quiet --eval 'print("mongo1 queries:", db.serverStatus().opcounters.query)'
mongo1 queries: Long('3955')
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("mongo2 queries:", db.serverStatus().opcounters.query)'
mongo2 queries: Long('5882')
```

Secondary natija: secondary (mongo1) **+3878**, primary (mongo2) **0**. 3878 requests = 3878 secondary queries, primary par zero. Aik setting se routing poori tarah palat gayi.

### Comparison (reads)

| | Baseline (primary) | SecondaryPreferred |
|---|---|---|
| Requests | 3792 | 3878 |
| Avg / p95 | 78.81 ms / 101 ms | 77.1 ms / 93.92 ms |
| Req/s | 130.36 | 133.50 |
| Primary queries | +3853 | 0 |
| Secondary queries | +39 | +3878 |

Imaandari se: **time ka farq na hone ke barabar hai** — laptop par dono members aik hi machine par hain aur data sirf 15 posts hai. Asal faida counters me hai: primary ab reads se azaad hai, is liye writes ke liye us ke paas poori capacity hai. Production me (alag machines, bara load) yehi farq app ko zinda rakhta hai.

---

## Step 5 — Write load test (aur aik asli bug)

Endpoints dekhne ka command + poora output (`backend` se):

```
grep -n "@app.post\|@app.delete\|def create\|def like\|def comment\|class .*Create" app/main.py
105:class PostCreate(BaseModel):
232:@app.post("/api/v1/auth/signup", status_code=201)
256:@app.post("/api/v1/auth/login")
336:@app.post("/api/v1/auth/logout")
349:@app.post("/api/v1/auth/refresh")
416:@app.post("/api/v1/posts", status_code=201)
417:def create_post(data: PostCreate, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
504:@app.post("/api/v1/posts/{post_id}/like")
505:def like_post(post_id: str, data: LikeRequest, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
516:@app.post("/api/v1/posts/{post_id}/comment")
517:def comment_post(post_id: str, data: CommentRequest, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
534:@app.delete("/api/v1/posts/{post_id}")
562:@app.post("/api/v1/users/{username}/follow")
578:@app.post("/api/v1/users/{username}/unfollow")
649:@app.post("/api/v1/posts/{post_id}/view")
```

PostCreate fields dekhne ka command + poora output:

```
sed -n '105,112p' app/main.py
class PostCreate(BaseModel):
    content: str


class LikeRequest(BaseModel):
    pass
```

Login + pehle counters (poore commands + poora output):

```
curl -s -c /tmp/ali_cookies.txt -H "Content-Type: application/json" -d '{"username":"ali","password":"12345"}' http://localhost:8000/api/v1/auth/login
CSRF=$(grep csrf_token /tmp/ali_cookies.txt | awk '{print $NF}')
echo "csrf length: ${#CSRF}"
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("primary inserts before:", db.serverStatus().opcounters.insert)'
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("posts count before:", db.getSiblingDB("social_lab").posts.countDocuments())'
{"message":"login successful","username":"ali"}csrf length: 64
primary inserts before: Long('5')
posts count before: 16
```

Note: posts count **16** hai, pehle 15 tha — darmiyan me aik post barha tha, is test ka baseline 16 hai, 15 nahi.

Write loop (poora command, 200 parallel creates, `xargs -P 10`):

```
curl -s -c /tmp/ali_cookies.txt -H "Content-Type: application/json" -d '{"username":"ali","password":"12345"}' http://localhost:8000/api/v1/auth/login
CSRF=$(grep csrf_token /tmp/ali_cookies.txt | awk '{print $NF}')
seq 1 200 | xargs -P 10 -I N curl -s -o /dev/null -w "%{http_code}\n" -b /tmp/ali_cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d '{"content": "writetest-N"}' http://localhost:8000/api/v1/posts | sort | uniq -c
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("primary inserts after:", db.serverStatus().opcounters.insert)'
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("posts count after:", db.getSiblingDB("social_lab").posts.countDocuments())'
docker exec social-lab-mongo mongosh --quiet --eval 'db.getMongo().setReadPref("secondary"); printjson(db.getSiblingDB("social_lab").posts.findOne({content: "writetest-1"}))'
```

Poora output:

```
{"message":"login successful","username":"ali"}    155 201
     45 500
primary inserts after: Long('210')
posts count after: 216
{
  _id: ObjectId('6ae05922ae510ae7ede9b9fd'),
  username: 'ali',
  content: 'writetest-1',
  created_at: ISODate('2026-10-27T07:56:18.377Z'),
  liked_by: [],
  comments: [],
  views: 0
}
```

Natija: **155 × 201, 45 × 500** — lekin posts phir bhi 200 ban gaye (16 → 216), inserts 5 → 210, aur secondary se `writetest-1` mil gaya. Matlab: write ka rasta readPreference se **nahi** badla (writes primary par hi gaye, phir replicate hue) — lekin 45 responses 500 the. Ise nazar-andaz nahi kiya gaya.

---

## Step 6 — Diagnosis (traceback se, andaze se nahi)

Pehli koshish (traceback framework ki lines par kata tha):

```
docker logs social-lab-app --tail 200 2>&1 | grep -A 15 "Traceback" | head -80
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/uvicorn/protocols/http/httptools_impl.py", line 422, in run_asgi
  File "/usr/local/lib/python3.12/site-packages/uvicorn/middleware/proxy_headers.py", line 63, in __call__
  File "/usr/local/lib/python3.12/site-packages/fastapi/applications.py", line 1194, in __call__
  File "/usr/local/lib/python3.12/site-packages/starlette/applications.py", line 96, in __call__
  File "/usr/local/lib/python3.12/site-packages/starlette/middleware/errors.py", line 186, in __call__
  File "/usr/local/lib/python3.12/site-packages/starlette/middleware/errors.py", line 164, in __call__
  File "/usr/local/lib/python3.12/site-packages/fastapi/telemetry/_asgi.py", line 151, in __call__
```

Doosri koshish (poora traceback nahi mila to tail 50 bhi dekha — us me sirf browser activity thi: /metrics scrapes, /static/feed.html 304, /api/v1/auth/me 200, /api/v1/following 200, /api/v1/feed 200, /view POSTs 200 — koi 500 nahi, kyun ke wo taaza activity thi, load test ki nahi).

Phir error ki qism + poora traceback:

```
docker logs social-lab-app 2>&1 | grep -E "Error" | sort | uniq -c | sort -rn | head -10
     45 TypeError: 'NoneType' object is not subscriptable
      1 INFO:     172.19.0.1:60084 - "POST /api/v1/posts HTTP/1.1" 500 Internal Server Error
      (... 45 dafa 500 wali lines, har aik alag port par ...)
docker logs social-lab-app 2>&1 | grep -A 100 "Traceback (most recent call last)" | sed -n '50,95p'
  File "/usr/local/lib/python3.12/site-packages/fastapi/routing.py", line 152, in app
    response = await f(request)
  File "/usr/local/lib/python3.12/site-packages/fastapi/routing.py", line 745, in app
    raw_response = await run_endpoint_function(
  File "/usr/local/lib/python3.12/site-packages/fastapi/routing.py", line 374, in run_endpoint_function
    return await run_in_threadpool(
  File "/usr/local/lib/python3.12/site-packages/starlette/concurrency.py", line 34, in run_in_threadpool
    return await anyio.to_thread.run_sync(func)
  File "/usr/local/lib/python3.12/site-packages/anyio/to_thread.py", line 65, in run_sync
    return await get_async_backend().run_sync_in_worker_thread(
  File "/usr/local/lib/python3.12/site-packages/anyio/_backends/_asyncio.py", line 2706, in run_sync_in_worker_thread
    return await future
  File "/usr/local/lib/python3.12/site-packages/anyio/_backends/_asyncio.py", line 1100, in run
    result = context.run(func, *args)
  File "/usr/local/lib/python3.12/site-packages/fastapi/telemetry/_api.py", line 244, in _run_sync_endpoint
    return function(**arguments)
  File "/code/backend/app/main.py", line 432, in create_post
    return post_to_dict(post)
           ^^^^^^^^^^^^^^^^^^
  File "/code/backend/app/main.py", line 119, in post_to_dict
    "id": str(post["_id"]),
              ~~~~^^^^^^^
TypeError: 'NoneType' object is not subscriptable
```

Root cause (sabit, andaza nahi):

1. `create_post` post **primary** par insert karta hai.
2. Phir foran wahi post dobara parhta hai (`find_one`) taake jawab me poora post bhej sake.
3. readPreference `secondaryPreferred` hai — is liye wo `find_one` **secondary** par gaya.
4. Replication me milliseconds ki der hoti hai — 45 dafa document abhi secondary par pahuncha hi nahi tha, to `find_one` ne `None` diya, aur `None["_id"]` par crash → 500.
5. Insert pehle ho chuka tha, is liye posts phir bhi 200 ban gaye. **500 ka matlab ye nahi ke write fail hua** — yehi "uncertain outcome" wali baat hai (Ch21 ke `w:majority` timeout jaisi, ab app ke andar).

Sirf 45 dafa kyun? Kyun ke ye **race** hai: 155 dafa document waqt par pahunch gaya, 45 dafa nahi. Aise bug testing me aksar chhup jate hain — is liye parallel load test zaroori tha.

Cleanup (poora command + poora output):

```
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("deleted:", db.getSiblingDB("social_lab").posts.deleteMany({content: /^writetest/}).deletedCount); print("posts count now:", db.getSiblingDB("social_lab").posts.countDocuments())'
deleted: 200
posts count now: 16
```

---

## Step 7 — Fix: read-after-write primary se (pehla hissa — create/like/comment/view)

Code dekhne ke commands (`backend` se):

```
sed -n '416,435p' app/main.py
sed -n '504,533p' app/main.py
```

`create_post` ka poora code (BEFORE, lines 416–432):

```python
@app.post("/api/v1/posts", status_code=201)
def create_post(data: PostCreate, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    result = posts_collection.insert_one({
        "username": username,
        "content": data.content.strip(),
        "created_at": datetime.now(timezone.utc),
        "liked_by": [],
        "comments": [],
        "views": 0,
    })
    post = posts_collection.find_one({"_id": result.inserted_id})
    invalidate_feed_cache()
    return post_to_dict(post)
```

`like_post` aur `comment_post` ka mutaliqa hissa (BEFORE):

```python
# like_post
    post = get_post_or_404(post_id)
    if username not in post.get("liked_by", []):
        posts_collection.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
    post = posts_collection.find_one({"_id": post["_id"]})
    invalidate_feed_cache()
    return post_to_dict(post)

# comment_post
    post = get_post_or_404(post_id)
    comment = {
        "username": username,
        "text": data.text.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    posts_collection.update_one({"_id": post["_id"]}, {"$push": {"comments": comment}})
    post = posts_collection.find_one({"_id": post["_id"]})
    invalidate_feed_cache()
    return post_to_dict(post)
```

Teenon re-read jagahen (locator script + poora output):

```python
python3 - << 'EOF'
import re
lines = open("app/main.py").read().splitlines()
func = None
for i, l in enumerate(lines, 1):
    m = re.match(r"def (\w+)", l)
    if m: func = m.group(1)
    if 'find_one({"_id": post["_id"]})' in l:
        print(i, "in", func, ":", l.strip())
EOF
511 in like_post : post = posts_collection.find_one({"_id": post["_id"]})
529 in comment_post : post = posts_collection.find_one({"_id": post["_id"]})
656 in view_post : post = posts_collection.find_one({"_id": post["_id"]})
```

Pehla patch assert par fail hua (poora output):

```
python3 /tmp/ch22_primary_read_patch.py
Traceback (most recent call last):
  File "/tmp/ch22_primary_read_patch.py", line 9, in <module>
    must('post = posts_collection.find_one({"_id": post["_id"]})', 2)
  File "/tmp/ch22_primary_read_patch.py", line 6, in must
    assert c == n, "found: " + str(c) + " for " + repr(sub)
AssertionError: found: 3 for 'post = posts_collection.find_one({"_id": post["_id"]})'
compile OK
```

Aur achha hua: script asserts write se pehle hain, is liye `main.py` wahi ka wahi raha (koi aadha patch nahi). "compile OK" bhi original file ka tha. Teesri jagah `view_post` (line 656) nikli — views barha kar dobara parhna, is me crash nahi hota (post pehle se maujood hai) lekin secondary se **purana views count** aata.

Doosra patch (poora code, `/tmp/ch22_primary_read_patch2.py`, `must 3` ke saath):

```python
p = "app/main.py"
s = open(p).read()
open("/tmp/main.py.ch22.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must("from pymongo import MongoClient", 1)
must('post = posts_collection.find_one({"_id": result.inserted_id})', 1)
must('post = posts_collection.find_one({"_id": post["_id"]})', 3)
s = s.replace("from pymongo import MongoClient", "from pymongo import MongoClient\nfrom pymongo.read_preferences import ReadPreference", 1)
s = s.replace('post = posts_collection.find_one({"_id": result.inserted_id})', 'post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": result.inserted_id})', 1)
s = s.replace('post = posts_collection.find_one({"_id": post["_id"]})', 'post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})')
open(p, "w").write(s)
print("primary-read patch applied")
```

Patch chalane + verify ke commands (`backend` se, phir restart `apps/social-lab` se):

```
python3 /tmp/ch22_primary_read_patch2.py
python3 -m py_compile app/main.py && echo "compile OK"
grep -n "ReadPreference" app/main.py
cd ~/system-design/apps/social-lab
docker compose restart app
sleep 5
curl -s -o /dev/null -w "feed http code: %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
```

Expectation: patch applied, compile OK, grep me **5 lines** (1 import + 4 usages: create, like, comment, view — `get_post_or_404` abhi baaqi hai, wo Step 9 me hai), feed 200.

**Dobara write test (poore commands + poora output):**

```
curl -s -c /tmp/ali_cookies.txt -H "Content-Type: application/json" -d '{"username":"ali","password":"12345"}' http://localhost:8000/api/v1/auth/login
CSRF=$(grep csrf_token /tmp/ali_cookies.txt | awk '{print $NF}')
seq 1 200 | xargs -P 10 -I N curl -s -o /dev/null -w "%{http_code}\n" -b /tmp/ali_cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d '{"content": "writetest-N"}' http://localhost:8000/api/v1/posts | sort | uniq -c
docker logs social-lab-app 2>&1 | grep -c "TypeError"
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("deleted:", db.getSiblingDB("social_lab").posts.deleteMany({content: /^writetest/}).deletedCount); print("posts count now:", db.getSiblingDB("social_lab").posts.countDocuments())'
{"message":"login successful","username":"ali"}    200 201
45
deleted: 200
posts count now: 16
```

**200 × 201, 0 × 500.** TypeError count abhi bhi 45 (purane, koi naya nahi). Cleanup ke baad count 16.

---

## Step 8 — Cache wapas ON

`FEED_CACHE_ENABLED=false` env line hatayi (default true). Patch script (poora code, `/tmp/ch22_cacheon_patch.py`):

```python
p = "docker-compose.yml"
s = open(p).read()
open("/tmp/docker-compose.yml.ch22cache.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must("      - FEED_CACHE_ENABLED=false", 1)
s = s.replace("      - FEED_CACHE_ENABLED=false\n", "", 1)
open(p, "w").write(s)
print("cache env removed (default true)")
```

Cache ON karne + verify ke commands + poora output:

```
python3 /tmp/ch22_cacheon_patch.py
docker compose up -d app
[+] up 5/5
 ✔ Container social-lab-redis  Running
 ✔ Container social-lab-mongo  Running
 ✔ Container social-lab-mongo2 Running
 ✔ Container social-lab-mongo3 Running
 ✔ Container social-lab-app    Started
curl -s -D - -o /dev/null "http://localhost:8000/api/v1/feed?limit=5" | grep -i "x-cache"
x-cache: MISS
curl -s -D - -o /dev/null "http://localhost:8000/api/v1/feed?limit=5" | grep -i "x-cache"
x-cache: HIT
```

Pehli request MISS, doosri HIT — app normal haalat me wapas, readPreference `secondaryPreferred` barkarar. Git status us waqt: `?1 ~2` (Dockerfile naya, compose + main.py modified).

---

## Step 9 — Fix: read-after-write primary se (doosra hissa — get_post_or_404)

`get_post_or_404` dekhne ki pehli koshish galat folder se hui:

```
grep -n -A 8 "def get_post_or_404" app/main.py
grep: app/main.py: No such file or directory
```

Wajah: command `backend` folder ki thi aur terminal `apps/social-lab` me tha. Sahi command (`apps/social-lab` se) + poora output:

```
grep -n -A 8 "def get_post_or_404" backend/app/main.py
131:def get_post_or_404(post_id):
132-    try:
133-        object_id = ObjectId(post_id)
134-    except Exception:
135-        raise HTTPException(status_code=404, detail="post not found")
136-    post = posts_collection.find_one({"_id": object_id})
137-    if not post:
138-        raise HTTPException(status_code=404, detail="post not found")
139-    return post
```

Ye bhi secondary se parhta hai — taaza post par like/comment/delete se ghalat 404 ka edge isi family ka hai. Is ka patch (poora code, `/tmp/ch22_404_patch.py`):

```python
p = "backend/app/main.py"
s = open(p).read()
open("/tmp/main.py.ch22b.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must('post = posts_collection.find_one({"_id": object_id})', 1)
s = s.replace('post = posts_collection.find_one({"_id": object_id})', 'post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": object_id})', 1)
open(p, "w").write(s)
print("get_post_or_404 primary-read applied")
```

Patch chalane + verify + restart ke commands (`apps/social-lab` se) + poora output:

```
python3 /tmp/ch22_404_patch.py
python3 -m py_compile backend/app/main.py && echo "compile OK"
grep -n "ReadPreference" backend/app/main.py
17:from pymongo.read_preferences import ReadPreference
136:    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": object_id})
431:    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": result.inserted_id})
512:    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
530:    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
657:    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
docker compose restart app
[+] restart 0/1
 ⠦ Container social-lab-app Restarting
sleep 5
curl -s -o /dev/null -w "feed http code: %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
feed http code: 200
```

**6 lines** (1 import + 5 usages: get_post_or_404, create, like, comment, view). Feed 200.

**Fresh-like test (get_post_or_404 fix ka seedha test, poore commands + poora output):**

```
curl -s -c /tmp/ali_cookies.txt -H "Content-Type: application/json" -d '{"username":"ali","password":"12345"}' http://localhost:8000/api/v1/auth/login
CSRF=$(grep csrf_token /tmp/ali_cookies.txt | awk '{print $NF}')
ID=$(curl -s -b /tmp/ali_cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d '{"content": "freshliketest"}' http://localhost:8000/api/v1/posts | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "created id: $ID"
curl -s -o /dev/null -w "immediate like http code: %{http_code}\n" -b /tmp/ali_cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d '{}' "http://localhost:8000/api/v1/posts/$ID/like"
docker exec social-lab-mongo2 mongosh --quiet --eval 'print("deleted:", db.getSiblingDB("social_lab").posts.deleteMany({content: "freshliketest"}).deletedCount); print("posts count now:", db.getSiblingDB("social_lab").posts.countDocuments())'
{"message":"login successful","username":"ali"}created id: 6ae05d79b0a944424e112c0f
immediate like http code: 200
deleted: 1
posts count now: 16
```

Post banao → foran us par like → **200** (pehle yehi race 404 de sakti thi). Cleanup ke baad count 16.

---

## BEFORE/AFTER (har changed jagah, mukammal)

Import — BEFORE:

```python
from pymongo import MongoClient
```

Import — AFTER:

```python
from pymongo import MongoClient
from pymongo.read_preferences import ReadPreference
```

create_post — BEFORE:

```python
    post = posts_collection.find_one({"_id": result.inserted_id})
```

create_post — AFTER:

```python
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": result.inserted_id})
```

like_post / comment_post / view_post — BEFORE:

```python
    post = posts_collection.find_one({"_id": post["_id"]})
```

like_post / comment_post / view_post — AFTER:

```python
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
```

get_post_or_404 — BEFORE:

```python
    post = posts_collection.find_one({"_id": object_id})
```

get_post_or_404 — AFTER:

```python
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": object_id})
```

Compose env — BEFORE: koi app service nahi, MONGO_URI default `mongodb://localhost:27017` → AFTER: app service + `MONGO_URI=mongodb://social-lab-mongo:27017,social-lab-mongo2:27017,social-lab-mongo3:27017/?replicaSet=rs0&readPreference=secondaryPreferred` + `REDIS_HOST=redis` (test ke dauran `FEED_CACHE_ENABLED=false` aarzi tor par laga tha, aakhir me hata diya gaya — default true).

WHY `with_options`: sirf us aik read ke liye preference badalta hai — baaqi app abhi bhi `secondaryPreferred` par rehti hai. Aam reads secondary se, apna taaza kaam primary se. Yehi production ka usool hai.

---

## Comparison (writes)

| | Pehle (secondaryPreferred, baghair fix) | Baad (fix ke saath) |
|---|---|---|
| 200 parallel creates | 155 × 201, 45 × 500 | 200 × 201, 0 × 500 |
| Posts bane | 200 (500 ke bawajood) | 200 |
| Secondary par writetest-1 | Mil gaya (replication) | Mil gaya |
| Write ka rasta | Primary (readPreference se nahi badla) | Primary |
| Fresh post par foran like | Race me 404 mumkin (get_post_or_404 secondary se) | 200 (primary existence check) |

## Limitations (imaandari se)

- Laptop par primary/secondary aik hi machine par hain — latency ka farq chhota hai; routing ka saboot counters hain, milliseconds nahi.
- Sirf aik data-bearing secondary hai (mongo1); arbiter data nahi rakhta, is liye secondary reads sirf mongo1 par jati hain.
- `secondaryPreferred` ka matlab fallback bhi hai: secondary na mile to read primary se aa jayegi (ye feature hai, bug nahi).
- Inserts +205 vs posts +200 ka 5 ka farq: 2 login ke session/refresh ke hain, 3 ka hisab logs se poori tarah explain nahi hua — natije par asar nahi, lekin note kar liya gaya hai.
- App-level failover driver ke zariye hai; host par chalne wala purana uvicorn tareeqa ab container me hai (bind mount + `--reload` barkarar).
- Doosra tareeqa bhi durust hota: dobara parhe baghair response usi data se banana jo haath me hai (inserted_id + defaults) — is chapter me primary-read wala tareeqa chuna gaya kyun ke wo isi chapter ke topic (readPreference) se jura hai.

---

## Close (poore git commands)

Code commit (repo root `~/system-design` se):

```
cd ~/system-design
git add apps/social-lab/Dockerfile apps/social-lab/docker-compose.yml apps/social-lab/backend/app/main.py
git status --short
git commit -m "Chapter 22: app-level read/write split (containerized app, secondary reads, primary read-after-write)"
git push
git log -1 --format="%H %s"
```

Docs commit + tag (is README file ke saath):

```
mkdir -p chapters/22-app-read-write-split
```

Is README ko `chapters/22-app-read-write-split/README.md` par rakho, phir:

```
git add chapters/22-app-read-write-split/README.md
git status --short
git commit -m "Chapter 22 docs: app read/write split, secondary load tests, stale-read bug and fix"
git push
git log -1 --format="%H %s"
git tag ch22-app-read-write-split
git push origin ch22-app-read-write-split
git tag --list | grep ch22
```

- Code: `Dockerfile` (nayi), `docker-compose.yml` (app service + replicaSet env), `backend/app/main.py` (primary read-after-write).
- Tag: `ch22-app-read-write-split` (docs commit par).
- Commit/tag/Notion hashes: Notion page par (fetch-verified).
- Agla topic: sharding (partitioning methods + partition key + hot shard).

# Chapter 24 — Kafka: Async Likes (Event-Driven Writes)

Last chapter of the scale roadmap: indexing (Ch20) → replication + read/write split
(Ch21, Ch22) → sharding (Ch23) → **Kafka (Ch24)**.

## What this chapter builds on, and what it unlocks

- Builds on **Ch19 (fan-out)**: the follower feed was built as pull-on-read; push via
  consumers was the missing half. This chapter builds that missing half for likes.
- Builds on **Ch13 (unique views)** and **Ch18 (cache)**: the write path is the
  bottleneck now, not reads. Ch22 already proved reads are fast (133.50 req/s); this
  chapter attacks the write path.
- Builds on **Ch22 (read/write split)**: the consumer deliberately reads PRIMARY
  (see Step 4), because the Ch22 stale-read lesson applies to the consumer too.
- Unlocks: the scale roadmap is complete after this chapter. The pattern (produce an
  event, consume it asynchronously) generalises to views, follows, notifications, and
  any write that does not need an immediate synchronous answer.

## Design (decided before starting, Rao can veto)

- **Single broker in KRaft mode (no ZooKeeper)**: image `apache/kafka:3.9.0`,
  container `social-lab-kafka`, host port `9092:9092` only, volume `kafka_data`.
- **Topics for events**: this chapter implements likes only. Topic **`post-likes`**,
  1 partition, replication factor 1 (single broker, so every replication factor
  must be 1).
- **Producer inside the app**: the like endpoint keeps auth + CSRF + post-exists
  check; only the Mongo write is replaced by a Kafka event. Security is unchanged.
- **Consumer as a separate process**: applies the same check-then-push rule as the
  sync path, so duplicate events are harmless (safe reprocessing after a crash).
- **Flag `KAFKA_LIKES_ENABLED`**: toggles sync/async so the SAME k6 test runs on
  both paths. Default `false` in code; `true` in compose for the app service.
- **Library: `kafka-python`** (pure Python, no native deps) — replaced by
  `kafka-python-ng` during Step 4 (see the library bug there). Same imports, same
  API, so no application code changed.
- **Dual listeners** (the classic advertised-listener trap): containers use the
  INTERNAL listener (`social-lab-kafka:29092`); the host uses EXTERNAL
  (`localhost:9092`). Advertising only `localhost` would make container clients
  connect to themselves and fail.
- Planned steps: 1 baseline → 2 broker up → 3 sync baseline measurement →
  4 producer + consumer (async likes) + same test → 5 burst absorption demo +
  comparison → 6 close (tag `ch24-kafka`, folder `chapters/24-kafka/`).

## Step 1 — Baseline (verified 2026-11-02 09:35)

The stack had restarted around 09:26. Containers app, mongo, mongo2, mongo3,
redis, grafana, prometheus were all Up ~9 minutes; the sharded cluster from Ch23
was correctly NOT up. Feed returned 200.

```bash
docker ps --format "{{.Names}} {{.Status}}"
curl -s -o /dev/null -w "feed: %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
```

App database confirmed via `listDatabases`: admin 81920, config 266240,
local 765952, **`social_lab` 1650688 bytes**.

Machine context: this chapter runs on the OLD machine (WSL, 3.8 GiB total RAM
reported by the OS). The second machine (8 GB RAM, 4 cores, Ubuntu Desktop)
arrived 2026-11-02 but is reserved; Rao chose the old machine for this chapter.

Standing warning (proven in Ch23, repeated here): bare `docker compose up -d`
starts ALL services including the stopped sharded cluster — always name services
explicitly (e.g. `up -d kafka`, `up -d app consumer`).

## Step 2 — Broker up, with one real bug (verified 2026-11-02 09:49–09:51)

### The kafka service block

Added to `apps/social-lab/docker-compose.yml` before `prometheus:` (top-level
volume `kafka_data:` added after `redis_data:`). Patch script
`/tmp/ch24_kafka_patch.py` (backup `/tmp/docker-compose.yml.ch24kafka.bak`),
`must()` asserts passed ("kafka service added"), compose syntax OK.

```yaml
  kafka:
    image: apache/kafka:3.9.0
    container_name: social-lab-kafka
    ports:
      - "9092:9092"
    volumes:
      - kafka_data:/var/lib/kafka/data
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@social-lab-kafka:9093
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENERS: INTERNAL://:29092,EXTERNAL://:9092,CONTROLLER://:9093
      KAFKA_ADVERTISED_LISTENERS: INTERNAL://social-lab-kafka:29092,EXTERNAL://localhost:9092
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL
      CLUSTER_ID: f5265252-f845-4547-afe7-5d9ffdfda816
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: 0
      KAFKA_HEAP_OPTS: "-Xmx512M -Xms512M"
```

Notes on the values:

- `CLUSTER_ID` was generated explicitly by the assistant for reproducibility; the
  image would otherwise auto-generate one.
- `KAFKA_HEAP_OPTS: "-Xmx512M -Xms512M"` is laptop demo tuning.
- All replication factors are 1 because there is a single broker.
- `KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL` was NOT in the first version — it
  is the fix for the bug below.

### First start: image pull, then broker exit

```bash
docker compose up -d kafka
```

Image pull took **620.3 s** (~10 min), volume created, container started — then
the **broker exited**. Follow-up commands failed with "container is not running"
(a consequence, not a separate bug).

### Bug: inter-broker listener name (from the log, not guessed)

```
java.lang.IllegalArgumentException: requirement failed: inter.broker.listener.name
must be a listener name defined in advertised.listeners. The valid options based
on currently configured listeners are INTERNAL,EXTERNAL
```

Root cause: Kafka's default inter-broker listener name is `PLAINTEXT`, which does
not exist among our named listeners (INTERNAL/EXTERNAL/CONTROLLER). Fix patch
`/tmp/ch24_kafka_fix.py` (backup `/tmp/docker-compose.yml.ch24kafka2.bak`) added
`KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL` after the
`KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER` line. No volume wipe was needed —
storage format had never run (validation failed first).

### Success (verified 2026-11-02 09:51)

Container `Up 15 seconds`; logs: BrokerServer STARTING → **STARTED**,
`Kafka Server started`, **Kafka 3.9.0** (commitId `a60e31147e6b01ee`), broker
unfenced, both endpoints READY, `Awaiting socket connections on 0.0.0.0:29092`
and `0.0.0.0:9092`.

```bash
docker exec social-lab-kafka /opt/kafka/bin/kafka-cluster.sh cluster-id --bootstrap-server localhost:9092
```

Output: **`Cluster ID: f5265252-f845-4547-afe7-5d9ffdfda816`** — exact match with
the configured `CLUSTER_ID`. The topics `--list` output was not pasted, but the
cluster-id answer proves the broker works.

## Step 3 — Sync baseline measurement (verified 2026-11-02 09:54–10:00)

### Target post

Newest feed post, id **`6ae2f1d4eecd8e414e3a7e99`**, username `rao`, content
"Kafka limitation", created 2026-10-29T07:12:20 (a post Rao made himself after
Ch23 closed — he kept using the app). Before-state: `likes_count: 0`,
`liked_by: []`.

### First k6 run FAILED completely — then diagnosed from numbers, not guesses

Script `/tmp/ch24_like_baseline.js` (10 VUs, 30 s; `setup()` fetches
`feed?limit=1` for the post id, logs in as `ali`, passes all login cookies +
`csrf_token` to the VUs). Result:

```
checks_total.......: 29070
checks_succeeded...: 0.00%   0 out of 29070
checks_failed......: 100.00%
http_req_duration..............: avg=10.65ms  p(95)=12.37ms
http_req_failed................: 99.99%
```

After-feed still `likes_count: 0`. Reading the numbers: setup's 2 requests
(feed + login) were the only non-failed ones; all likes failed FAST (~10 ms =
rejection, not a slow write); the count never moved (NOT idempotency).

### Diagnosis: manual curl, proven pattern

Login `{"message":"login successful","username":"ali"}` — auth ruled out.
CSRF length **64** — CSRF ruled out. Manual like returned:

```
HTTP 422 {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]}
```

**The like endpoint requires a JSON body**; the k6 script had sent an empty
body `''`. Code confirmation (grep, 2026-11-02 09:57):

```python
# backend/app/main.py, lines 505-511 (before the Step 4 patch)
@app.post("/api/v1/posts/{post_id}/like")
def like_post(post_id: str, data: LikeRequest, ...):
    ...
    if username not in post.get("liked_by", []):
        posts_collection.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
```

`data: LikeRequest` is a required body model (confirmed by line 506), and lines
510–511 confirm check-then-push idempotency in code.

Probe with body `'{}'` + `Content-Type: application/json` → **HTTP 200**, post
returned with `likes_count: 1, liked_by: ["ali"]` — `LikeRequest` is satisfied
by `{}`. The k6 script's body was changed to `'{}'` + JSON content-type header.

### Baseline k6 re-run (verified)

```
500 iterations, checks 500/500 (100% like 200), 0 failed
http_reqs......................: 502    15.755453/s   (500 + 2 setup)
http_req_duration..............: avg=627.91ms min=13.42ms med=604.68ms max=1.83s
                                   p(90)=643.52ms p(95)=679.14ms
iteration_duration.............: avg=606.24ms med=603.85ms p(90)=640.9ms p(95)=654.17ms
```

After-feed: `likes_count: 1` (unchanged through k6 — ali already liked;
idempotent no-ops, latency still valid because every request did the full
endpoint work). Also observed: post `views: 2, unique_views: 2` in the
after-feed (feed renders, not the like test).

**The cause of the ~600 ms sync latency was NOT diagnosed at this point and
must not be guessed.** Contrast: Ch22 reads were 133.50 req/s / avg 77.1 ms —
the like write path is ~8x slower. This 628 ms avg / 679 ms p95 / 15.76 req/s
is the baseline the async version must beat.

## Step 4 — Producer + consumer, async likes (verified 2026-11-02 10:03–10:16)

### Design (stated before the patch)

- Producer inside the app: like endpoint keeps auth + CSRF + post-exists check,
  only the Mongo write is replaced by a Kafka event.
- Consumer as a separate process, applying the same check-then-push.
- Flag `KAFKA_LIKES_ENABLED` toggles sync/async for the same k6 test.
- Topic `post-likes`, 1 partition, RF 1. App bootstrap for containers:
  `social-lab-kafka:29092` (INTERNAL listener).
- Ordering rule for the patch sequence (crash-window-free): topic +
  requirements + consumer + compose FIRST, then build + up, and main.py LAST —
  so the reload only happens once kafka-python is already installed in the image.

### 4.1 Topic

```bash
docker exec social-lab-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --topic post-likes --partitions 1 --replication-factor 1
docker exec social-lab-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
docker exec social-lab-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic post-likes
```

Expectation: `Created`, list shows `post-likes`, describe shows Partition 0,
Leader 1, Replicas 1, Isr 1.

### 4.2 Requirements

```bash
cat > /tmp/ch24_reqs_patch.py << 'PYEOF'
p = "backend/requirements.txt"
s = open(p).read()
assert "kafka-python" not in s, "already present"
if not s.endswith("\n"):
    s += "\n"
s += "kafka-python==2.0.2\n"
open(p, "w").write(s)
print("requirements updated")
PYEOF
python3 /tmp/ch24_reqs_patch.py
```

`backend/requirements.txt` before (verified 2026-11-02, exactly):

```
fastapi
uvicorn[standard]
bcrypt
pymongo
prometheus-client
redis
```

After: the same six lines plus `kafka-python==2.0.2` — later replaced by
`kafka-python-ng==2.2.2` (see the library bug in 4.6).

### 4.3 Consumer script (`backend/consumer.py`, full file)

```python
"""Kafka consumer for async likes (Chapter 24).

Reads like events from the post-likes topic and applies them to MongoDB
with the same check-then-push rule as the sync path, so duplicate events
are harmless (reprocessing after a crash is safe). After applying, it
invalidates the feed cache so the UI catches up.
"""
import json
import os

import redis
from bson import ObjectId
from kafka import KafkaConsumer
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

TOPIC = "post-likes"
GROUP_ID = "likes-group"


def invalidate_feed_cache(r):
    for key in r.scan_iter("feed:*"):
        r.delete(key)


def main():
    client = MongoClient(MONGO_URI)
    posts = client["social_lab"]["posts"]
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=GROUP_ID,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    print("likes consumer started, waiting for events...", flush=True)
    for msg in consumer:
        event = msg.value
        post_id = event.get("post_id")
        username = event.get("username")
        try:
            post = posts.find_one({"_id": ObjectId(post_id)})
        except Exception as exc:
            print("skip bad event %r: %s" % (event, exc), flush=True)
            continue
        if post is None:
            print("skip like for missing post %s" % post_id, flush=True)
            continue
        if username in post.get("liked_by", []):
            print("skip duplicate like %s on %s" % (username, post_id), flush=True)
            continue
        posts.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
        invalidate_feed_cache(r)
        print("applied like %s on %s" % (username, post_id), flush=True)


if __name__ == "__main__":
    main()
```

WHY the consumer's `MONGO_URI` has NO readPreference (default PRIMARY): the
Ch22 stale-read lesson. If the consumer read from a secondary, it could see a
stale `liked_by` and push a duplicate. The consumer is a writer — it reads
PRIMARY.

### 4.4 Compose patch — app env + consumer service

Script `/tmp/ch24_consumer_patch.py` (backup
`/tmp/docker-compose.yml.ch24c.bak`). It inserts the app env vars (handling
both list-style and map-style environment blocks) and the consumer service
before `prometheus:`. The consumer service shares `build: .` with the app but
a different command:

```yaml
  consumer:
    build: .
    container_name: social-lab-consumer
    command: ["python", "consumer.py"]
    environment:
      # No readPreference: consumer reads PRIMARY so the check-then-push
      # never sees stale liked_by (Ch22 lesson).
      MONGO_URI: mongodb://social-lab-mongo:27017,social-lab-mongo2:27017,social-lab-mongo3:27017/?replicaSet=rs0
      REDIS_HOST: redis
      KAFKA_BOOTSTRAP_SERVERS: social-lab-kafka:29092
    depends_on:
      - kafka
      - mongo
      - mongo2
      - mongo3
      - redis
    restart: unless-stopped
```

App env added:

```yaml
      - KAFKA_BOOTSTRAP_SERVERS=social-lab-kafka:29092
      - KAFKA_LIKES_ENABLED=true
```

Important consequence: `consumer.py` is copied into the image by `COPY backend`
— it is NOT bind-mounted. A later consumer change needs an image rebuild, unlike
app code under `backend/app`, which is bind-mounted and live on reload. Same
applies to `requirements.txt` changes.

### 4.5 Build + up

```bash
docker compose build app consumer
docker compose up -d app consumer
```

### 4.6 Main.py patch — BEFORE/AFTER

BEFORE (from Rao's grep, lines 505–515):

```python
@app.post("/api/v1/posts/{post_id}/like")
def like_post(post_id: str, data: LikeRequest, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    if username not in post.get("liked_by", []):
        posts_collection.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
    invalidate_feed_cache()
    return post_to_dict(post)
```

AFTER — only one branch is added: flag on → produce the event and return
immediately (likes_count in the response is the pre-like value — eventual
consistency; it catches up once the consumer drains). Auth, CSRF, post-exists
check all stay exactly as before. The sync block is untouched, so flag off
behaves exactly like before:

```python
@app.post("/api/v1/posts/{post_id}/like")
def like_post(post_id: str, data: LikeRequest, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    if KAFKA_LIKES_ENABLED:
        # Async path: publish the event and return immediately. The consumer
        # applies the like to Mongo, so likes_count here is the pre-like value
        # (eventual consistency) -- it catches up once the consumer drains.
        # Fire-and-forget: lowest latency, but a broker outage would drop the
        # event silently; production would use acks=all + send().get(timeout)
        # with a retry/dead-letter path.
        get_kafka_producer().send(KAFKA_LIKES_TOPIC, {"post_id": str(post["_id"]), "username": username})
        return post_to_dict(post)
    if username not in post.get("liked_by", []):
        posts_collection.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
    invalidate_feed_cache()
    return post_to_dict(post)
```

Also added: `from kafka import KafkaProducer` import, and in the flags area
(after the `FOLLOW_ENABLED` line) the flag + env + lazy producer singleton:

```python
KAFKA_LIKES_ENABLED = os.environ.get("KAFKA_LIKES_ENABLED", "false").lower() == "true"
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_LIKES_TOPIC = "post-likes"
_kafka_producer = None


def get_kafka_producer():
    global _kafka_producer
    if _kafka_producer is None:
        _kafka_producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
    return _kafka_producer
```

Patch script `/tmp/ch24_main_patch.py` (backup `/tmp/main.py.ch24.bak`) with
`must()` asserts for the import line, the `FOLLOW_ENABLED` line, and the full
BEFORE block. Verified with
`python3 -m py_compile backend/app/main.py backend/consumer.py` (py_compile
only parses — runtime behaviour is the real proof, per the standing lesson).

Note: in async mode `invalidate_feed_cache()` is NOT called by the endpoint —
the consumer invalidates after applying. The response itself is a direct
`post_to_dict`, not a cached feed, so the response is never stale-cached.

### 4.7 Library bug: kafka-python breaks on Python 3.12 (from the log)

After build + up, the consumer was crash-looping:

```
social-lab-app Up 58 seconds
social-lab-consumer Restarting (1) Less than a second ago
```

```python
File "/usr/local/lib/python3.12/site-packages/kafka/codec.py", line 9, in <module>
    from kafka.vendor.six.moves import range
ModuleNotFoundError: No module named 'kafka.vendor.six.moves'
```

Cause: **kafka-python 2.0.2** is from 2022 and its vendored `six` breaks on
Python 3.12 (the image's Python). This is a known incompatibility, not our
code's fault. (The app would hit the same error on reload — the rebuild fixes
both.) Fix: **kafka-python-ng**, the maintained fork, same imports and API, so
no application code changed:

```bash
cat > /tmp/ch24_reqs_fix.py << 'PYEOF'
p = "backend/requirements.txt"
s = open(p).read()
assert "kafka-python==2.0.2" in s, "expected line not found"
s = s.replace("kafka-python==2.0.2", "kafka-python-ng==2.2.2", 1)
open(p, "w").write(s)
print("requirements fixed")
PYEOF
python3 /tmp/ch24_reqs_fix.py
docker compose build app consumer
docker compose up -d app consumer
sleep 10
docker ps --format "{{.Names}} {{.Status}}" | grep -E "app|consumer"
docker logs social-lab-consumer --tail 10
curl -s -o /dev/null -w "feed: %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
```

Why the rebuild is clean: changing `requirements.txt` busts Docker's layer
cache from the `COPY requirements` step onward, so the new image contains only
kafka-python-ng — no stale kafka-python files.

Verify output (2026-11-02 10:13):

```
[+] build 2/2
 ✔ Image social-lab-consumer Built   45.7s
 ✔ Image social-lab-app      Built   45.6s
[+] up 7/7
 ✔ Container social-lab-app      Started
 ✔ Container social-lab-consumer Started
social-lab-app Up 10 seconds
social-lab-consumer Up 10 seconds
likes consumer started, waiting for events...
feed: 200
```

### 4.8 Single-event proof: eventual consistency, live (verified 2026-11-02 10:14)

A FRESH post, so the count starts at 0 and the 0→1 move is visible:

```bash
curl -s -c /tmp/ali_cookies.txt -H "Content-Type: application/json" -d '{"username":"ali","password":"12345"}' http://localhost:8000/api/v1/auth/login
CSRF=$(grep csrf_token /tmp/ali_cookies.txt | awk '{print $NF}')
curl -s -b /tmp/ali_cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d '{"content":"kafka async like test"}' -X POST http://localhost:8000/api/v1/posts > /tmp/newpost.json
cat /tmp/newpost.json
POSTID=$(grep -o '"id":"[^"]*"' /tmp/newpost.json | head -1 | cut -d'"' -f4)
echo "POSTID=$POSTID"
curl -s -b /tmp/ali_cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d '{}' -X POST "http://localhost:8000/api/v1/posts/$POSTID/like"
echo
sleep 5
docker logs social-lab-consumer --tail 5
curl -s "http://localhost:8000/api/v1/feed?limit=20" | python3 -c "import json,sys; d=json.load(sys.stdin); print([(p['id'],p['likes_count'],p['liked_by']) for p in d['posts'] if 'kafka async like test' in p['content']])"
```

Output, all three proofs together:

1. Create: `{"id":"6ae81c2a9d3ebbc0eafd9498","username":"ali","content":"kafka async like test","created_at":"2026-11-02T05:14:18.603000","likes_count":0,"liked_by":[],"comments":[],"views":0}`
2. Like response: same post with **`likes_count: 0`** — the endpoint answered
   immediately; Mongo was not updated yet. This IS eventual consistency.
3. Consumer log: `likes consumer started, waiting for events...` then
   **`applied like ali on 6ae81c2a9d3ebbc0eafd9498`** — the event was read
   from the topic and applied to Mongo.
4. Feed check: **`[('6ae81c2a9d3ebbc0eafd9498', 1, ['ali'])]`** — count caught
   up 0→1 after the drain.

### 4.9 Async k6 re-run: same script, async path (verified 2026-11-02 10:16)

```bash
k6 run /tmp/ch24_like_baseline.js
docker logs social-lab-consumer --tail 3
```

```
checks_total.......: 8891    280.055543/s
checks_succeeded...: 100.00% 8891 out of 8891
checks_failed......: 0.00%   0 out of 8891
  ✓ like 200
http_req_duration..............: avg=35.16ms min=5.98ms med=32.43ms max=1.47s
                                   p(90)=44.53ms p(95)=48.76ms
http_req_failed................: 0.00%  0 out of 8893
http_reqs......................: 8893   280.118541/s
iteration_duration.............: avg=33.73ms med=32.62ms p(90)=44.68ms p(95)=48.76ms
iterations.....................: 8891   280.055543/s
data_received..................: 4.4 MB 139 kB/s
data_sent......................: 4.5 MB 140 kB/s
running (0m31.7s), 00/10 VUs, 8891 complete and 0 interrupted iterations
```

Consumer log showed `skip duplicate like ali on 6ae81c2a9d3ebbc0eafd9498` —
duplicate events are safe; 8891 events drained without corrupting the count.
(This test post already had ali's like, so this run measures latency, not count
movement — same as the baseline run, where the count also stayed put.)

### Sync vs async comparison (Step 3 vs Step 4)

| Metric | Sync (Step 3) | Async (Step 4) | Difference |
|---|---|---|---|
| avg latency | 627.91 ms | 35.16 ms | ~18x faster |
| p95 | 679.14 ms | 48.76 ms | ~14x faster |
| throughput | 15.76 req/s | 280.12 req/s | ~18x more |
| iterations (30 s) | 500 | 8891 | ~18x more |
| checks | 500/500 | 8891/8891 | both 100% |
| max | 1.83 s | 1.47 s | one slow request in both (not hidden) |

Honest note: the sync path's ~600 ms was never pinpointed to one line, but the
async run proves it lived in the write path collectively — the only steps
removed were the Mongo update, the PRIMARY re-read, and cache invalidation, and
latency fell 628 → 35 ms.

Tradeoffs of async (the price of the speed): the response shows the OLD count
(eventual consistency); a dead consumer means DELAYED likes, not lost ones
(proven in Step 5); fire-and-forget produce means a broker outage could silently
drop an event (production fix: `acks=all` + `send().get(timeout)` + retry /
dead-letter path, noted in the code comment).

## Step 5 — Burst absorption demo (verified 2026-11-02 10:20)

Kafka's second big win: even with the consumer dead, the app keeps serving and
events wait safely in the topic. Demo: stop the consumer, fire the burst, watch
the lag grow, restart the consumer, watch it drain to zero.

```bash
docker compose stop consumer
k6 run /tmp/ch24_like_baseline.js
docker exec social-lab-kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group likes-group --describe
docker compose up -d consumer
sleep 10
docker logs social-lab-consumer --tail 3
docker exec social-lab-kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group likes-group --describe
```

Results:

1. **Burst: 9404 complete, 0 interrupted iterations in ~31.6 s** (≈298 req/s,
   derived from those two numbers) — the app stayed fast while the consumer
   was dead. The write path ends at the broker; it is independent of consumer
   speed.
2. **Lag while consumer down: exactly 9404** — the burst count, no more, no
   less. The describe output also said `Consumer group 'likes-group' has no
   active members.` — the consumer was down and the events persisted:

```
GROUP          TOPIC       PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
likes-group    post-likes  0          8892            18296           9404
```

3. **After restart: LAG 0**, drain visible in the log (`skip duplicate like`
   lines), and the consumer identity confirms the ng library in production:

```
GROUP          TOPIC       PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
likes-group    post-likes  0          18296           18296           0
CONSUMER-ID: kafka-python-2.2.2-dd92a507-ec2c-4943-97fe-a76fcaa2849d
HOST: /172.19.0.10   CLIENT-ID: kafka-python-2.2.2
```

4. **Offset audit — every event accounted for, zero loss:**

```
8892  (1 single-event like + 8891 async k6 likes)
+ 9404  (burst while consumer down)
= 18296  (LOG-END-OFFSET, and CURRENT-OFFSET after drain — exact match)
```

## Step 6 — Cleanup (verified 2026-11-02 10:22)

Only the test post is deleted — Rao's own "Kafka limitation" post stays. The
topic and the consumer service stay, like the replica set stayed after Ch21:
they are architecture, not test data.

```bash
curl -s -c /tmp/ali_cookies.txt -H "Content-Type: application/json" -d '{"username":"ali","password":"12345"}' http://localhost:8000/api/v1/auth/login
CSRF=$(grep csrf_token /tmp/ali_cookies.txt | awk '{print $NF}')
curl -s -w "\nHTTP %{http_code}\n" -b /tmp/ali_cookies.txt -H "X-CSRF-Token: $CSRF" -X DELETE "http://localhost:8000/api/v1/posts/6ae81c2a9d3ebbc0eafd9498"
curl -s "http://localhost:8000/api/v1/feed?limit=20" | python3 -c "import json,sys; d=json.load(sys.stdin); print([p['content'] for p in d['posts'] if 'kafka async like test' in p['content']])"
git status --short
git diff --stat
```

Output: `{"message":"post deleted","id":"6ae81c2a9d3ebbc0eafd9498"}` HTTP 200,
feed check `[]` (test post gone), and:

```
 M backend/app/main.py
 M backend/requirements.txt
 M docker-compose.yml
?? backend/consumer.py
 apps/social-lab/backend/app/main.py      | 24 ++++++++++++++++++++++++
 apps/social-lab/backend/requirements.txt |  1 +
 apps/social-lab/docker-compose.yml       | 42 ++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 67 insertions(+)
```

(`consumer.py` is untracked, so it does not appear in `diff --stat` — expected.)

## Limitations — honest notes

- **Eventual consistency**: the async like response shows the pre-like count.
  A UI that needs the fresh count must re-read after the drain (or the
  consumer must push an update). This is the price of the 18x speedup.
- **Fire-and-forget produce**: `send()` without `.get()` means a broker outage
  could silently drop an event. Production needs `acks=all`, a send timeout,
  retries, and a dead-letter path (noted in the code comment).
- **Single broker, 1 partition**: no fault tolerance (broker down = produce
  fails) and no parallelism (one partition = one consumer order). Ordering is
  guaranteed only WITHIN a partition; across partitions there is none.
- **Topic retention is default** (7 days in Kafka): events stay in the topic
  after consumption until retention expires. That is what made the Step 5
  lag demo possible, and it is also how a crashed consumer replays.
- **Sync 600 ms never pinpointed to one line**: only proven to live in the
  write path collectively (Mongo update + PRIMARY re-read + cache invalidate).
- **One slow request in both runs** (max 1.83 s sync, 1.47 s async) — likely a
  first-connection/batch effect; not investigated further, not hidden.
- **App still runs on rs0**: Kafka is independent of the Ch23 sharded cluster,
  which stays stopped. Pointing the app at mongos (the Ch23 follow-up offer)
  is still open and untaken.
- **Laptop tuning**: broker heap 512 MB, single broker, wiredTiger caches
  0.25 GB — demo settings, not production sizing.
- **k6 script note**: `/tmp/ch24_like_baseline.js` lived in /tmp (ephemeral);
  its structure is recorded in Step 3 (10 VUs, 30 s, setup fetches feed?limit=1,
  login as ali, cookies + CSRF to VUs) and the body fix (`''` → `'{}'` +
  JSON content-type). The script file itself is not part of the repo.
- **Security unchanged by async**: auth + CSRF + post-exists check run BEFORE
  the produce call, identically on both paths. The flag only swaps the write.
- **No hardcoded passwords**: unchanged rule. Grafana `admin/admin` in the
  local compose remains a local-demo ception.

## Close

- Code commit: TBD (4 files: `apps/social-lab/backend/app/main.py`,
  `apps/social-lab/backend/requirements.txt`,
  `apps/social-lab/backend/consumer.py`,
  `apps/social-lab/docker-compose.yml`).
- Docs commit: TBD (`chapters/24-kafka/README.md` — this file).
- Tag: `ch24-kafka` (on the docs commit, pushed).
- Notion: TBD (DEVOPS parent, fetch-verified).
- Scale roadmap complete: indexing → replication → sharding → Kafka.


# Chapter 23 — Sharding

## What this chapter builds on, and what it unlocks next

Builds on:

- Chapter 20 (DB indexing) — how a query finds data, and what a shard key is for.
- Chapter 21 (replication) — replica sets, PRIMARY/SECONDARY/ARBITER, elections.
- Chapter 22 (app-level read/write split) — the driver routes reads and writes.

Unlocks next:

- Chapter 24 (Kafka, last in the roadmap) — burst absorption and async likes/views/follows with push fan-out via consumers.

## Design (stated before starting)

A sharded cluster has three kinds of processes:

- **Config server** — holds the cluster metadata (which chunk lives on which shard). Runs as a replica set (CSRS).
- **Shards** — hold the actual data. In production each shard is a replica set; here each shard is a single mongod, learning only.
- **Mongos** — the router. The app talks to mongos as if it were a single MongoDB; mongos routes each operation to the right shard.

Topology used in this chapter (all in `apps/social-lab/docker-compose.yml`):

| Process | Container | Host port | Role |
|---|---|---|---|
| Config server | `social-lab-config` | 27020 | CSRS, single member `configrs` |
| Shard 1 | `social-lab-shard1` | 27021 | Single mongod, replica set `shard1rs` |
| Shard 2 | `social-lab-shard2` | 27022 | Single mongod, replica set `shard2rs` |
| Shard 3 | `social-lab-shard3` | 27023 | Single mongod, replica set `shard3rs` |
| Mongos router | `social-lab-mongos` | 27024 | Router, `--configdb configrs/social-lab-config:27017` |

Deliberate limitation, stated up front: **the app stays on rs0**. The sharding playground is a separate database (`shardlab`) on the sharded cluster. A production app would point at mongos (a single endpoint); migrating the existing app to mongos is a separate step, not part of this chapter (see the Q&A at the end).

RAM caveat, stated up front: 5 extra mongods on an 8 GB laptop (WSL itself showed only 3.8 GB total). Fallback if it got slow: drop to 2 shards. It did not come to that.

Final compose blocks for the 5 new services (commands as verified after all fixes in Step 2):

```yaml
  config:
    image: mongo:7
    container_name: social-lab-config
    command: ["--configsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "configrs", "--wiredTigerCacheSizeGB", "0.25"]
    ports:
      - "27020:27017"
    volumes:
      - config_data:/data/db
  shard1:
    image: mongo:7
    container_name: social-lab-shard1
    command: ["--shardsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "shard1rs", "--wiredTigerCacheSizeGB", "0.25"]
    ports:
      - "27021:27017"
    volumes:
      - shard1_data:/data/db
  shard2:
    image: mongo:7
    container_name: social-lab-shard2
    command: ["--shardsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "shard2rs", "--wiredTigerCacheSizeGB", "0.25"]
    ports:
      - "27022:27017"
    volumes:
      - shard2_data:/data/db
  shard3:
    image: mongo:7
    container_name: social-lab-shard3
    command: ["--shardsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "shard3rs", "--wiredTigerCacheSizeGB", "0.25"]
    ports:
      - "27023:27017"
    volumes:
      - shard3_data:/data/db
  mongos:
    image: mongo:7
    container_name: social-lab-mongos
    command: ["mongos", "--configdb", "configrs/social-lab-config:27017", "--bind_ip_all"]
    ports:
      - "27024:27017"
    depends_on:
      - config
```

Top-level volumes added: `config_data`, `shard1_data`, `shard2_data`, `shard3_data`.

WHY the explicit `--port 27017` and `--dbpath /data/db` are there is explained by Bug 3 in Step 2. They were not in the first version of the patch; they were added after the failure proved them necessary.

## Step 1 — Baseline (verified 2026-10-28 10:19)

The stack had restarted. Containers `social-lab-app`, `social-lab-mongo`, `social-lab-mongo2`, `social-lab-mongo3` were all `Up 38 minutes`.

One surprise, owned as a wrong prediction: **the primary had flipped**. The assistant predicted mongo2 PRIMARY (it was PRIMARY at the end of Chapter 22). Actual state:

- mongo1 PRIMARY, mongo2 SECONDARY, mongo3 ARBITER.

WHY: a restart triggers an election, and mongo1 won it. This is normal behaviour, and the app is unaffected because the driver re-discovers the primary by itself (proven in Chapter 22).

App posts count at baseline: **16**. The app itself was untouched throughout this chapter.

## Step 2 — Cluster up (three real bugs, then success)

### 2a. First `up`: shards in a restart loop

Patch `/tmp/ch23_cluster_patch.py` added the 5 service blocks (before `prometheus:`) and the 4 volumes (after top-level `redis_data:`), with `must()` asserts and a backup at `/tmp/docker-compose.yml.ch23.bak`. Then:

```
docker compose config --quiet && echo "compose syntax OK"
docker compose up -d
docker ps --format "{{.Names}} {{.Status}}" | grep -E "config|shard|mongos"
```

Result:

```
social-lab-mongos Up 1 second
social-lab-shard2 Up Less than a second
social-lab-config Up 1 second
social-lab-shard3 Restarting (2) Less than a second ago
social-lab-shard1 Restarting (2) Less than a second ago
```

And the config initiate failed immediately:

```
docker exec social-lab-config mongosh --quiet --eval 'rs.initiate({_id: "configrs", configsvr: true, members: [{_id: 0, host: "social-lab-config:27017"}]})'
MongoNetworkError: connect ECONNREFUSED 127.0.0.1:27017
```

### 2b. Bug 1: a shardsvr cannot be standalone

Diagnostics run:

```
docker ps --format "{{.Names}} {{.Status}}" | grep -E "config|shard|mongos"
docker inspect social-lab-shard1 --format "shard1 OOMKilled: {{.State.OOMKilled}}"
docker inspect social-lab-shard3 --format "shard3 OOMKilled: {{.State.OOMKilled}}"
docker logs social-lab-shard1 --tail 30
docker logs social-lab-shard3 --tail 30
docker logs social-lab-config --tail 30
free -h
```

Findings:

- `shard1 OOMKilled: false`, `shard3 OOMKilled: false` — **not a memory problem**.
- Shard logs: `BadValue: Cannot start a shardsvr as a standalone server. Please use the option --replSet to start the node as a replica set.`
- Config log tail: FTDC warnings `Collection [local.oplog.rs] not found` — normal before initiation, not an error.
- `free -h`: Mem total 3.8 GiB, available 1.8 GiB — tight, but not the cause.

Root cause, owned honestly: the assistant had said a standalone shard would work for learning. **MongoDB 7 refuses it.** Every shard must be a replica set, even a single-member one.

Fix — patch `/tmp/ch23_shardrs_patch.py` (run from `apps/social-lab`, backup `/tmp/docker-compose.yml.ch23b.bak`). It finds each shard's service block and inserts that shard's own `--replSet` into its command line:

```python
p = "docker-compose.yml"
s = open(p).read()
open("/tmp/docker-compose.yml.ch23b.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must('"--shardsvr"', 3)
for shard, rs in [("shard1", "shard1rs"), ("shard2", "shard2rs"), ("shard3", "shard3rs")]:
    b = s.find("  " + shard + ":\n")
    assert b != -1, "block not found: " + shard
    c = s.find('command: ["--shardsvr"', b)
    assert c != -1, "command not found: " + shard
    ins = c + len('command: ["--shardsvr"')
    s = s[:ins] + ', "--replSet", "' + rs + '"' + s[ins:]
open(p, "w").write(s)
print("shard replica sets added")
```

```
python3 /tmp/ch23_shardrs_patch.py
docker compose config --quiet && echo "compose syntax OK"
docker compose up -d
sleep 10
docker ps --format "{{.Names}} {{.Status}}" | grep -E "config|shard|mongos"
```

Result: all three shards `Up About a minute`. But every initiate still failed with `ECONNREFUSED` — including config, which had been Up for 7 minutes. So there was a second, systematic problem.

### 2c. Bug 2 investigation: everything refuses, yet mongod is running

Diagnostics run:

```
docker exec social-lab-mongo mongosh --quiet --eval 'db.runCommand({ping: 1})'
docker inspect social-lab-shard1 --format '{{.Config.Cmd}}'
docker inspect social-lab-config --format '{{.Config.Cmd}}'
docker top social-lab-config
docker logs social-lab-config --tail 30
docker logs social-lab-shard1 --tail 30
```

Findings:

- rs0 mongod answered ping with `{ ok: 1 }` — Docker and the host are fine; the problem is only the new containers.
- Shard1 was actually running `[--shardsvr --replSet shard1rs --wiredTigerCacheSizeGB 0.25]` and config `[--configsvr --replSet configrs --wiredTigerCacheSizeGB 0.25]` — **the patch had applied**.
- `docker top` showed mongod alive in config since 10:21 (PID 7171, only 11 seconds of CPU in 9 minutes — idle, not stuck in heavy startup), with `--bind_ip_all` appended by the image entrypoint.
- Logs showed FTDC running — which only happens inside a running mongod.

So: mongod processes were alive, yet port 27017 refused every connection. The answer was in the head of the config log, not the tail.

### 2d. Bug 3: configsvr/shardsvr default ports and dbpaths

```
docker exec social-lab-config mongosh --quiet --eval 'db.runCommand({ping: 1})'
docker logs social-lab-config 2>&1 | head -60
```

The startup line gave it away:

```json
{"msg": "MongoDB starting", "attr": {"pid": 1, "port": 27019, "dbPath": "/data/configdb"}}
```

**A `--configsvr` mongod's own defaults are port 27019 and dbpath `/data/configdb`** (a `--shardsvr` mongod defaults to port 27018). The compose file mapped host ports to container port 27017 and mounted volumes on `/data/db` — so nothing listened on 27017, and data would have landed on the container layer instead of the named volume.

Fix — patch `/tmp/ch23_port_patch.py` (run from `apps/social-lab`, backup `/tmp/docker-compose.yml.ch23c.bak`). It inserts explicit `--port 27017` and `--dbpath /data/db` into the config and shard commands:

```python
p = "docker-compose.yml"
s = open(p).read()
open("/tmp/docker-compose.yml.ch23c.bak", "w").write(s)
def must(sub, n=1):
    c = s.count(sub)
    assert c == n, "found: " + str(c) + " for " + repr(sub)
must('command: ["--configsvr", "--replSet", "configrs"', 1)
must('"--shardsvr", "--replSet", "shard1rs"', 1)
must('"--shardsvr", "--replSet", "shard2rs"', 1)
must('"--shardsvr", "--replSet", "shard3rs"', 1)
s = s.replace('command: ["--configsvr", "--replSet", "configrs"', 'command: ["--configsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "configrs"', 1)
s = s.replace('"--shardsvr", "--replSet", "shard1rs"', '"--shardsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "shard1rs"', 1)
s = s.replace('"--shardsvr", "--replSet", "shard2rs"', '"--shardsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "shard2rs"', 1)
s = s.replace('"--shardsvr", "--replSet", "shard3rs"', '"--shardsvr", "--port", "27017", "--dbpath", "/data/db", "--replSet", "shard3rs"', 1)
open(p, "w").write(s)
print("ports fixed")
```

No data was lost in the recreate: configrs had never been initiated, so the old container layer's `/data/configdb` held nothing valuable.

### 2e. Success (verified 2026-10-28 10:34–10:35)

```
python3 /tmp/ch23_port_patch.py
docker compose config --quiet && echo "compose syntax OK"
docker compose up -d
sleep 10
docker ps --format "{{.Names}} {{.Status}}" | grep -E "config|shard|mongos"
docker exec social-lab-config mongosh --quiet --eval 'db.runCommand({ping: 1})'
docker exec social-lab-shard1 mongosh --quiet --eval 'db.runCommand({ping: 1})'
```

Result: all containers Up, both pings `{ ok: 1 }`. (Mongos showed `Up 13 minutes` — it was never recreated because its command never changed.)

Then the full initiation sequence:

```
docker exec social-lab-config mongosh --quiet --eval 'rs.initiate({_id: "configrs", configsvr: true, members: [{_id: 0, host: "social-lab-config:27017"}]})'
sleep 10
docker exec social-lab-config mongosh --quiet --eval 'rs.status().members.forEach(function (m) { print(m.name, m.stateStr, "health:", m.health); })'
docker exec social-lab-shard1 mongosh --quiet --eval 'rs.initiate({_id: "shard1rs", members: [{_id: 0, host: "social-lab-shard1:27017"}]})'
docker exec social-lab-shard2 mongosh --quiet --eval 'rs.initiate({_id: "shard2rs", members: [{_id: 0, host: "social-lab-shard2:27017"}]})'
docker exec social-lab-shard3 mongosh --quiet --eval 'rs.initiate({_id: "shard3rs", members: [{_id: 0, host: "social-lab-shard3:27017"}]})'
sleep 10
docker exec social-lab-shard1 mongosh --quiet --eval 'print("shard1:", rs.status().members[0].stateStr)'
docker exec social-lab-shard2 mongosh --quiet --eval 'print("shard2:", rs.status().members[0].stateStr)'
docker exec social-lab-shard3 mongosh --quiet --eval 'print("shard3:", rs.status().members[0].stateStr)'
docker exec social-lab-mongos mongosh --quiet --eval 'sh.addShard("shard1rs/social-lab-shard1:27017")'
docker exec social-lab-mongos mongosh --quiet --eval 'sh.addShard("shard2rs/social-lab-shard2:27017")'
docker exec social-lab-mongos mongosh --quiet --eval 'sh.addShard("shard3rs/social-lab-shard3:27017")'
docker exec social-lab-mongos mongosh --quiet --eval 'db.adminCommand({listShards: 1})'
```

Verified results:

- Configrs initiate `{ ok: 1 }`, then `social-lab-config:27017 PRIMARY health: 1`.
- All three shard initiates `{ ok: 1 }`, then `shard1: PRIMARY`, `shard2: PRIMARY`, `shard3: PRIMARY`.
- All three `addShard` calls `ok: 1` (`shardAdded: shard1rs`, `shard2rs`, `shard3rs`).
- `listShards` showed 3 shards, each `state: 1`:
  - `shard1rs/social-lab-shard1:27017`
  - `shard2rs/social-lab-shard2:27017`
  - `shard3rs/social-lab-shard3:27017`

## Step 3 — Seed + hashed shard key (verified 2026-10-28 10:36–10:39)

WHY hashed: a hashed key spreads values across the whole hash range, so a new user can land on any shard — no single shard gets hot.

WHY shard first, seed second: `numInitialChunks` only works on an **empty** collection. Creating 6 chunks up front is what lets even small data spread — otherwise there would be a single chunk on a single shard and the distribution would show nothing. **The chunk, not the individual document, is the unit of distribution** — that is this step's real lesson.

Shard the empty collection (via mongos; the `shardlab` database is new, so `enableSharding` and `shardCollection` both run here):

```
docker exec social-lab-mongos mongosh --quiet --eval 'sh.enableSharding("shardlab"); sh.shardCollection("shardlab.posts", {username: "hashed"}, false, {numInitialChunks: 6})'
```

Result: `ok: 1` with `collectionsharded: shardlab.posts`.

Seed script `/tmp/ch23_seed.js` — 6 users × 1000 posts = 6000 documents, each padded to about 490 bytes:

```js
const sdb = db.getSiblingDB("shardlab");
const users = ["ali", "rao", "zain", "sara", "omar", "hina"];
let batch = [];
let n = 0;
for (let i = 0; i < 6000; i++) {
  batch.push({username: users[i % users.length], content: "shardlab post " + i + " " + "x".repeat(400), created_at: new Date()});
  if (batch.length === 1000) { sdb.posts.insertMany(batch); n += batch.length; batch = []; }
}
if (batch.length) { sdb.posts.insertMany(batch); n += batch.length; }
print("seeded:", n, "total:", sdb.posts.countDocuments());
```

```
docker exec -i social-lab-mongos mongosh --quiet < /tmp/ch23_seed.js
```

Result: `seeded: 6000 total: 6000`.

Distribution check:

```
docker exec social-lab-mongos mongosh --quiet --eval 'print("total:", db.getSiblingDB("shardlab").posts.countDocuments()); db.getSiblingDB("shardlab").posts.getShardDistribution()'
```

Verified result — textbook even:

```
total: 6000
Shard shard1rs at shard1rs/social-lab-shard1:27017
{ data: '963KiB', docs: 2000, chunks: 2, 'estimated data per chunk': '481KiB', 'estimated docs per chunk': 1000 }
---
Shard shard3rs at shard3rs/social-lab-shard3:27017
{ data: '963KiB', docs: 2000, chunks: 2, 'estimated data per chunk': '481KiB', 'estimated docs per chunk': 1000 }
---
Shard shard2rs at shard2rs/social-lab-shard2:27017
{ data: '964KiB', docs: 2000, chunks: 2, 'estimated data per chunk': '482KiB', 'estimated docs per chunk': 1000 }
---
Totals
{ data: '2.82MiB', docs: 6000, chunks: 6,
  'Shard shard1rs': [ '33.32 % data', '33.33 % docs in cluster', '493B avg obj size on shard' ],
  'Shard shard3rs': [ '33.32 % data', '33.33 % docs in cluster', '493B avg obj size on shard' ],
  'Shard shard2rs': [ '33.35 % data', '33.33 % docs in cluster', '493B avg obj size on shard' ] }
```

6 chunks, 2 per shard, 2000 documents each — the hash spread exactly as designed.

## Step 4 — Hot shard demo with a range key (verified 2026-10-28 10:41–10:42)

The story: one viral user posts constantly. The range key is `{created_at: 1}` — time order. Every new post is "now", so every new post falls into the same (last) chunk, and that chunk sits on a single shard. The balancer cannot help, because there is only one chunk to move — nothing to balance.

Routing is by `created_at`, not by username; the username `viraluser` is only the story for why so many new posts appear at once.

Shard the new collection — deliberately **without** `numInitialChunks` this time, because a single chunk is the whole point. The database `shardlab` is already sharding-enabled, so no `enableSharding` is needed again:

```
docker exec social-lab-mongos mongosh --quiet --eval 'sh.shardCollection("shardlab.hotposts", {created_at: 1})'
```

Result: `collectionsharded: shardlab.hotposts`, `ok: 1`.

Seed script `/tmp/ch23_hotseed.js` — 3000 posts, all from `viraluser`, all with the current timestamp:

```js
const sdb = db.getSiblingDB("shardlab");
let batch = [];
let n = 0;
for (let i = 0; i < 3000; i++) {
  batch.push({username: "viraluser", content: "hot post " + i + " " + "x".repeat(400), created_at: new Date()});
  if (batch.length === 1000) { sdb.hotposts.insertMany(batch); n += batch.length; batch = []; }
}
if (batch.length) { sdb.hotposts.insertMany(batch); n += batch.length; }
print("hot seeded:", n, "total:", sdb.hotposts.countDocuments());
```

```
docker exec -i social-lab-mongos mongosh --quiet < /tmp/ch23_hotseed.js
```

Result: `hot seeded: 3000 total: 3000`.

Distribution check:

```
docker exec social-lab-mongos mongosh --quiet --eval 'print("total:", db.getSiblingDB("shardlab").hotposts.countDocuments()); db.getSiblingDB("shardlab").hotposts.getShardDistribution()'
```

Verified result — the exact opposite of Step 3:

```
total: 3000
Shard shard3rs at shard3rs/social-lab-shard3:27017
{ data: '1.41MiB', docs: 3000, chunks: 1, 'estimated data per chunk': '1.41MiB', 'estimated docs per chunk': 3000 }
---
Totals
{ data: '1.41MiB', docs: 3000, chunks: 1,
  'Shard shard3rs': [ '100 % data', '100 % docs in cluster', '493B avg obj size on shard' ] }
```

One shard, one chunk, 100% of the data. The other two shards hold nothing. Note: the hot chunk sits on shard3rs, which is `shardlab`'s primary shard — a newly sharded collection's first chunk is created on the database's primary shard.

## Comparison — hashed vs range

| | Step 3: hashed (`username`) | Step 4: range (`created_at`) |
|---|---|---|
| Documents | 6000 | 3000 |
| Chunks | 6 | 1 |
| shard1rs | 2000 (33.33%) | 0 |
| shard2rs | 2000 (33.33%) | 0 |
| shard3rs | 2000 (33.33%) | 3000 (100%) |

Lesson: **a hash spreads, a range clusters.** Time-ordered writes all pile onto the newest end of a range key. A range key is only safe when writes spread across the key space (or when range queries matter more than write spread); a hashed key is the safe default for write-heavy data.

## Q&A during the chapter

### Why don't the 6000 and 3000 test posts show in the app's web UI?

They were never supposed to. This was checked live during the chapter and is not a bug:

- The app reads from rs0 (mongo1/2/3) and its own database — 16 posts. Those posts show fine on both the All Posts and Following tabs, with no browser console errors, after a hard refresh.
- The test data lives in a different cluster (mongos) and a different database (`shardlab`). The UI has no route to it.
- The separation was deliberate (see Design): the sharding experiment must not touch the app's real data.

### How do you shard an app that is already running with data? Is the data lost?

No — done the standard way, no data is lost, because the existing replica set itself becomes the first shard. Nothing is dumped and restored:

1. **Backup first** (`mongodump` or a snapshot). Not because data will be lost, but so a mistake is recoverable. (Chapter 21 lesson: replication is not backup.)
2. **Add the existing replica set as a shard**: `sh.addShard("rs0/mongo1:27017,mongo2:27017,mongo3:27017")`. The data stays exactly where it is; nothing moves in this step.
3. **Point the app at mongos** instead of the replica set hosts. To the driver, mongos looks like an ordinary MongoDB. This needs an app restart (a rolling restart gives zero downtime with multiple instances).
4. **Shard the collection**: `sh.shardCollection("appdb.posts", {username: "hashed"})`. At first all data is in one chunk on the first shard; then the **balancer** splits and migrates chunks to the other shards in the background while the app keeps running. No downtime — only extra balancer load, which is why large migrations are often run off-peak.

Data is lost only by mistake: an operation without a backup (such as a `drop` on the wrong database), or a badly chosen shard key. Two key rules:

- A shard key is hard to change later (resharding exists but is heavy), so choose it carefully up front.
- The key field should exist in **every** document. Documents missing the key are treated as `null` and all pile into one chunk — the same hot-chunk problem as Step 4. For existing data, **hashed `_id`** is the safest default: it exists in every document, is unique, and spreads evenly.

Applied to social-lab, migration would mean: add rs0 with `sh.addShard`, point the app's `MONGO_URI` at mongos, then shard the posts collection — the 16 posts would still be there, on the UI, nothing missing. That migration is a separate step, not part of this chapter.

## Step 5 — Cleanup (verified 2026-10-28 10:53–10:56)

Test data dropped (mongos was briefly restarted for this, then stopped again):

```
cd ~/system-design/apps/social-lab && docker compose up -d config mongos
sleep 10
docker exec social-lab-mongos mongosh --quiet --eval 'db.getSiblingDB("shardlab").dropDatabase()'
docker exec social-lab-mongos mongosh --quiet --eval 'print("posts:", db.getSiblingDB("shardlab").posts.countDocuments(), "hotposts:", db.getSiblingDB("shardlab").hotposts.countDocuments())'
docker compose stop config mongos
```

Results: drop `{ ok: 1, dropped: 'shardlab' }`, counts `posts: 0 hotposts: 0`.

Earlier, the whole cluster had already been stopped:

```
cd ~/system-design/apps/social-lab && docker compose stop config shard1 shard2 shard3 mongos
docker ps -a --format "{{.Names}} {{.Status}}" | grep -E "config|shard|mongos"
curl -s -o /dev/null -w "feed: %{http_code}\n" "http://localhost:8000/api/v1/feed?limit=5"
cd ~/system-design && git status --short
```

Results: stop 5/5 (config, shard1, shard2, shard3, mongos). All five `Exited` — mongos showed `Exited (137)` because it took 10.3 s to stop and compose sent SIGKILL; that is a stop-time kill, not a crash. Feed stayed `200` — the app and rs0 were never touched. Git status showed only `M apps/social-lab/docker-compose.yml`.

Final state after cleanup: test data gone, cluster stopped (no RAM/CPU use), services retained in `docker-compose.yml` and volumes retained — bringing the cluster back is just `docker compose up -d` (plus re-initiation is not needed: replica set configs persist in the volumes). Removing the services from compose entirely is a separate decision, not taken here.

## Limitations

- **Learning-only topology.** The config server is a single member (production: 3 members) and each shard is a single mongod (production: each shard is a replica set). A single-member failure here loses that shard's data; in production it would not.
- **The app still runs on rs0.** Nothing in this chapter moved the app to mongos. The migration path is documented in the Q&A above and is a separate step.
- **Small-data distribution is coarse.** With 6000 documents the spread looked perfect only because 6 chunks were pre-created. In general, distribution granularity is the chunk (default 64 MB), not the document — small collections will not spread evenly.
- **Balancer timing.** Chunk migration is asynchronous. Distribution snapshots taken mid-migration would show intermediate states; the numbers in this chapter were taken after the cluster settled.
- **Host resources.** WSL reported only 3.8 GB total RAM. The cluster ran, but on a bigger dataset the 0.25 GB WiredTiger cache cap per mongod would need revisiting.

## Close

Three pillars, as required for every chapter:

1. **Code commit + push** — `apps/social-lab/docker-compose.yml` only (5 new services + 4 volumes, with the replica-set and explicit port/dbpath fixes from Step 2).
2. **Docs commit + push** — this README at `chapters/23-sharding/README.md`.
3. **Git tag `ch23-sharding`** on the docs commit, pushed — plus a Notion page under DEVOPS with the full content, fetch-verified (hashes and Notion page id recorded there).

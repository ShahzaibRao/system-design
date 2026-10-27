# Chapter 21: Replication + Read/Write Split

**Concept:** aik database ki copies (replicas) banana — reads bat jati hain, aur primary mar jaye to koi doosra us ki jagah le leta hai. Lekin copy hamesha taaza nahi hoti (lag), aur yehi lag stale reads ka sabab banta hai.

**Builds on:** Ch03 (Mongo), Ch13 (Redis — eventual consistency ka pehla sabak), Ch18 (cache staleness), Ch20 (indexing — DB bottleneck ki pehli misaal).

**Unlocks:** sharding (writes scale karna — replication se writes scale nahi hote), phir Kafka (async writes).

---

## Foundations (is chapter se pehle samajhna zaroori tha)

### Relational vs NoSQL

- **Relational (SQL):** tables, rows, fixed columns, joins. PostgreSQL, MySQL. Structure pakka ho aur rishton par sawal zyada hon to behtareen.
- **Document (MongoDB):** JSON jaisa document, flexible schema. Hamara social-lab isi liye Mongo par hai — post + comments aik document me.
- **Key-Value (Redis):** key se seedha value, sab se tez. Hamara Redis: cache, sessions, HLL counters.
- **Wide-Column (Cassandra):** bohot zyada writes ke liye (IoT, logs).
- **Graph (Neo4j):** vertex (node: user, post) + edge (rishta: follows, likes). Sawal jaise "doston ke dost kon?" — joins se slow, graph par tez.
- **Vector (Pinecone, pgvector):** AI text/image ko numbers ki list (embedding) me badalta hai; milte-julte matlab wale vectors qareeb hote hain. Semantic search, RAG. **Vertex ≠ vector:** vertex graph ka node hai, vector AI ki numbers-list hai.

### ACID vs BASE

- **ACID:** Atomicity (sab ya kuch nahi), Consistency (rules na tooten), Isolation (parallel kaam adhoora data na dikhayen), Durability (commit ke baad mehfooz).
- **BASE:** Basically Available, Soft state, Eventually consistent — availability/scale ke liye thodi der purana data qabool, consistency baad me aa jati hai (hamara feed cache yehi hai).

### Isolation levels (SQL-92)

Masle: dirty read (commit-na-hua data parhna), non-repeatable read (dobara parho to alag jawab), phantom read (dobara query par zyada rows).

- **Read Uncommitted:** koi protection nahi, sab se tez, sab se khatarnak. Paisay wali jagah kabhi nahi.
- **Read Committed:** sirf committed data — dirty read khatam. PostgreSQL/Oracle default. Aam apps ke liye kaafi.
- **Repeatable Read:** aik transaction me dobara wahi jawab — non-repeatable khatam. MySQL default.
- **Serializable:** sab se sakht, jaise serial chal rahi hon — koi masla nahi, sab se slow. Bank/booking.

Usool: jitni sakhti, utni slowness.

### Vertical vs Horizontal scaling

- **Vertical (scale up):** aik machine bari karo. Aasan, lekin hadd hai, mehnga, single point of failure.
- **Horizontal (scale out):** machines barhao. Hadd nahi, lekin mushkil. Replication aur sharding isi ke tareeqe hain.

### Replication: sync vs async

- **Sync:** primary tab tak "ok" nahi kehta jab tak secondary ne likh na liya ho. Faida: data kabhi gum nahi. Nuqsan: slow, secondary down ho to write ruk jata hai.
- **Async:** primary foran "ok" kehta hai, secondary ko baad me bhejta hai. Faida: tez writes. Nuqsan: crash par taaza data gum ho sakta hai, secondary lag (peeche) rehta hai → stale reads.

### Read/write split

Writes sirf primary par, reads secondaries par — read load bat jata hai. **Lekin write load primary par hi rehta hai** — writes scale karne ke liye sharding chahiye.

### Partitioning / Sharding (agla topic, yahan sirf matrix)

| Tareeqa | Kab | Faida | Nuqsan |
|---|---|---|---|
| Range | Range queries zyada hon | Range query tez | Hot partition (naya data aik shard par) |
| Hash | Load barabar baantna ho | Load barabar, hot spot nahi | Range query mushkil (scatter-gather) |
| List | Geo/category alag rakhna ho | Compliance/geo aasan | Unbalanced ho sakta hai |
| Directory | Shards move karne hon | Sab se flexible | Lookup table bottleneck ban sakti hai |
| Geo | Latency kam karni ho | Qareebi data, tez response | Cross-region sawal mushkil |

Partition key ki 3 shartein: zyada unique values, load barabar bate, query pattern se match kare. Ghalat key = hot shard (viral user ka saara load aik shard par).

---

## Step 1 — Baseline (verified)

```
grep -A5 "social-lab-mongo" docker-compose.yml
docker exec social-lab-mongo mongosh --quiet --eval "try { rs.status(); } catch (e) { print('replica set error:', e.codeName || e.message); }"
```

Outputs: compose me koi `--replSet` nahi tha (sirf container, port 27017, volume `mongo_data`); `rs.status()` error: `NoReplicationEnabled`. Matlab: aik Mongo, koi copy nahi, koi failover nahi.

## Step 2a — Compose patch (BEFORE/AFTER)

BEFORE (mongo service):

```yaml
  mongo:
    image: mongo:7
    container_name: social-lab-mongo
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db
    restart: unless-stopped
```

AFTER — mongo service me command lagi, aur 2 nayi services + volumes:

```yaml
  mongo:
    image: mongo:7
    container_name: social-lab-mongo
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db
    command: ["--replSet", "rs0", "--wiredTigerCacheSizeGB", "0.25"]
    restart: unless-stopped

  mongo2:
    image: mongo:7
    container_name: social-lab-mongo2
    ports:
      - "27018:27017"
    volumes:
      - mongo2_data:/data/db
    command: ["--replSet", "rs0", "--wiredTigerCacheSizeGB", "0.25"]
    restart: unless-stopped

  mongo3:
    image: mongo:7
    container_name: social-lab-mongo3
    ports:
      - "27019:27017"
    volumes:
      - mongo3_data:/data/db
    command: ["--replSet", "rs0", "--wiredTigerCacheSizeGB", "0.25"]
    restart: unless-stopped
```

WHY `--wiredTigerCacheSizeGB 0.25`: laptop par 3 mongod halke rahen. WHY mongo3 arbiter: data nahi rakhta, sirf vote deta hai — election ke liye 3 votes chahiye, warna 2 members me faisla nahi hota. Patch verify: teenon `must()` found: 1 (backup `/tmp/docker-compose.yml.ch21.bak`), `docker compose config --quiet` OK.

## Step 2b — Initiate + verify (outputs)

```
docker compose up -d
docker exec social-lab-mongo mongosh --quiet --eval 'rs.initiate({_id: "rs0", members: [{_id: 0, host: "social-lab-mongo:27017"}, {_id: 1, host: "social-lab-mongo2:27017"}, {_id: 2, host: "social-lab-mongo3:27017", arbiterOnly: true}]})'
{ ok: 1 }

social-lab-mongo:27017 PRIMARY health: 1
social-lab-mongo2:27017 SECONDARY health: 1
social-lab-mongo3:27017 ARBITER health: 1

posts count: 15
```

Data mehfooz raha (wahi `mongo_data` volume, sirf flag laga).

## Demo A — Write primary par, read secondary se

```
docker exec social-lab-mongo mongosh --quiet --eval 'db.getSiblingDB("social_lab").repltest.insertOne({msg: "repltest-1"})'
{ acknowledged: true, insertedId: ObjectId('6ae048ccb48331e23facc463') }

docker exec social-lab-mongo2 mongosh --quiet --eval 'db.getMongo().setReadPref("secondary"); printjson(db.getSiblingDB("social_lab").repltest.findOne())'
{ _id: ObjectId('6ae048ccb48331e23facc463'), msg: 'repltest-1' }
```

WHY `setReadPref("secondary")`: secondary par default read mana hai — secondary se parhne ke liye ye kehna parta hai ke "mujhe secondary se parhna qabool hai", aur isi qabool karne me stale read ka risk chhupa hai.

## Demo B — Sync vs async (secondary band karke)

```
docker stop social-lab-mongo2
insert {msg: "repltest-async"} (default w:1) → acknowledged: true
insert {msg: "repltest-sync"} (w: "majority", wtimeout: 5000) → MongoWriteConcernError: waiting for replication timed out
docker start social-lab-mongo2
secondary repltest count → 3
```

WHY majority fail hui: majority ke liye 2 acks chahiye, secondary down tha, aur arbiter vote to deta hai lekin data nahi rakhta, is liye write ka ack nahi de sakta. Election vote aur write ack alag cheezein hain.

Bareek nuqta (sabit hua): error ka matlab ye **nahi** ke document likha hi nahi gaya — primary ne usay likh liya tha, sirf majority ack nahi mila. Restart ke baad count 3 aaya (repltest-1, async, sync) — async write bhi catchup se pahunch gaya, aur "failed" sync document bhi gum nahi hua tha. Uncertain outcome ka matlab yehi hai: error aane par likha hai ya nahi, ye dobara parh kar hi pata chalta hai.

## Demo C — Failover (primary band karo)

```
docker stop social-lab-mongo
social-lab-mongo:27017 (not reachable/healthy) health: 0
social-lab-mongo2:27017 PRIMARY health: 1
social-lab-mongo3:27017 ARBITER health: 1
feed http code: 500

docker start social-lab-mongo
social-lab-mongo:27017 SECONDARY health: 1
social-lab-mongo2:27017 PRIMARY health: 1

rs.stepDown() on mongo2 → { ok: 1 }
social-lab-mongo:27017 PRIMARY health: 1
social-lab-mongo2:27017 SECONDARY health: 1
feed http code: 200
```

Do sabak:

1. **Election kaam karti hai:** primary marne par mongo2 arbiter ke vote se PRIMARY ban gaya. Wapas aane par mongo1 khud-ba-khud PRIMARY nahi bana (SECONDARY ke tor par wapas aaya) — dobara election ke liye `stepDown()` karna para.
2. **App ke paas failover nahi tha:** maine pehle "connection refused" kaha tha, asal me feed ne **500** diya — kyun ke uvicorn zinda tha aur us ne DB error ko 500 me badal diya. Natija wahi hai (app down rahi), shakal farq thi. Production app replicaSet URI + retry use karta hai, is liye usay failover nazar bhi nahi aata.

---

## Comparison

| | Pehle (standalone) | Baad (replica set) |
|---|---|---|
| Copies | 1 (sirf volume) | 3 members (primary + secondary + arbiter) |
| Read load | Sirf aik mongod | Primary + secondary par bat sakta hai |
| Primary mar jaye | App band, haath se recover | Election se naya primary (seconds me) |
| Write guarantee | Sirf primary ack (w:1) | w:majority se 2 members tak mehfooz |
| Stale read ka risk | Nahi (aik hi copy) | Hai (secondary lag) — secondary read ki keemat |
| Write scale | Aik mongod ki hadd | Wahi hadd (writes primary par hi) — is ke liye sharding chahiye |

---

## Cleanup (verified)

Test collection drop ki gayi (repltest) — asal posts (15) aur users ko haath nahi lagaya. Replica set rehta hai (ye architecture hai, test data nahi).

## Limitations (imaandari se)

- **App ka connection wahi hai** (`localhost:27017`, baghair replicaSet ke) — laptop par container names host se resolve nahi hote, is liye app-level replicaSet URI is demo me nahi lagaya. Replica set ka kaam mongosh + direct connections se dikhaya gaya hai. Production fix: replicaSet URI + retryWrites + readPreference.
- **Local lag milliseconds me hai** — is liye stale read ko race me pakarna mushkil tha; lag ka saboot stop/restart + catchup se diya gaya hai (production me network distance par lag seconds/minutes hota hai).
- **Arbiter data nahi rakhta** — wo election me vote deta hai, write durability me hissa nahi leta.
- **StepDown ke baad** mongo1 primary bana, lekin ye haath se kiya gaya — production me aam tor par primary wapas aane par dobara election nahi hoti (jo primary hai, wahi rehta hai).

---

## Close

- Code: `docker-compose.yml` (mongo command + mongo2/mongo3 services + volumes).
- Tag: `ch21-replication` (docs commit par).
- Commit/tag/Notion hashes: Notion page par (fetch-verified).
- Agla topic: sharding (partitioning methods + partition key + hot shard).

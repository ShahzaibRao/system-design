# Chapter 03 - Database (MongoDB)

## Goal
Move users from in-memory storage to MongoDB, so data survives an app restart.
Passwords were already hashed in Chapter 02, so only hashes reach the database.

## Step 1 - MongoDB via Docker Compose
- File: `apps/social-lab/docker-compose.yml`
- Service: `mongo:7`, container `social-lab-mongo`, host port `27017:27017`
- Volume: `mongo_data` on `/data/db`, so data survives a container recreate.
- Dependency added: `pymongo` in `requirements.txt`.
- Verified: `client.admin.command('ping')` returned `{'ok': 1.0}`.

### Troubleshooting lesson in this step
- First pymongo ping failed with Connection refused on localhost:27017.
- `docker ps` showed the container Up, but PORTS was empty.
- `docker port` was empty, `docker logs` showed Mongo running inside, `ss` showed host 27017 not listening.
- Cause: a stale container created without port mapping, plain `docker compose up -d` did not recreate it.
- Fix: `docker compose down` (WITHOUT `-v`, so the volume survived) then `docker compose up -d`.
- After recreate: `docker port` showed `27017/tcp -> 0.0.0.0:27017`, `ss` showed LISTEN, pymongo ping passed.

## Step 2 - App code to MongoDB
- File: `apps/social-lab/backend/app/main.py`
- Connection: `MONGO_URI` env var, default `mongodb://localhost:27017`.
- Database: `social_lab`, collection: `users`.
- Unique indexes: `username` unique, `email` unique.
  App checks first, but the DB index is the final guard.
  A race between two simultaneous signups is caught by `DuplicateKeyError`, returned as 400.
- Signup: find username/email, hash with bcrypt (Chapter 02), insert, return `str(inserted_id)`.
- Login: `find_one` by username, `bcrypt.checkpw` against the stored hash.
- In-memory dict was removed completely.

## Test results, in order
- Fresh signup: user created in MongoDB
- Login: login successful
- Stop uvicorn, restart uvicorn, login again WITHOUT signup: login successful
  This is the persistence proof. The in-memory version failed this test.
- Duplicate signup: 400, username taken
- Wrong password: 401, invalid username or password

## Limitations, still present
- Single MongoDB node only, no replica set, no sharding yet.
- No username filter, no login rate limiting, no frontend page yet.
- No Redis cache yet.
- App still runs with local uvicorn, not inside Docker yet.

## Next steps, one at a time
1. Username availability filter (Bloom / Cuckoo)
2. Login rate limiting, with an attack script
3. Signup and login frontend pages
4. Redis cache, replication and sharding later, when those chapters arrive

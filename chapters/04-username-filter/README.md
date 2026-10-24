# Chapter 04 - Username Availability Filter (Bloom)

## Goal
Answer "is this username taken?" without a database query for every check.
The database stays the truth, the filter in RAM answers first.

## How a Bloom filter works
- A bit array, all bits off at start.
- Adding a username turns on a few bits, chosen by hash functions.
- Checking: if any bit is off, the answer is certain: definitely NOT present.
- If all bits are on, the answer is only "maybe present".
  That maybe case must be confirmed by the database, because of false positives.
- A false positive costs one extra database query, never a wrong answer,
  because the database check is always the final step.

## Step 1 - Standalone Bloom filter
- File: `apps/social-lab/backend/app/core/bloom.py`
- No new dependency. Double hashing with md5 and sha256.
- Parameters: capacity 10000 usernames, error rate 1%.
- Verified: added `rao` -> in filter True, never added `ali` -> False.
- Size: 95850 bits, 11.7 KB, 6 hash functions.
  10000 usernames fit in about 11.7 KB in the filter.

## Step 2 - Wiring into signup and username check
- File: `apps/social-lab/backend/app/main.py`
- New endpoint: `GET /api/v1/auth/check-username?username=<name>`
  Returns `{"username": ..., "available": true/false}`.
- Check logic:
  - Filter says definitely not -> available True, no database query.
  - Filter says maybe -> database `find_one`, available is True only if no user found.
- Signup logic:
  - Filter says definitely not -> skip the username database check, check email only.
  - Filter says maybe -> database username check as before.
  - After a successful insert, the username is added to the filter.
- Startup load:
  - The filter lives in RAM, so a restart would empty it.
  - On startup, all usernames are loaded from MongoDB into the filter.
  - Rule: database first, filter second. Never trust an empty filter.
- Feature flag:
  - `FILTER_ENABLED` env var, default true.
  - When false, the old database-only checks run, so the simple version stays toggleable.

## Test results, clean re-test in order
- Check `ali` -> available True
- Signup `ali` -> success
- Check `ali` -> available False
- Restart uvicorn, check `ali` without signup -> available False
  This restart check is the startup-load proof. An empty filter would have returned True without touching the database.
- Login `ali` -> login successful
- Duplicate signup `ali` -> 400, username taken

### Troubleshooting lesson in this chapter
- The first test looked wrong: old user `rao` showed available True, login failed, and a fresh signup of `rao` succeeded.
- Cause, confirmed by the user: `docker compose down -v` had been run, which deletes the `mongo_data` volume.
- The filter was not buggy. It answered correctly for an empty database.
- Lesson: plain `down` keeps volumes, `down -v` deletes data. Use `-v` only when data will be re-seeded.

## Limitations, still present
- One filter per app process, in RAM only. No multi-node sync yet.
- Bloom filters do not support delete. A deleted username would still say "maybe", but the database check would correct it to available.
- False positives still need a database query, by design.
- No filter metrics yet, no frontend page yet.
- No login rate limiting yet.

## Next steps, one at a time
1. Login rate limiting, with an attack script
2. Signup and login frontend pages
3. Instagram-like feed (followers, likes, comments, views)
4. Redis cache, replication and sharding later, when those chapters arrive

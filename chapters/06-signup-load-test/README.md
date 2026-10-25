# Chapter 06 - Signup Load Test

## Goal
Prove, with k6, that more than 100 users can sign up through the real signup API, and measure how signup latency differs from a simple username check.

## Step 1 - k6 signup script
- File: `apps/social-lab/load-testing/signup.js`
- Options: 10 virtual users, 150 iterations, maxDuration 120 seconds, sleep 0.1.
- Unique user per iteration: `k6_RUNID_VU_ITER`, where RUN_ID comes from `date +%s` at run time, so a second run does not collide with the first run.
- Email: username plus `@test.com`, also unique.
- Password: `12345` for all test users.
- Checks: status 201, and response username matches the requested username.

### Troubleshooting lesson in this step
- First count command failed with `ModuleNotFoundError: No module named 'pymongo'`.
- Cause: the command used system `python3`, and pymongo is installed only in `backend/.venv`, not in system Python. The Step 1 commands had omitted `source .venv/bin/activate`.
- Fix: use the venv Python directly, `backend/.venv/bin/python`, for count commands, so activation is not needed.

## Test results, in order
- MongoDB count before run: 10153
- k6 run: 150 iterations, 300 checks, 100 percent pass, status 201 and username matches both pass.
- MongoDB count after run: 10303
- Delta: exactly 150, every signup reached the database.
- Sample users: `k6_1792844161_10_0`, `k6_1792844161_10_1`, `k6_1792844161_10_10`, with matching emails.
- Latency: average 779.73 ms, median 774.73 ms, minimum 426.29 ms, maximum 1.35 seconds, p90 910.27 ms, p95 about 1 second.
- Rate: 11.01 requests per second, total run 13.6 seconds, iteration duration average 854.65 ms.

## Comparison, signup versus username check
- Username check, Chapter 05 FILTER ON: average 2.84 ms, p95 6.95 ms, 94.21 requests per second.
- Signup, this chapter: average 779.73 ms, p95 about 1000 ms, 11.01 requests per second.
- Signup is about 274x slower in average latency, and about 8.5x lower in throughput.
- Reason: signup does bcrypt hashing, which is intentionally slow, plus a MongoDB insert and a filter add, on a single uvicorn worker that blocks on CPU. A username check only reads the filter or the database.
- Lesson: slow hashing is a security tradeoff. It protects passwords against guessing, but it limits signup throughput. This is why rate limiting, workers, and async hashing matter in later chapters.

## Limitations, still present
- Single uvicorn worker only, no multi-worker signup measurement yet.
- No signup metrics in Grafana yet, this chapter measures signup only through k6 summary and MongoDB count.
- k6 test users remain in the database and in the filter, total count is now 10303, future tests must account for test data.
- No login rate limiting yet.

## Next steps, one at a time
1. Login rate limiting, with an attack script
2. Signup and login frontend pages
3. Instagram-like feed (followers, likes, comments, views)
EOFcat > chapters/06-signup-load-test/README.md <<'EOF'
# Chapter 06 - Signup Load Test

## Goal
Prove, with k6, that more than 100 users can sign up through the real signup API, and measure how signup latency differs from a simple username check.

## Step 1 - k6 signup script
- File: `apps/social-lab/load-testing/signup.js`
- Options: 10 virtual users, 150 iterations, maxDuration 120 seconds, sleep 0.1.
- Unique user per iteration: `k6_RUNID_VU_ITER`, where RUN_ID comes from `date +%s` at run time, so a second run does not collide with the first run.
- Email: username plus `@test.com`, also unique.
- Password: `12345` for all test users.
- Checks: status 201, and response username matches the requested username.

### Troubleshooting lesson in this step
- First count command failed with `ModuleNotFoundError: No module named 'pymongo'`.
- Cause: the command used system `python3`, and pymongo is installed only in `backend/.venv`, not in system Python. The Step 1 commands had omitted `source .venv/bin/activate`.
- Fix: use the venv Python directly, `backend/.venv/bin/python`, for count commands, so activation is not needed.

## Test results, in order
- MongoDB count before run: 10153
- k6 run: 150 iterations, 300 checks, 100 percent pass, status 201 and username matches both pass.
- MongoDB count after run: 10303
- Delta: exactly 150, every signup reached the database.
- Sample users: `k6_1792844161_10_0`, `k6_1792844161_10_1`, `k6_1792844161_10_10`, with matching emails.
- Latency: average 779.73 ms, median 774.73 ms, minimum 426.29 ms, maximum 1.35 seconds, p90 910.27 ms, p95 about 1 second.
- Rate: 11.01 requests per second, total run 13.6 seconds, iteration duration average 854.65 ms.

## Comparison, signup versus username check
- Username check, Chapter 05 FILTER ON: average 2.84 ms, p95 6.95 ms, 94.21 requests per second.
- Signup, this chapter: average 779.73 ms, p95 about 1000 ms, 11.01 requests per second.
- Signup is about 274x slower in average latency, and about 8.5x lower in throughput.
- Reason: signup does bcrypt hashing, which is intentionally slow, plus a MongoDB insert and a filter add, on a single uvicorn worker that blocks on CPU. A username check only reads the filter or the database.
- Lesson: slow hashing is a security tradeoff. It protects passwords against guessing, but it limits signup throughput. This is why rate limiting, workers, and async hashing matter in later chapters.

## Limitations, still present
- Single uvicorn worker only, no multi-worker signup measurement yet.
- No signup metrics in Grafana yet, this chapter measures signup only through k6 summary and MongoDB count.
- k6 test users remain in the database and in the filter, total count is now 10303, future tests must account for test data.
- No login rate limiting yet.

## Next steps, one at a time
1. Login rate limiting, with an attack script
2. Signup and login frontend pages
3. Instagram-like feed (followers, likes, comments, views)
EOFcat > chapters/06-signup-load-test/README.md <<'EOF'
# Chapter 06 - Signup Load Test

## Goal
Prove, with k6, that more than 100 users can sign up through the real signup API, and measure how signup latency differs from a simple username check.

## Step 1 - k6 signup script
- File: `apps/social-lab/load-testing/signup.js`
- Options: 10 virtual users, 150 iterations, maxDuration 120 seconds, sleep 0.1.
- Unique user per iteration: `k6_RUNID_VU_ITER`, where RUN_ID comes from `date +%s` at run time, so a second run does not collide with the first run.
- Email: username plus `@test.com`, also unique.
- Password: `12345` for all test users.
- Checks: status 201, and response username matches the requested username.

### Troubleshooting lesson in this step
- First count command failed with `ModuleNotFoundError: No module named 'pymongo'`.
- Cause: the command used system `python3`, and pymongo is installed only in `backend/.venv`, not in system Python. The Step 1 commands had omitted `source .venv/bin/activate`.
- Fix: use the venv Python directly, `backend/.venv/bin/python`, for count commands, so activation is not needed.

## Test results, in order
- MongoDB count before run: 10153
- k6 run: 150 iterations, 300 checks, 100 percent pass, status 201 and username matches both pass.
- MongoDB count after run: 10303
- Delta: exactly 150, every signup reached the database.
- Sample users: `k6_1792844161_10_0`, `k6_1792844161_10_1`, `k6_1792844161_10_10`, with matching emails.
- Latency: average 779.73 ms, median 774.73 ms, minimum 426.29 ms, maximum 1.35 seconds, p90 910.27 ms, p95 about 1 second.
- Rate: 11.01 requests per second, total run 13.6 seconds, iteration duration average 854.65 ms.

## Comparison, signup versus username check
- Username check, Chapter 05 FILTER ON: average 2.84 ms, p95 6.95 ms, 94.21 requests per second.
- Signup, this chapter: average 779.73 ms, p95 about 1000 ms, 11.01 requests per second.
- Signup is about 274x slower in average latency, and about 8.5x lower in throughput.
- Reason: signup does bcrypt hashing, which is intentionally slow, plus a MongoDB insert and a filter add, on a single uvicorn worker that blocks on CPU. A username check only reads the filter or the database.
- Lesson: slow hashing is a security tradeoff. It protects passwords against guessing, but it limits signup throughput. This is why rate limiting, workers, and async hashing matter in later chapters.

## Limitations, still present
- Single uvicorn worker only, no multi-worker signup measurement yet.
- No signup metrics in Grafana yet, this chapter measures signup only through k6 summary and MongoDB count.
- k6 test users remain in the database and in the filter, total count is now 10303, future tests must account for test data.
- No login rate limiting yet.

## Next steps, one at a time
1. Login rate limiting, with an attack script
2. Signup and login frontend pages
3. Instagram-like feed (followers, likes, comments, views)

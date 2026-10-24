# Chapter 05 - Observability and Load Testing

## Goal
See, visually in Grafana, which username checks reach MongoDB and which are answered by the Bloom filter, and measure the difference with k6, filter ON versus filter OFF, using the same test.

## Step 1 - Prometheus metrics in the app
- Dependency: `prometheus-client` in `requirements.txt`.
- Endpoint: `GET /metrics`.
- Counter: `sociallab_username_check_total` with results:
  - `filter_no_db`: filter said definitely not, no database query.
  - `db_taken`: filter said maybe, database confirmed taken.
  - `db_available`: filter said maybe, database said available, a false positive corrected by the database.
- Histogram: `sociallab_username_check_duration_seconds`.
- Verified: ali check -> db_taken, newuser999 check -> filter_no_db, duration count 2.

## Step 2 - Prometheus and Grafana via Docker
- File: `apps/social-lab/docker-compose.yml`, plus `observability/prometheus.yml`.
- Services: mongo, prometheus on 9090, grafana on 3000, volumes for data.
- Prometheus scrapes `host.docker.internal:8000` every 5s, with `host-gateway`.
- Grafana login, local demo only: admin / admin.

### Troubleshooting lesson in this step
- First Prometheus query returned an empty result, even though the app metrics were correct.
- Targets showed `host.docker.internal:8000` down, error `dial tcp 172.17.0.1:8000: connection refused`.
- Cause: uvicorn binds `127.0.0.1` by default, the Docker gateway cannot reach loopback.
- Fix: start uvicorn with `--host 0.0.0.0 --port 8000`.
- After the fix, targets showed up, but the query was still empty, because restart had reset the in-memory counters and only `/health` had been called.
- After two fresh username checks, the Prometheus query showed `db_taken 1` and `filter_no_db 1`.
- Lesson: a target up means scrape works, a counter series only exists after its first increment.

## Step 3 - Grafana dashboard as code
- Provisioning: datasource Prometheus, uid `prometheus`, url `http://prometheus:9090`.
- Dashboard: `Social Lab`, uid `social-lab`, 4 panels:
  1. Username checks per second by outcome, rate query.
  2. Username checks total by outcome, counter query.
  3. Check duration p95, histogram_quantile query.
  4. Check duration count.
- Verified by screenshot: all 4 panels rendering, p95 about 0.0046 seconds, about 4.6 ms, at the time of the screenshot.

## Step 4 - Seed 10000 users
- File: `apps/social-lab/backend/seed_users.py`.
- Users `seed000000` to `seed009999`, one bcrypt hash reused for all seed users, load test only.
- Direct database inserts do not update the in-RAM filter, so uvicorn must be restarted after seeding, for the startup load to rebuild the filter from MongoDB.
- Verified: seed000042 -> available False, db_taken; randomuser999999 -> available True, filter_no_db.

## Step 5 - k6, FILTER ON
- File: `apps/social-lab/load-testing/check-username.js`.
- 10 virtual users, 30 seconds, sleep 0.1, 80 percent miss, 20 percent hit.
- Result: 2892 iterations, 5784 checks, 100 percent pass.
- Metrics delta: db_taken +585, filter_no_db +2285, db_available +22, total 2892.
- Database hits: 607, 21 percent. Filter saved: 2285, 79 percent.
- False positives: 22 out of 2307 miss attempts, 0.95 percent, close to the designed 1 percent error rate.
- Latency: average 2.84 ms, p90 5.06 ms, p95 6.95 ms, max 29.52 ms, 94.21 requests per second.

## Step 6 - k6, FILTER OFF, same script
- No code change, only `FILTER_ENABLED=false` on uvicorn restart.
- Verification: newuser999 -> db_available, ali -> db_taken, filter_no_db absent.
- Result: 2855 iterations, 5710 checks, 100 percent pass.
- Metrics delta: db_available +2310, db_taken +545, filter_no_db 0, total 2855.
- Database hits: 2855, 100 percent. Filter saved: 0.
- Latency: average 4.31 ms, p90 7.16 ms, p95 9.76 ms, max 43.05 ms, 93.09 requests per second.

## Comparison table, ON versus OFF
- Iterations: 2892 versus 2855, roughly equal.
- Database hits: 607, 21 percent, versus 2855, 100 percent, 4.7x more OFF.
- Filter saved: 2285, 79 percent, versus 0.
- Correctness: 100 percent both ways.
- Average latency: 2.84 ms versus 4.31 ms, OFF 1.47 ms higher.
- p95 latency: 6.95 ms versus 9.76 ms, OFF 2.81 ms higher.
- Rate: 94.21 versus 93.09 requests per second, roughly equal.
- Lesson: on localhost the latency difference is modest, the database query count difference is large. The filter pays off most when the database is slow or under high load.

## Step 7 - Restore FILTER ON
- Stop uvicorn, `unset FILTER_ENABLED`, restart with `--host 0.0.0.0`.
- Verified: filter_no_db 1, db_taken 1, app back in ON mode.
- Counters reset on every restart, this is normal for in-memory metrics, Prometheus and Grafana keep the history.

## Limitations, still present
- App still runs on the host with uvicorn, not inside Docker yet.
- Counters live in RAM and reset on restart.
- Grafana admin password is a local demo value, not production.
- No alerts yet.
- Load test covers check-username only, not signup load yet.
- No login rate limiting yet.

## Next steps, one at a time
1. Signup load test, 100 plus users, with k6
2. Login rate limiting, with an attack script
3. Signup and login frontend pages
4. Instagram-like feed (followers, likes, comments, views)
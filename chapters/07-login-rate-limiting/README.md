# Chapter 07 - Login Rate Limiting

## Goal
Prove that a simple login can be brute-forced without limit, then add rate limiting, measure the same attack again, and fix a concurrency race that the measurement reveals.

## Step 1 - Baseline attack, no rate limiting
- File: `apps/social-lab/load-testing/login-attack.js`
- Options: 10 virtual users, 100 iterations, sleep 0.05.
- Target: local demo user `ali`, password `wrong123`, wrong on every attempt.
- Checks: status 401, detail invalid username or password.
- Results: 100 iterations, 200 checks, 100 percent pass, average 819.79 ms, median 773.35 ms, p90 972.81 ms, p95 1.41 seconds, rate 11.04 requests per second, total run 9.1 seconds.
- After attack, correct login for `ali` returned login successful.
- Lesson: no block, no lockout, no delay. An attacker can keep trying forever, and every wrong attempt still costs a full bcrypt check, about 800 ms of CPU.

## Step 2 - Rate limiting, first version
- Limit per username, not per IP, so one attacked account does not block other users.
- Constants: MAX_FAILED_ATTEMPTS 5, BLOCK_SECONDS 60, demo values only.
- Flag: RATE_LIMIT_ENABLED, default true, so the simple version can be toggled, like FILTER_ENABLED.
- Block check happens before database lookup and bcrypt, so blocked requests do not cost CPU.
- 5th failed attempt still returns 401 and sets the block, 6th attempt onwards returns 429.
- Correct password during block also returns 429, because the block check runs before password verification.
- Successful login clears the counter, block expiry also clears the counter.
- Metrics: sociallab_login_total with results success, invalid, blocked.
- Manual test, in order: 5 wrong `ali` 401, 6th wrong `ali` 429, wrong `zain` 401 proving per-username isolation, correct `ali` during block 429, correct `ali` after 65 seconds login successful.
- Metrics note: one manual run showed invalid 12, blocked 5, success 2, which is cumulative across more than one manual run, not a single-run total. The sequence outputs themselves were exactly as designed.

## Step 3 - Same attack, rate limiting on, race revealed
- File: `apps/social-lab/load-testing/login-attack-after.js`
- Same options as Step 1, but check is status 401 or 429, because blocked responses are expected now.
- App restarted first, for clean metrics and a cleared block.
- Results: 100 iterations, 100 checks, 100 percent pass, average 86.52 ms, median 1.46 ms, p90 475.24 ms, p95 762.54 ms, rate 71.55 requests per second, total run 1.4 seconds.
- Metrics: invalid 12.0, blocked 88.0, total 100. Correct login after attack returned 429.
- Expected was invalid 5, blocked 95, so 7 extra invalid attempts slipped through.
- Cause: Step 2 incremented the counter only after bcrypt finished, about 800 ms later, while the block check ran at request start. Under 10 parallel virtual users, more than 5 requests passed the block check before the first 5 bcrypt checks finished and set the block. This is a check-then-act race. The limit was soft under concurrency, not hard.
- Median 1.46 ms proves blocked requests skip bcrypt and database, which is why average latency and total run time fell so sharply.

## Step 4 - Race fix, pre-increment under a lock
- Change: count is incremented before database lookup and bcrypt, inside a threading.Lock. The lock covers only check plus increment, microseconds of work, and is released before bcrypt, so logins are not serialized on slow hashing.
- 5th attempt still returns 401 and sets the block, next attempts return 429, even under parallel load.
- Successful login clears the record under the lock, so a correct password on the 5th try does not leave the user blocked.
- Results, after restart and the same after script: 100 iterations, 100 checks, 100 percent pass, average 23.05 ms, median 2.15 ms, p90 15.87 ms, p95 42.25 ms, rate 129.80 requests per second, total run 0.8 seconds.
- Metrics: invalid 5.0, blocked 95.0, exact as designed. Correct login after attack returned 429, correct login after 65 seconds returned login successful, so expiry still works after the fix.

## Comparison, three attack runs
- Baseline, no rate limiting: average 819.79 ms, rate 11.04 req/s, run 9.1 s, 100 invalid, 0 blocked.
- After rate limiting, with race: average 86.52 ms, rate 71.55 req/s, run 1.4 s, 12 invalid, 88 blocked.
- After race fix: average 23.05 ms, rate 129.80 req/s, run 0.8 s, 5 invalid, 95 blocked.
- Lesson: rate limiting saves CPU because blocked requests skip bcrypt, and pre-increment under a lock turns a soft limit into a hard limit under parallel load.

## Limitations, still present
- In-memory only, single process only. Restart clears blocks and counters.
- Multiple workers would each have their own lock and store, so a shared atomic counter, for example Redis INCR, is needed for multi-worker or multi-node use.
- Demo values only, 5 attempts and 60 seconds. Production would use longer blocks, progressive delays, or account recovery flows.
- No IP-based limiting, no CAPTCHA, no progressive delay yet.

## Next steps, one at a time
1. Signup and login frontend pages
2. Instagram-like feed (followers, likes, comments, views)

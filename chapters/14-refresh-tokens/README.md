# Chapter 14 — Refresh Tokens + Shorter Sessions

## What this chapter builds on, and what it unlocks next

This chapter closes the authentication story started in Chapter 10 (sessions),
hardened in Chapter 11 (SHA256 token hashes, HttpOnly cookies) and protected in
Chapter 12 (CSRF double-submit tokens).

- Chapter 10 gave us a session: a random token, stored in Mongo, valid for 24 hours.
- Chapter 11 made the stored token useless to a database thief (hash only) and
  invisible to page JavaScript (HttpOnly cookie).
- Chapter 12 stopped another website from triggering our authenticated requests.

What was still open: a stolen session cookie worked for the full 24 hours, and
there was no way to shorten that window without logging the user out every few
minutes. This chapter fixes exactly that trade-off: a **short-lived access
session** (15 minutes) plus a **long-lived, rotating refresh token** (7 days).

Next, this chapter unlocks Chapter 15 (CSP headers, the last XSS wall from
Chapter 11) and Chapter 16 (Authorization / IDOR: login is not enough, ownership
checks are needed), because the session layer is now complete and can be trusted
as the foundation for those lessons.

## The problem, in one sentence

A session that lives for 24 hours is a 24-hour window for a stolen cookie. A
session that lives for 15 minutes is safe, but it logs the user out every
15 minutes. Refresh tokens break the trade-off: the short session is renewed
silently, and only the refresh token lives long — and it rotates on every use,
so a stolen copy dies the moment the real user refreshes.

## Step 1 — Baseline: the 24-hour session

Method: log in as `ali` with curl, read `/me`, then read the newest session
document from Mongo.

Results, verified:

- Login returned `{"message": "login successful", "username": "ali"}`.
- `/me` returned `{"username": "ali"}`.
- The session document showed `created_at` to `expires_at` difference of
  **24.0 hours**, and a `token_hash` of length **64** (SHA256 hex, no plaintext
  token in the database — Chapter 11 still holds).

Conclusion: before this chapter, one session cookie was enough for a full day,
with no device binding and no rotation. That 24-hour window is what Step 2
shrinks to 15 minutes.

## Step 2 — Backend patch: short access + rotating refresh

Design, agreed before code:

- Access session: **15 minutes** by default, controlled by the
  `ACCESS_TOKEN_TTL_SECONDS` environment variable (default `900`).
- Refresh token: **7 days** by default, controlled by
  `REFRESH_TOKEN_TTL_DAYS` (default `7`).
- Feature flag: `REFRESH_TOKENS_ENABLED` (default `true`). When `false`, login
  behaves exactly as before — a 24-hour session, no refresh cookie, and the
  refresh endpoint returns 404. The flag hides the feature, it does not delete
  stored refresh data.
- Refresh tokens are stored the same way as sessions: only a SHA256
  `token_hash` in Mongo, never the plaintext value. The cookie is HttpOnly,
  so page JavaScript cannot read it.
- The CSRF cookie lifetime follows the refresh lifetime when the feature is
  on (7 days), because the refresh request itself needs a valid CSRF token.
  When the feature is off, it stays at 24 hours as before.
- Rotation: every successful refresh deletes the old refresh token and issues
  a new access session plus a new refresh token.
- Reuse detection: rotation alone stops replay (the old token is dead), but it
  does not prove theft. So every consumed refresh token's hash is kept in a
  `used_refresh_tokens` collection (TTL-cleaned after the refresh lifetime).
  If a used hash is presented again, that is treated as theft: **all** of that
  user's sessions and refresh tokens are deleted, and the response is
  `401 refresh token reuse detected, all sessions revoked`.
- The refresh endpoint is CSRF-protected like every other authenticated POST:
  the CSRF check runs before the refresh lookup, so a missing header returns
  `403 csrf token missing` even when the refresh token is already dead.
- Logout deletes the access session, the refresh token for that cookie, and
  all three cookies (session, CSRF, refresh).

New collections (Mongo, TTL indexes on `expires_at`):

- `refresh_tokens`: `token_hash` (unique), `username`, `created_at`,
  `expires_at`.
- `used_refresh_tokens`: `token_hash` (unique), `username`, `created_at`,
  `expires_at`.

New endpoint:

- `POST /api/v1/auth/refresh` — reads the `social_lab_refresh` cookie and the
  `X-CSRF-Token` header, rotates the refresh token, sets a new access cookie
  and a new refresh cookie, returns `{"message": "refreshed", "username": ...}`.

Backend patch script (`/tmp/ch14_step02_backend_patch.py`, exactly-once asserts,
backup `/tmp/main.py.ch14.bak` — do NOT re-run it; a second run fails its
asserts on purpose, because the old text is already gone):

```python
import pathlib, shutil
p = pathlib.Path("app/main.py")
text = p.read_text()

def must(old, new, label):
    global text
    n = text.count(old)
    print(label, "found:", n)
    assert n == 1, label + " not found exactly once"
    text = text.replace(old, new)

must("""sessions_collection = db["sessions"]
sessions_collection.create_index("token_hash", unique=True)
sessions_collection.create_index("expires_at", expireAfterSeconds=0)""",
     """sessions_collection = db["sessions"]
sessions_collection.create_index("token_hash", unique=True)
sessions_collection.create_index("expires_at", expireAfterSeconds=0)
refresh_tokens_collection = db["refresh_tokens"]
refresh_tokens_collection.create_index("token_hash", unique=True)
refresh_tokens_collection.create_index("expires_at", expireAfterSeconds=0)
used_refresh_tokens_collection = db["used_refresh_tokens"]
used_refresh_tokens_collection.create_index("token_hash", unique=True)
used_refresh_tokens_collection.create_index("expires_at", expireAfterSeconds=0)""",
     "refresh collections")

must('@app.get("/health")',
     '''ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "900"))
REFRESH_TOKEN_TTL_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "7"))
REFRESH_TOKENS_ENABLED = os.getenv("REFRESH_TOKENS_ENABLED", "true").lower() == "true"


@app.get("/health")''', "refresh env vars")

must("""    sessions_collection.insert_one({
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "username": user["username"],
        "created_at": now,
        "expires_at": now + timedelta(hours=SESSION_EXPIRY_HOURS),
    })""",
     """    access_expires_at = now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS) if REFRESH_TOKENS_ENABLED else now + timedelta(hours=SESSION_EXPIRY_HOURS)
    sessions_collection.insert_one({
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "username": user["username"],
        "created_at": now,
        "expires_at": access_expires_at,
    })""", "login access expiry")

must("""        key="social_lab_session",
        value=token,
        httponly=True,
        max_age=24 * 60 * 60,""",
     """        key="social_lab_session",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_TTL_SECONDS if REFRESH_TOKENS_ENABLED else 24 * 60 * 60,""",
     "session cookie max_age")

must("""        key="csrf_token",
        value=csrf_token,
        httponly=False,  # frontend JS must read it to send as header, it is not a session token
        max_age=24 * 60 * 60,""",
     """        key="csrf_token",
        value=csrf_token,
        httponly=False,  # frontend JS must read it to send as header, it is not a session token
        max_age=REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60 if REFRESH_TOKENS_ENABLED else 24 * 60 * 60,""",
     "csrf cookie max_age")

must("""        secure=False,  # localhost HTTP demo only, production must be True with HTTPS
    )
    return {"message": "login successful", "username": user["username"]}""",
     """        secure=False,  # localhost HTTP demo only, production must be True with HTTPS
    )
    if REFRESH_TOKENS_ENABLED:
        refresh_token = secrets.token_hex(32)
        refresh_tokens_collection.insert_one({
            "token_hash": hashlib.sha256(refresh_token.encode()).hexdigest(),
            "username": user["username"],
            "created_at": now,
            "expires_at": now + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        })
        response.set_cookie(
            key="social_lab_refresh",
            value=refresh_token,
            httponly=True,
            max_age=REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
            path="/",
            samesite="lax",
            secure=False,  # localhost HTTP demo only, production must be True with HTTPS
        )
    return {"message": "login successful", "username": user["username"]}""",
     "login refresh creation")

must("""def logout(response: Response, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    if social_lab_session:
        validate_csrf(csrf_token, x_csrf_token)
        sessions_collection.delete_one({"token_hash": hashlib.sha256(social_lab_session.encode()).hexdigest()})
    response.delete_cookie(key="social_lab_session", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    return {"message": "logged out"}""",
     """def logout(response: Response, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None), social_lab_refresh: str | None = Cookie(None)):
    if social_lab_session:
        validate_csrf(csrf_token, x_csrf_token)
        sessions_collection.delete_one({"token_hash": hashlib.sha256(social_lab_session.encode()).hexdigest()})
    if social_lab_refresh:
        refresh_tokens_collection.delete_one({"token_hash": hashlib.sha256(social_lab_refresh.encode()).hexdigest()})
    response.delete_cookie(key="social_lab_session", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    response.delete_cookie(key="social_lab_refresh", path="/")
    return {"message": "logged out"}


@app.post("/api/v1/auth/refresh")
def refresh(response: Response, social_lab_refresh: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    if not REFRESH_TOKENS_ENABLED:
        raise HTTPException(status_code=404, detail="refresh tokens disabled")
    validate_csrf(csrf_token, x_csrf_token)
    if not social_lab_refresh:
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")
    refresh_hash = hashlib.sha256(social_lab_refresh.encode()).hexdigest()
    used = used_refresh_tokens_collection.find_one({"token_hash": refresh_hash})
    if used:
        username = used["username"]
        sessions_collection.delete_many({"username": username})
        refresh_tokens_collection.delete_many({"username": username})
        raise HTTPException(status_code=401, detail="refresh token reuse detected, all sessions revoked")
    record = refresh_tokens_collection.find_one({"token_hash": refresh_hash})
    if not record:
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")
    expires_at = record.get("expires_at")
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and datetime.now(timezone.utc) >= expires_at:
        refresh_tokens_collection.delete_one({"token_hash": refresh_hash})
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")
    username = record["username"]
    now = datetime.now(timezone.utc)
    used_refresh_tokens_collection.insert_one({
        "token_hash": refresh_hash,
        "username": username,
        "created_at": now,
        "expires_at": now + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    })
    refresh_tokens_collection.delete_one({"token_hash": refresh_hash})
    token = secrets.token_hex(32)
    sessions_collection.insert_one({
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "username": username,
        "created_at": now,
        "expires_at": now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
    })
    response.set_cookie(
        key="social_lab_session",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_TTL_SECONDS,
        path="/",
        samesite="lax",
        secure=False,  # localhost HTTP demo only, production must be True with HTTPS
    )
    new_refresh_token = secrets.token_hex(32)
    refresh_tokens_collection.insert_one({
        "token_hash": hashlib.sha256(new_refresh_token.encode()).hexdigest(),
        "username": username,
        "created_at": now,
        "expires_at": now + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    })
    response.set_cookie(
        key="social_lab_refresh",
        value=new_refresh_token,
        httponly=True,
        max_age=REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
        path="/",
        samesite="lax",
        secure=False,  # localhost HTTP demo only, production must be True with HTTPS
    )
    return {"message": "refreshed", "username": username}""",
     "logout refresh delete and refresh endpoint")

shutil.copy(p, "/tmp/main.py.ch14.bak")
p.write_text(text)
print("written, backup at /tmp/main.py.ch14.bak")
```

Patch result: all 7 asserts `found: 1`, diff index `19670ae..e1c15a7`,
`py_compile` syntax OK, `/health` 200.

Backend curl verification, in order (cookie values never pasted, only counts
and messages):

- Login: `{"message": "login successful", "username": "ali"}`.
- Cookie counts in the jar: `social_lab_session` 1, `social_lab_refresh` 1,
  `csrf_token` 1.
- `/me` with the jar: `{"username": "ali"}`.
- Refresh with the correct CSRF header: `{"message": "refreshed",
  "username": "ali"}`; `/me` again: `ali` (the new access session works).
- The old refresh token, presented again: `401 refresh token reuse detected,
  all sessions revoked`.
- After that reuse: `/me` with the new access jar: `401 invalid or expired
  session`; refresh with the new jar: `401 invalid or expired refresh token`.
  The revoke really revoked everything.
- Refresh without a CSRF header: `403 csrf token missing` (CSRF runs before
  the refresh lookup, as designed).

## Step 2 — Frontend patch: silent refresh on page load

`feed.js` `initFeed` only; `login.js` needed no change, because the refresh
cookie is set by the login response and the browser stores it before the
redirect.

New behaviour:

- Normal case: `/me` returns 200, the feed loads exactly as before. Refresh
  is never called.
- Expired access: `/me` returns 401, `initFeed` tries `POST
  /api/v1/auth/refresh` once (credentials + CSRF header), then retries `/me`.
  If that succeeds, the feed loads and the user never notices the access
  session expired.
- Refresh failure (revoked, expired, flag-off 404, or CSRF 403): the old
  cleanup runs and the page redirects to login. That is the only case where
  re-login is required.

`getCsrfToken()` is defined later in the file than `initFeed`, but it is a
function declaration, so it is hoisted and available when `initFeed` runs.

Frontend patch script (`/tmp/ch14_step02_frontend_patch.py`, exactly-once,
backup `/tmp/feed.js.ch14.bak` — do NOT re-run it; the second and third runs
failed with `found: 0` on purpose, which is the safety working, not breakage):

```python
import pathlib, shutil
p = pathlib.Path("frontend/feed.js")
text = p.read_text()

def must(old, new, label):
    global text
    n = text.count(old)
    print(label, "found:", n)
    assert n == 1, label + " not found exactly once"
    text = text.replace(old, new)

must("""(function initFeed() {
  fetch('/api/v1/auth/me', {
    credentials: 'same-origin',
  })
    .then(function (res) {
      if (!res.ok) {
        throw new Error('invalid session');
      }
      return res.json();
    })""",
     """(function initFeed() {
  fetch('/api/v1/auth/me', {
    credentials: 'same-origin',
  })
    .then(function (res) {
      if (res.ok) {
        return res.json();
      }
      return fetch('/api/v1/auth/refresh', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': getCsrfToken() }
      }).then(function (refreshRes) {
        if (!refreshRes.ok) {
          throw new Error('invalid session');
        }
        return fetch('/api/v1/auth/me', {
          credentials: 'same-origin',
        }).then(function (res2) {
          if (!res2.ok) {
            throw new Error('invalid session');
          }
          return res2.json();
        });
      });
    })""", "initFeed silent refresh")

shutil.copy(p, "/tmp/feed.js.ch14.bak")
p.write_text(text)
print("written, backup at /tmp/feed.js.ch14.bak")
```

Verification: the diff showed exactly one inserted block, `grep` showed one
`auth/refresh` line (line 143), `node --check` reported JS syntax OK, and the
browser normal flow worked end to end (login, logout, comment, like, view,
unique views). A wrong-password burst correctly triggered the Chapter 07 rate
limit (`too many login attempts, try again later`, 60-second block) — that is
expected protection, not a bug.

## Step 3 — Measure: expiry, silent refresh, and the flag

No 15-minute waits were needed: the access TTL was set to 60 seconds for the
test run (`ACCESS_TOKEN_TTL_SECONDS=60`), and the flag was tested separately
(`REFRESH_TOKENS_ENABLED=false`).

Expiry test (curl, 60-second TTL), in order:

- Login: successful, ali. `/me`: ali.
- Newest session document: `expires_at - created_at` = **60.0 seconds**.
- After a 70-second wait, `/me`: `401 invalid or expired session`.
- Refresh with the correct CSRF header: `refreshed`, ali.
- `/me` again: ali.

Silent refresh test (browser, same 60-second server):

- Logged in, waited 70 seconds, hard-refreshed the feed (Ctrl+Shift+R).
- Result: the feed loaded again, logged in as ali — **not** the login page.
  `initFeed` refreshed silently, exactly as designed.

Flag test (curl, `REFRESH_TOKENS_ENABLED=false`):

- Login: successful, ali. Refresh cookie count in the jar: **0**.
- `/me`: ali.
- Newest session document: `expires_at - created_at` = **24.0 hours** — the
  old behaviour, restored.
- Refresh endpoint: `404 refresh tokens disabled`.

After the tests, the server was restarted with the normal command (no
environment overrides): 15-minute access sessions and 7-day refresh tokens.

## Comparison

| Check | Before (Chapters 10–12) | After (Chapter 14) |
|---|---|---|
| Access session lifetime | 24 hours | 15 minutes (900 s, env-tunable) |
| Stolen access cookie window | 24 hours | 15 minutes |
| Refresh token | Did not exist | 7 days, HttpOnly cookie, hash only in Mongo |
| Refresh reuse | Not applicable | Old refresh dead after rotation; reuse returns 401 and revokes all of that user's sessions and refresh tokens |
| Access expired, page reload | Login page after 24 h | Feed stays, silent refresh, then `/me` succeeds |
| Access expired, curl `/me` | 401, re-login only | 401, then refresh returns `refreshed`, then `/me` succeeds |
| Refresh without CSRF header | Not applicable | 403 `csrf token missing` |
| Flag off | Not applicable | No refresh cookie (count 0), 24-hour session, refresh endpoint 404 |
| Logout | Deletes session + CSRF cookie | Deletes session + refresh token + all three cookies |
| Normal UI (login, post, like, comment, view, unique views, logout) | Works | Works, unchanged |

## Honest limitations

- Refresh does not revoke the old access session immediately; it expires on
  its own within 15 minutes. That is the standard trade-off for short-lived
  access tokens, and it is why the access lifetime is kept short.
- Silent refresh runs only on page load (`initFeed`). If the access session
  expires while the page is already open, a write call returns 401 until the
  page is reloaded. Per-call retry is a later improvement, not built here.
- Reuse detection revokes **all** of the user's sessions, on every device.
  That is the safe response to suspected theft, and it means one device's
  stolen refresh token logs every device out.
- A refresh token stolen and used **before** the real user rotates it still
  wins that race; reuse detection only fires when the old token is presented
  a second time. Short refresh lifetimes and rotation limit, but do not
  eliminate, that window.
- `secure=False` and `samesite="lax"` are localhost demo values; production
  needs HTTPS with `secure=True`. The 15-minute and 7-day lifetimes are demo
  values chosen for this app, not a universal rule.
- Token and cookie values are never pasted into chat or notes. Only lengths,
  counts, and endpoint results are recorded.

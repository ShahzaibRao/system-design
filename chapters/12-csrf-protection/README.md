# Chapter 12 — CSRF Protection (Double-Submit Token)

## What this chapter demonstrates

Chapter 11 moved the session token into an HttpOnly cookie. That stopped JavaScript
from reading the token, but it did **not** stop CSRF: the browser sends cookies
automatically, even for a request triggered by a completely different site. This
chapter proves that with a real cross-site attack, then fixes it with a
double-submit CSRF token, then re-runs the attack to show it no longer works.

The method is the same as Chapter 11: attack first, patch second, re-attack third.
No code was committed for the attack experiments. The evil test pages live outside
the repo in `~/csrf-evil/` and must never be committed.

## Step 1 — The attack (before the patch)

### 1a. Create-post with a text/plain request: FAILED (and the lesson in it)

The first hypothesis was that FastAPI would accept a JSON body sent with
`Content-Type: text/plain`, because text/plain is a "simple" header that does not
trigger a CORS preflight. That hypothesis was **wrong**, and the test proved it:

```
curl login (fresh jar) -> {"message":"login successful","username":"ali"}
curl POST /api/v1/posts, Content-Type: text/plain, JSON body
  -> 422 {"detail":[{"type":"model_attributes_type","loc":["body"],
     "msg":"Input should be a valid dictionary or object to extract fields from"}]}
```

FastAPI treats a non-JSON Content-Type body as a raw string, not as a JSON object.
The evil page (`evil.html`, served from `http://localhost:8001` while logged in at
`http://localhost:8000`) also produced no hacked post, and the server log showed
the reason:

```
POST /api/v1/posts 422 Unprocessable Entity
```

Honest conclusion: JSON body parsing accidentally blocked this vector. It is **not**
a security control. A future endpoint that accepts a form body would reopen CSRF
immediately, which is why the patch in Step 2 protects every state-changing POST
instead of relying on body parsing.

### 1b. Like with a body-less request: FAILED (invalid test, two defects)

The like endpoint takes an empty `LikeRequest` model, so a body-less simple request
was tried next. The server log showed:

```
POST /api/v1/posts/POST_ID/like 422 Unprocessable Entity
```

Two defects, neither of which is a CSRF verdict:

1. The URL literally contained `POST_ID` — the placeholder was never replaced with
   the real post id (`6adde035346ab43aa449354e`, ali, 0 likes at the time).
2. The fetch sent **no body**, but `like_post` still declares a `LikeRequest` body
   parameter, so FastAPI body validation returns 422 before post lookup or auth.
   A correctly-formed JSON body (`{}` with `Content-Type: application/json`, as the
   real frontend sends) is not a simple request and would trigger preflight
   cross-origin. No correctly-formed simple-request like test was ever run.

### 1c. Logout with a body-less request: SUCCEEDED — CSRF proven

`logout` declares **no body model at all** (only `Response` + cookie), so a
body-less simple POST has no body validation to fail. It is the cleanest CSRF
candidate, and it worked. The evil page (`evil-logout.html`) ran this while the
victim was logged in:

```javascript
fetch('http://localhost:8000/api/v1/auth/logout', {
  method: 'POST',
  mode: 'no-cors',
  credentials: 'include'
});
```

Server log, in order (feed load, then the attack, then the proof):

```
POST /api/v1/auth/login 200 OK
GET  /api/v1/auth/me 200 OK
GET  /api/v1/feed 200 OK
POST /api/v1/posts/<id>/view 200 OK   (many, one per feed post)
POST /api/v1/auth/logout 200 OK       <- from the evil page, not by the user
GET  /static/feed.html 304 Not Modified
GET  /api/v1/auth/me 401 Unauthorized <- session is gone, login page shown
```

Severity is honestly low — a forced logout only, no data changed and no token
stolen — but the proof is complete: an attacker site ended the victim's session
without consent, without ever seeing a token. That is CSRF, and it is enough to
justify the patch, including protection on logout itself.

## Step 2 — The patch: double-submit token

### Design

- Login sets a second cookie, `csrf_token`, in addition to the HttpOnly
  `social_lab_session` cookie. The CSRF cookie is deliberately **non-HttpOnly**,
  so the site's own JavaScript can read it. That is safe by design: it is not a
  session token, it only proves the request came from a page that could read
  this site's cookies. An attacker site cannot read it, because cookies are only
  visible to their own origin.
- The frontend reads `csrf_token` from `document.cookie` and sends it as the
  `X-CSRF-Token` header on every write. The browser automatically sends the
  cookie too. The server compares header vs cookie: match, or 403.
- An attacker site can make the browser send the cookie, but it cannot send the
  header, because it does not know the value, and a custom header would trigger
  a CORS preflight that this server does not allow. That asymmetry is the whole
  defense.
- Comparison uses `hmac.compare_digest`, not `==`, so a timing attack cannot
  guess the token byte by byte.
- Protected writes: create, like, comment, **and logout**. Logout is included
  precisely because Step 1c proved it was the exploitable endpoint.
- Login is exempt: there is no session yet, so there is nothing to compare
  against. That is the standard exception, not a gap in this design.
- GET endpoints (`/me`, `/feed`) are exempt: they do not change state.
- View (`POST /posts/<id>/view`) is exempt on purpose: it is public and
  unauthenticated, so a CSRF token would add nothing — anyone can already call
  it directly. Its real fix is authentication plus unique views, which belongs
  to the unique-views chapter, not this one.

### Backend — `apps/social-lab/backend/app/main.py`

New helper, placed before `/health`:

```python
def validate_csrf(csrf_token_cookie, csrf_token_header):
    if not csrf_token_cookie or not csrf_token_header:
        raise HTTPException(status_code=403, detail="csrf token missing")
    if not hmac.compare_digest(csrf_token_cookie, csrf_token_header):
        raise HTTPException(status_code=403, detail="csrf token invalid")
```

Login sets the CSRF cookie alongside the session cookie (localhost demo values,
same as Chapter 11: `secure=False` is HTTP-only-local, production must use HTTPS
with `secure=True`):

```python
csrf_token = secrets.token_hex(32)
response.set_cookie(
    key="csrf_token",
    value=csrf_token,
    httponly=False,  # frontend JS must read it to send as header, it is not a session token
    max_age=24 * 60 * 60,
    path="/",
    samesite="lax",
    secure=False,  # localhost HTTP demo only, production must be True with HTTPS
)
```

Every protected endpoint takes the cookie and the header, authenticates first
(401 if there is no valid session), then validates CSRF (403 on mismatch):

```python
def create_post(data: PostCreate, social_lab_session: str | None = Cookie(None),
                csrf_token: str | None = Cookie(None),
                x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
```

`like_post` and `comment_post` follow the same pattern. Logout validates only
when a session exists, then deletes **both** cookies:

```python
def logout(response: Response, social_lab_session: str | None = Cookie(None),
           csrf_token: str | None = Cookie(None),
           x_csrf_token: str | None = Header(None)):
    if social_lab_session:
        validate_csrf(csrf_token, x_csrf_token)
        sessions_collection.delete_one({"token_hash": hashlib.sha256(social_lab_session.encode()).hexdigest()})
    response.delete_cookie(key="social_lab_session", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    return {"message": "logged out"}
```

The patch was applied with an exactly-once patch script
(`/tmp/ch12_step02_patch.py`, backup `/tmp/main.py.ch12.bak`). The first run
succeeded; a second run failed its assert on the already-inserted helper, which
is the script working as designed — it never writes a partial or duplicate
patch. The full `git diff` was verified and `py_compile` reported `syntax OK`.
**Do not re-run the patch script.**

### Backend curl verification (jar `/tmp/csrf2-cookies.txt`, values never pasted)

```
login                              -> {"message":"login successful","username":"ali"}
grep -c social_lab_session (jar)   -> 1
grep -c csrf_token (jar)           -> 1
POST /posts, no header             -> {"detail":"csrf token missing"}
POST /posts, correct header        -> 201, id 6ade0898b1def89dd62e4a4b, username ali,
                                      content "with csrf test", 0 likes, 0 views
POST /posts, wrong header          -> {"detail":"csrf token invalid"}
POST /auth/logout, no header      -> {"detail":"csrf token missing"}
POST /auth/logout, correct header  -> {"message":"logged out"}
GET  /auth/me after logout         -> {"detail":"invalid or expired session"}
```

(The curl commands printed response bodies without status codes; the detail
strings identify the branch: `csrf token missing` / `csrf token invalid` are the
403 CSRF branches, `invalid or expired session` is the 401 session branch.)

### Frontend — `apps/social-lab/frontend/feed.js` (login.js unchanged)

`login.js` needs no change: the CSRF cookie is set by the login response and the
browser stores it before the redirect to the feed. `feed.js` was patched with an
exactly-once script (`/tmp/ch12_step02_frontend_patch.py`, backup
`/tmp/feed.js.ch12.bak`), verified by grep:

```
old jsonHeaders definition removed
jsonHeaders() calls found: 3
write calls now use csrfHeaders()
helpers appended
logout fetch found: True
logout header added
```

Resulting grep (string concatenation only, no template literals, per the
Chapter 08 frontend rule):

```
50:      headers: csrfHeaders(),
82:      headers: csrfHeaders(),
105:    headers: csrfHeaders(),
124:  fetch('/api/v1/auth/logout', { headers: { 'X-CSRF-Token': getCsrfToken() },
156:function getCsrfToken() {
167:function csrfHeaders() {
168:  return { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() };
jsonHeaders gone
```

`getCsrfToken()` parses `document.cookie` for `csrf_token=`; `csrfHeaders()`
replaces the old `jsonHeaders()` on create, like, and comment. The view fetch is
untouched, as designed. After the frontend edit, a hard refresh (Ctrl+Shift+R)
is required — the Chapter 11 cache lesson applies again: old cached `feed.js`
would keep sending writes without the header and get 403.

## Step 3 — Re-attack after the patch

- Evil logout page, opened while logged in: **no logout anymore**. The feed
  stays logged in after returning to it. Before the patch the same page returned
  200 and ended the session; after the patch the request is rejected for a
  missing CSRF header and the session survives.
- Normal UI flow still works end to end: login, post, like, comment, and logout
  from the logout button all succeed with the header now sent by the frontend.
- Evil create-post page: still produces no post, as before (body validation
  rejects the text/plain request before the CSRF check runs). The outcome is
  unchanged, the reason is now layered: even a correctly-formed cross-site
  request would fail the CSRF check.

### Comparison

| Check | Before patch | After patch |
|---|---|---|
| Evil create-post (text/plain) | 422, no post (body parsing, accidental) | No post (body parsing, plus CSRF check behind it) |
| Evil like (body-less) | 422, no like (body required) | No like |
| Evil logout (body-less) | **200, session ended, /me 401, login page** | **Session survives, feed stays logged in** |
| Legit post, no CSRF header (curl) | Would succeed with a valid session cookie | 403 `csrf token missing` |
| Legit post, correct CSRF header (curl) | Not applicable | 201, post created |
| Legit post, wrong CSRF header (curl) | Not applicable | 403 `csrf token invalid` |
| Legit logout, no header (curl) | Would succeed | 403 `csrf token missing` |
| Normal UI (post, like, comment, logout button) | Works | Works (frontend now sends the header) |

## Limitations (honest)

- Double-submit stops **cross-site** requests. It does not stop a script already
  running on this site's own pages: such a script can read `csrf_token` (it is
  non-HttpOnly by design) and send the header itself. That is why this chapter is
  one layer among several — `textContent` rendering (Chapter 11 Step 1) means
  attacker script does not execute via posts, and the HttpOnly session cookie
  means the session value itself never reaches JavaScript.
- Login CSRF is out of scope: an attacker could in principle log a victim into
  the attacker's account. It changes no victim data, but it is a known remaining
  item, not a solved one.
- View counts are still public and unauthenticated; CSRF protection was
  deliberately not added there (see Step 2 design). Unique, authenticated views
  are a separate chapter.
- `secure=False` and `samesite="lax"` are localhost demo values. Production
  needs HTTPS with `secure=True`, and SameSite alone was never the defense here
  — the token comparison is.
- The 24-hour session/CSRF lifetime is a demo value; shorter sessions plus
  refresh tokens are a future chapter.

## Troubleshooting notes from this chapter

- A patch script that asserts exactly-once will fail on a second run because the
  old text is already gone. That failure is safety, not breakage: the script
  writes nothing unless every replacement matches, so a failed re-run leaves
  the file exactly as the successful run left it. Verify with `git diff`, do not
  re-run.
- A literal placeholder in a test URL (`POST_ID`) is not a CSRF result. Replace
  placeholders with real ids before reading a log line as evidence.
- A 422 from FastAPI body validation happens before endpoint code runs, so it
  cannot prove or disprove a CSRF check. Read the log line together with what
  the endpoint actually validates.
- After any frontend change, hard-refresh (Ctrl+Shift+R). A cached old script
  sending writes without the new header looks exactly like a broken patch.

## Next steps (candidates, not started)

- CSP headers, so even an injected script has a harder time running at all.
- Refresh tokens with a shorter session lifetime.
- Followers plus a followed-only feed.
- Unique views with HyperLogLog.

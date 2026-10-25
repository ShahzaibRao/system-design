# Chapter 11 — XSS Attack and Patch

Attack first, then patch. This chapter replaces a standalone
hashed-token / HttpOnly chapter: instead of only implementing the
secure version, we first test whether the site is vulnerable, measure
the impact, patch it in two isolated steps, and re-attack to compare.

## What this chapter demonstrates

- How to audit a frontend for XSS sinks (`innerHTML`, `document.write`,
  and friends) versus safe rendering (`textContent`).
- Why a token in `localStorage` is readable by any script on the page,
  even when stored XSS itself does not execute.
- Why a database should store only a hash of a session token, never
  the token itself.
- Why an `HttpOnly` cookie stops token theft, and what it does not stop.

## Step 1 — Audit and stored XSS test

Audit commands:

```bash
cd apps/social-lab/frontend
grep -rn "innerHTML\|outerHTML\|document.write\|insertAdjacentHTML" .
grep -rn "textContent" . | head -20
```

Result: no dangerous sink was found. The feed renders post content with
`textContent` (`feed.js`, `contentP.textContent = post.content`), a choice
made in Chapter 09.

Browser test: posts containing
`<script>alert('xss-script')</script>` (and an `<img onerror>` payload)
were stored and displayed literally, on post and on refresh. No alert
appeared. A script payload stored in a post is rendered as text, not as
executable markup.

A CSS file containing old JavaScript lines was noticed in the same grep
output. JavaScript inside a CSS file does not run, so it is not a sink;
it is deferred file hygiene, not part of this chapter.

## Step 2 — Token impact simulation

Console test on the logged-in feed page (a simulation, not a real
exploit — a real XSS needs an attacker-controlled injection point, and
Step 1 did not find one in posts):

```javascript
localStorage.getItem('social_lab_token').length  // 64
fetch('/api/v1/auth/me', {
  headers: { 'Authorization': 'Bearer ' + localStorage.getItem('social_lab_token') }
})
```

Result: token length 64, and `/me` returned the logged-in user. Any
script running on the page could read the token and act as that user.
That is the danger Chapter 10 left open, and the reason for Step 3.

## Step 3a — Store only the token hash in MongoDB

Changes in `apps/social-lab/backend/app/main.py`:

- `import hashlib`.
- Sessions index changed from `token` to `token_hash` (unique).
- Login stores
  `"token_hash": hashlib.sha256(token.encode()).hexdigest()`, never the
  token itself.
- The auth helper hashes the incoming Bearer token before lookup, and
  the expired-session cleanup and logout delete by hash.
- Bearer transport and the frontend are unchanged in this step, so any
  breakage is known to be hashing, not cookies.

Migration lessons (both hit in this step, both real):

1. A new unique field plus old documents without that field breaks
   index creation: MongoDB treats a missing field as `null`, so all old
   sessions collide on `{ token_hash: null }` and the app crashes at
   startup. Fix: stop the server, delete only sessions without
   `token_hash`, restart.
2. Changing the index line in code does not drop the old index in the
   database. The old unique `token_1` index stayed, the first new
   session insert used the single allowed `null`, and the second insert
   failed with a duplicate key, surfacing as a plain 500 (the login
   session insert has no `DuplicateKeyError` handler, unlike signup).
   Fix: `drop_index('token_1')`, then login works repeatedly.

Verified: fresh login, `/me` returns the user, the latest session
document has keys `_id, created_at, expires_at, token_hash, username`
(no `token` field, hash length 64), logout works, `/me` after logout
returns 401.

Commit: `4a517c7 ch11-step03a: store session token hash, not plaintext`.

## Step 3b — HttpOnly cookie, drop the localStorage token

Backend changes in `app/main.py`:

- `Header` replaced by `Cookie` for auth parameters.
- Login sets `social_lab_session` with `httponly=True`,
  `samesite="lax"`, `max_age` 24 hours, `path="/"`, and `secure=False`.
  The `secure=False` value is localhost-HTTP-only: a secure cookie is
  never sent over plain HTTP. Production must use `secure=True` with
  HTTPS.
- Login returns message and username only, no token field.
- `/me`, post, like, and comment read the cookie; logout deletes the
  session and clears the cookie with `delete_cookie`.
- Request models cleaned: `PostCreate` and `CommentRequest` lost the
  `username` field, `LikeRequest` became empty — the username now
  comes from the cookie, never from the body.

Frontend changes in `frontend/login.js` and `frontend/feed.js`:

- `authHeaders()` replaced by `jsonHeaders()` (Content-Type only).
- Like body became `{}`, comment and post bodies lost `username`.
- Login success and logout use `removeItem` cleanup for old
  `localStorage` keys instead of `setItem`.
- `initFeed` no longer checks a localStorage token; it calls `/me`
  directly and the browser sends the cookie.
- Auth fetches use `credentials: 'same-origin'`.

Verified with curl (cookie jar) and in the browser: login returns no
token, `/me` with the cookie works, without it returns 401, a post
created with the cookie and no body username belongs to the cookie
user, logout works and `/me` after logout returns 401.

One debugging lesson: the first browser screenshot showed
`localStorage` token value `undefined`. That was old JavaScript: the
old login code still ran `setItem` with a token the backend no longer
returns. Writes still worked because same-origin fetch sends the cookie
automatically and the backend ignores the Bearer header. The proof that
the new code was live came from `grep` (only `removeItem` and
`jsonHeaders` in the files) and from a fresh login after logout and
hard refresh: localStorage empty, `getItem` returns `null`.

Commit: `ddc3e82 ch11-step03b: HttpOnly cookie session, drop localStorage token`.

## Step 4 — Re-attack and compare

Stored XSS again: a post with
`<script>alert('xss-after-patch')</script>` displays literally, in the
post and in a comment on it. No alert, before or after refresh.

Console again, logged in:

```javascript
localStorage.getItem('social_lab_token')  // null
document.cookie.indexOf('social_lab_session')  // -1
fetch('/api/v1/auth/me', { credentials: 'omit',
  headers: { 'Authorization': 'Bearer test123' } })  // 401
fetch('/api/v1/auth/me')  // { username: 'ali' }
```

Reading: the token value is nowhere in JavaScript now, the Bearer path
is dead, and the cookie is the auth mechanism. The last call succeeding
is not a failure — the script learns only a username the page already
displays. What changed is token theft, not every in-page action: a
script already running on the page can still trigger authenticated
requests from that page, because the browser sends the cookie. That is
why rendering, hashing, and HttpOnly are a combination, not a single
fix.

| Check | Before | After |
|---|---|---|
| Stored script post | Text, no alert | Text, no alert (rendering was already safe) |
| Token in localStorage | Readable, length 64 | `null` |
| Token in `document.cookie` | No cookie existed | `-1` (HttpOnly, hidden from JS) |
| Bearer `/me` | Worked | 401 without a cookie |
| Cookie `/me` | No cookie existed | Works, returns the cookie user |
| Session in DB | Plaintext token | Only `token_hash`, no token field |
| Login response | Token field present | No token, Set-Cookie only |
| App still works | Yes | Yes (login, post, like, comment, logout) |

## Troubleshooting

- App crashes at startup with a `token_hash_1` duplicate key on
  `null`: old sessions lack the new field. Delete only sessions
  without `token_hash` and restart.
- Login returns 500 after a hash migration while the web login worked
  once: the old unique `token_1` index is still in the database.
  Drop it; code changes do not drop database indexes.
- A sessions check with a `{'username': 1}` projection always shows
  `False` for `token` and `token_hash`: the projection returns only
  the username. Check without a projection.
- `localStorage` token shows `undefined` after the cookie migration:
  old JavaScript ran `setItem` with a token the backend no longer
  returns. Check the files with `grep`, then logout, hard refresh, and
  log in again; the correct result is an empty store and `null`.
- `git diff` with a repo-root path fails from the backend folder:
  use a path relative to the current folder, for example
  `git diff -- app/main.py`.

## Limitations

- HttpOnly stops JavaScript token theft. It does not fully stop CSRF:
  the browser sends the cookie automatically. `SameSite=Lax` reduces
  it; a CSRF token or Origin check is still needed for full protection.
- A script already executing on the page can still make authenticated
  requests from that page. Rendering with `textContent` is what stops
  the script from getting there through posts in the first place.
- `secure=False` is localhost-only. Production needs HTTPS and
  `secure=True`.
- Session expiry is 24 hours, a demo value. Production would use a
  shorter lifetime plus refresh tokens.
- No Content Security Policy is set yet; CSP would be the next layer.

## Next steps

- CSRF protection (token or Origin check) for cookie-authenticated writes.
- Content Security Policy headers.
- Shorter session lifetime plus refresh tokens.
- Rate limiting already exists for login (Chapter 07); the same
  thinking could be extended to post creation.

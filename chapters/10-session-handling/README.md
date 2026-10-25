# Chapter 10 — Session Handling

## Goal

Login should actually keep the user logged in. After this chapter, the feed
uses the logged-in user automatically for posts, likes, and comments. No
username is typed by hand for each action anymore, and logout really ends
the session on the server, not just in the browser.

## What was built

### Step 1 — Session API only

- On successful login, the server generates a token with
  `secrets.token_hex(32)`: 64 hex characters, 256-bit random.
- Tokens live in a new MongoDB `sessions` collection with fields `token`,
  `username`, `created_at`, and `expires_at`.
- Expiry is 24 hours in this demo. A TTL index on `expires_at` deletes
  expired sessions automatically, and the code also checks expiry on every
  request, so an expired token fails even if TTL cleanup lags.
- New endpoint: `GET /api/v1/auth/me` with header
  `Authorization: Bearer <token>`. It returns the username for a valid,
  unexpired token, otherwise `401 invalid or expired session`.
- The login response stays backward compatible: `message` and `username`
  are unchanged, `token` is added.

### Step 2 — Frontend integration

- `frontend/login.js`: on successful login, saves `social_lab_token` and
  `social_lab_username` in localStorage and redirects to `/static/feed.html`.
  Rate-limit (429) and other errors still show as red messages.
- `frontend/feed.html`: the typed username input is gone. The page shows
  `Logged in as <user>` and a Logout button.
- `frontend/feed.js`: on load, if there is no token it redirects to login.
  Otherwise it calls `/me` with the Bearer token, sets the logged-in
  username, and only then loads the feed. If `/me` fails, it clears
  localStorage and redirects to login.
- Create post, like, and comment all use the logged-in username.
- Limitation of Step 2, fixed in Step 3: logout only cleared localStorage.
  The server token stayed valid until expiry.

### Step 3 — Server logout and protected write endpoints

- New endpoint: `POST /api/v1/auth/logout`. If a Bearer token is present,
  its session row is deleted. It always returns `logged out`.
- `POST /api/v1/posts`, `POST /api/v1/posts/{id}/like`, and
  `POST /api/v1/posts/{id}/comment` now require a Bearer token. The
  username comes from the token, never from the request body.
- `GET /api/v1/feed` and `POST /api/v1/posts/{id}/view` stay public.
  Reading and counting a view are public actions; only writes are
  protected.
- `frontend/feed.js` adds an `authHeaders()` helper that sends
  `Content-Type: application/json` plus `Authorization: Bearer <token>`
  on create, like, and comment requests. The logout button now calls
  server logout first, then clears localStorage in `finally` and redirects
  to login.

## Session flow

    login -> server creates token -> browser stores token in localStorage
    feed load -> GET /me with Bearer token -> username confirmed
    post / like / comment -> Bearer token -> server uses token username
    logout -> server deletes session -> browser clears token -> login page

## Test results

Step 1, curl:

- Login as `ali` returned a token of length 64, as designed.
- `/me` with the valid token returned `{"username": "ali"}`.
- `/me` with a wrong token returned `401 invalid or expired session`.
- `/me` with an expired session inserted directly in MongoDB returned
  `401 invalid or expired session`, proving expiry without waiting 24 hours.

Step 2, browser:

- Login redirected to the feed, a new post appeared on top, like and
  comment worked, localStorage held both token and username, logout
  deleted both, and re-login issued a new token. A new token per login
  is expected: every login creates a new session.

Step 3, curl, in order:

- Post with no token: `401 invalid or expired session`.
- Post with a valid token but body username `hacker`: created with
  username `ali`, not `hacker`. This is the key proof: the server trusts
  the token, not the body.
- Logout with the token: `{"message": "logged out"}`.
- `/me` with the same token after logout: `401`, proving the session
  was deleted on the server, not just cleared in the browser.

Step 3, browser: fresh login, post, like, comment, logout, and opening
the feed directly after logout redirected to login, all confirmed working.

## Troubleshooting notes

- The Step 3 patch script stopped on an assert: `create_post not found`.
  That was the correct behaviour, not a failure to work around. Grep
  showed logout and the new token-based `create_post` were already in
  the file, so the patch was looking for old text that no longer
  existed. The patch was not run again, which would have risked
  duplicating the logout endpoint. The remaining endpoints and the
  frontend were verified by grep and a syntax check before testing.
- A token-capture command failed at first with
  `backend/.venv/bin/python: No such file or directory`, because it was
  run from inside the `backend` folder, where that path is doubled. From
  inside `backend`, the venv path is `.venv/bin/python`, and plain
  `python3` is enough for parsing JSON.

## Limitations

- Session tokens are stored in plaintext in MongoDB in this simple
  version. A database leak would leak sessions. Production hashes the
  token in the database, like a password.
- The frontend stores the token in localStorage, which JavaScript can
  read and is therefore exposed to XSS. Production uses HttpOnly cookies
  that JavaScript cannot read.
- Expiry of 24 hours is a demo value. Production uses a short session
  plus a refresh token.
- There is no token rotation, no per-device session list, and no
  "log out everywhere" yet.
- Login rate-limit counters from Chapter 07 are still in-memory, so a
  server restart clears them. Sessions themselves survive a restart
  because they live in MongoDB.

## Next steps

- Hashed session tokens in the database and HttpOnly cookies in the
  browser.
- Refresh tokens and short-lived sessions.
- Unique views alongside total views, followers with a followed-only
  feed, and pagination beyond the current limit of 20.

## Commits

- `60d1a93` ch10-step01: session API, login token and me endpoint,
  TTL expiry
- `7606f55` ch10-step02: frontend session, login redirect feed,
  logout clears localstorage
- `9a46590` ch10-step03: server logout, protected writes use token
  username
- Tag `ch10-session-handling` goes on the docs commit for this chapter,
  not on a step commit.

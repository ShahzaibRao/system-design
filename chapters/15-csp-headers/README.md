# Chapter 15 — CSP Headers

## What this chapter builds on, and what it unlocks next

This chapter is the second wall for the XSS lesson from Chapter 11.

- Chapter 11 proved that the feed renders user content with `textContent`,
  so a stored `<script>` post displays as text and never executes. That is
  the first wall, and it lives in the application's own code.
- Chapter 15 adds a second wall that lives in the browser: a
  `Content-Security-Policy` header on every response, telling the browser
  which sources scripts, styles, connections, and images may come from. Even
  if a script somehow reaches the page in the future — a developer mistake,
  a third-party library, a new rendering path — the browser refuses to run
  it unless its source is allowed.

This chapter was chosen by the assistant under the ordering delegation from
Chapter 14 (the assistant decides chapter order so topics stay connected,
because Rao does not know which topic connects to which). It unlocks
Chapter 16 (Authorization / IDOR), because the session and browser-defence
layers are now complete before access-control logic is built on top of them.

## Step 1 — Baseline: no CSP header, and a clean inline audit

Why the audit comes first: if the application's own pages used inline
scripts, a strict CSP would break the application itself on day one. The
patch is only safe if the app already loads everything from same-origin
files.

Baseline commands and results, verified:

- `curl -I /static/feed.html | grep -i content-security-policy` — empty.
- `curl -I /api/v1/feed | grep -i content-security-policy` — empty.
- Conclusion: before this chapter, the browser received no CSP rule at all,
  on HTML or on API responses.
- Inline audit (`grep` for `onclick`, `onload`, `onerror`, `<script` in
  `frontend/*.html` and `frontend/*.js`, and for `style=` / `javascript:`
  in `frontend/*.html`): only three external script tags, no inline
  handlers, no inline script blocks, no inline styles, no `javascript:`
  URLs:
  - `frontend/feed.html:24: <script src="/static/feed.js"></script>`
  - `frontend/login.html:22: <script src="/static/login.js"></script>`
  - `frontend/signup.html:25: <script src="/static/signup.js"></script>`
- Frontend files at baseline: `feed.css`, `feed.html`, `feed.js`,
  `login.html`, `login.js`, `signup.html`, `signup.js`, `style.css`.

Conclusion: the app is CSP-ready. All scripts and styles come from
same-origin files, so a `script-src 'self'` / `style-src 'self'` policy
cannot break it.

## Step 2 — Backend patch: CSP middleware with a flag

Design, agreed before code:

- A FastAPI HTTP middleware adds the header to **every** response — pages,
  static files, and API JSON alike. On API JSON the header is harmless:
  browsers only enforce CSP on documents, but its presence proves the
  middleware runs on every path.
- The policy is a single constant, so it can be read and changed in one
  place.
- Feature flag `CSP_ENABLED` (default `true`). When `false`, the middleware
  passes responses through without the header — the old behaviour, restored
  exactly, so the simple vs enhanced difference can be shown live.

The policy, directive by directive:

- `default-src 'self'` — anything without its own rule may only come from
  this site.
- `script-src 'self'` — scripts only from this site's own `.js` files. No
  inline scripts, no scripts from other sites.
- `style-src 'self'` — styles only from this site's own CSS files. No
  inline `style=` attributes.
- `connect-src 'self'` — fetch and API calls only to this site, so stolen
  data cannot be sent to an outside site from a script.
- `img-src 'self' data:` — images from this site or data URIs, not from
  outside sites.
- `object-src 'none'` — no plugins or embeds at all.
- `base-uri 'self'` — no `<base>` tag pointing the page at another site.
- `frame-ancestors 'none'` — this page cannot be embedded in another
  site's iframe (clickjacking defence, a bonus on top of XSS).

Backend patch script (`/tmp/ch15_step02_backend_patch.py`, exactly-once
asserts, backup `/tmp/main.py.ch15.bak` — do NOT re-run it):

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

must("from fastapi import FastAPI, HTTPException, Cookie, Header",
     "from fastapi import FastAPI, HTTPException, Cookie, Header, Request",
     "Request import")

must('app = FastAPI(title="social-lab")',
     '''app = FastAPI(title="social-lab")

CSP_ENABLED = os.environ.get("CSP_ENABLED", "true").lower() == "true"
CSP_POLICY = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"


@app.middleware("http")
async def add_csp_headers(request: Request, call_next):
    response = await call_next(request)
    if CSP_ENABLED:
        response.headers["Content-Security-Policy"] = CSP_POLICY
    return response''',
     "CSP middleware")

shutil.copy(p, "/tmp/main.py.ch15.bak")
p.write_text(text)
print("written, backup at /tmp/main.py.ch15.bak")
```

Patch result: both asserts `found: 1`, diff index `e1c15a7..7f9c33e`,
`py_compile` syntax OK.

One honest debugging note from this step: the first header check right
after the patch showed nothing. That was not a patch failure — the diff
was already correct. It was a reload race: the curl ran before uvicorn's
reloader had picked up the changed file. A re-check moments later showed
the header on both `/health` and `/static/feed.html`, with the exact policy
string, served by uvicorn running from the backend folder (PID 35070,
`--reload`). Lesson: after a patch, if a header is missing, re-check after
a few seconds before blaming the code.

## Step 3 — Measure: the app still works, the inline script does not

### 3a — Normal flow, browser

Login, feed, post, like, comment, logout all worked unchanged under CSP.
Expected, because Step 1 proved the app uses no inline scripts — but it was
still verified by hand, not assumed.

### 3b — Inline script test page, CSP on

A test-only page, created outside the chapter (never committed, deleted in
Step 3d):

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>CSP test</title></head>
<body>
<h1 id="result">not run</h1>
<script>document.getElementById('result').textContent = 'INLINE RAN';</script>
</body></html>
```

Placed at `frontend/csp-test.html`, visited at
`http://localhost:8000/static/csp-test.html`.

Result, CSP on: the page showed **"not run"**, not "INLINE RAN". The browser
refused the inline script because of the policy, even though the script
reached the page. The feed on the other half of the screen kept working
(Logged in as ali, posts, likes, views, unique views, comments), proving
the policy blocks the attack without breaking the app.

### 3c — Flag off, and the two mistakes that had to be fixed first

The first flag-off attempt looked like a failure: with "CSP false" the test
page still showed "not run". Two separate causes were found in the
evidence, not guessed:

1. **The wrong server was running.** The uvicorn log showed the command
   line `.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
   with no `CSP_ENABLED=false` anywhere. That was the normal server
   (CSP on by default), so "not run" was the correct result for that
   server. A flag-off test needs a flag-off server, verified by its
   command line, not by intention.
2. **The test page was cached.** The log showed three lines of
   `GET /static/csp-test.html 304 Not Modified`. A 304 means the browser
   reused its cached copy — and the cached copy carries its original CSP
   header. So even on a true flag-off server, a normal refresh keeps the
   old policy. A fresh fetch is required: hard refresh (Ctrl+Shift+R) or a
   private window, so the log shows a 200 instead of a 304.

Corrected re-test, in order:

- Killed the old uvicorn, started the backend server with
  `CSP_ENABLED=false`.
- Header check first, before opening the browser:
  `curl -s -I http://localhost:8000/static/feed.html | grep -i
  content-security-policy` — empty, only "flag-off header check done".
  That proved the flag-off server was really serving.
- Hard-refreshed the test page: the page showed **"INLINE RAN"**.
  The flag alone is the difference: header present blocks the inline
  script, header absent lets it run.

### 3d — Cleanup and final state

- Deleted `frontend/csp-test.html`.
- `git status` showed only `modified: backend/app/main.py` (relative to
  the `apps/social-lab` folder), branch up to date with `origin/main`,
  no untracked test file left.
- The server was restarted with the normal command (no environment
  overrides), so the final state is CSP on.

## Comparison

| Check | Before (Chapters 11–14) | After (Chapter 15) |
|---|---|---|
| CSP header on HTML | None | Full policy on every response |
| CSP header on API | None | Full policy (harmless on JSON, proves middleware coverage) |
| Inline script in a test page | Runs ("INLINE RAN") | Blocked ("not run"), browser refuses it |
| Inline script, flag off | Runs | Runs ("INLINE RAN"), after a fresh 200 fetch |
| Normal app (login, feed, post, like, comment, view, unique views, logout) | Works | Works, unchanged |
| Page embedded in another site's iframe | Allowed | Blocked by `frame-ancestors 'none'` |
| Test file left in the repo | Not applicable | None — created, tested, deleted, `git status` clean of it |

## Honest limitations

- CSP is defence in depth, not a cure for XSS. It limits what a script
  can do after it reaches the page; it does not fix an injection point.
  The first wall (Chapter 11's `textContent` rendering) is still the fix,
  CSP is the second wall behind it.
- CSP is enforced by the browser on documents. It does not protect API
  JSON consumed by non-browser clients, it does not stop CSRF (Chapter 12
  covers that), and it does not stop an attacker who can already run code
  from an allowed same-origin source.
- No `report-uri` / `report-to` is configured, so blocked attempts are
  visible in the browser console but are not reported back to the server.
  Violation reporting is a later improvement, not built here.
- A cached document keeps its original policy until it is freshly fetched
  (the 304 lesson from Step 3c). Changing the policy does not instantly
  change already-cached pages.
- `style-src 'self'` blocks inline styles; any future feature that needs
  inline styles would need a nonce or hash, not a policy weakening by
  accident.

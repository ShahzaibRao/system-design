# Chapter 08 - Frontend Pages

## Goal
Turn the curl-only API into pages a recruiter can open in a browser. Signup and login pages, in plain HTML, CSS, and JavaScript, calling the same FastAPI endpoints built in earlier chapters. No React, no Node app, no build step.

## Step 1 - Signup page and static serving
- Files: `apps/social-lab/frontend/signup.html`, `frontend/style.css`, `frontend/signup.js`.
- Backend change: FastAPI serves the frontend from `/static`, root `/` returns signup.html. FRONTEND_DIR is derived from `__file__`, so it works regardless of the working directory.
- Same-origin design: frontend and API share one origin, so JavaScript fetches `/api/v1/...` directly, with no CORS configuration needed.
- Signup page features: live username check on input with 300 ms debounce, available in green, taken in red, submit button disabled when taken, signup submit shows success or error detail.
- Test results, rendering: signup page rendered correctly, centered card, blue Signup button, static files returned 200, 200, 200.
- Bug found and fixed in this step: first signup.js failed with browser console error `Uncaught SyntaxError: Unexpected token '{' (at signup.js:54:6)`. Symptoms were exactly what a parse error causes: no message under username, button never disabled, no check-username request, no signup POST in uvicorn logs. Backend was proven working by curl: `ali` available false, `frontendtest1` available true. Fix: rewrote signup.js completely with string concatenation only, no backtick template literals, no `${}` interpolation, to remove the copy corruption risk. After the fix, console showed no error.
- Test results, after fix: username `ali` showed Username taken in red, button grey and disabled. Username `frontendtest1` showed Username available in green, email `frontendtest1@test.com`, signup showed Signup successful, welcome frontendtest1 in green.

## Step 2 - Login page
- Files: `apps/social-lab/frontend/login.html`, `frontend/login.js`, style.css reused, no backend change.
- Login page features: username and password form, success message in green, invalid message in red, rate limit message in red with a note that it is a rate limit and to try again after 60 seconds.
- Navigation: signup page links to login, login page links to signup.
- Test results: new user `raoshahzaib` created through the signup page, login showed Login successful, welcome raoshahzaib in green. After failed attempts, login showed too many login attempts, try again later (rate limit, try again after 60 seconds) in red, proving the Chapter 07 rate limit path works through the UI, not only through curl. A 429 response can only occur after 5 invalid attempts, so the invalid path was exercised to reach the block.

## Limitations, still present
- No session or token yet. Login success is only a message on the page, it does not keep the user logged in, redirect to a feed, or protect any page.
- No logout, no protected routes, no password reset.
- Pages are plain HTML, CSS, and JavaScript, with no build step, no component framework, and no client-side routing.

## Next steps, one at a time
1. Instagram-like feed page (followers, likes, comments, views)
2. Session handling so login actually keeps the user logged in for the feed
EOFcat > chapters/08-frontend-pages/README.md <<'EOF'
# Chapter 08 - Frontend Pages

## Goal
Turn the curl-only API into pages a recruiter can open in a browser. Signup and login pages, in plain HTML, CSS, and JavaScript, calling the same FastAPI endpoints built in earlier chapters. No React, no Node app, no build step.

## Step 1 - Signup page and static serving
- Files: `apps/social-lab/frontend/signup.html`, `frontend/style.css`, `frontend/signup.js`.
- Backend change: FastAPI serves the frontend from `/static`, root `/` returns signup.html. FRONTEND_DIR is derived from `__file__`, so it works regardless of the working directory.
- Same-origin design: frontend and API share one origin, so JavaScript fetches `/api/v1/...` directly, with no CORS configuration needed.
- Signup page features: live username check on input with 300 ms debounce, available in green, taken in red, submit button disabled when taken, signup submit shows success or error detail.
- Test results, rendering: signup page rendered correctly, centered card, blue Signup button, static files returned 200, 200, 200.
- Bug found and fixed in this step: first signup.js failed with browser console error `Uncaught SyntaxError: Unexpected token '{' (at signup.js:54:6)`. Symptoms were exactly what a parse error causes: no message under username, button never disabled, no check-username request, no signup POST in uvicorn logs. Backend was proven working by curl: `ali` available false, `frontendtest1` available true. Fix: rewrote signup.js completely with string concatenation only, no backtick template literals, no `${}` interpolation, to remove the copy corruption risk. After the fix, console showed no error.
- Test results, after fix: username `ali` showed Username taken in red, button grey and disabled. Username `frontendtest1` showed Username available in green, email `frontendtest1@test.com`, signup showed Signup successful, welcome frontendtest1 in green.

## Step 2 - Login page
- Files: `apps/social-lab/frontend/login.html`, `frontend/login.js`, style.css reused, no backend change.
- Login page features: username and password form, success message in green, invalid message in red, rate limit message in red with a note that it is a rate limit and to try again after 60 seconds.
- Navigation: signup page links to login, login page links to signup.
- Test results: new user `raoshahzaib` created through the signup page, login showed Login successful, welcome raoshahzaib in green. After failed attempts, login showed too many login attempts, try again later (rate limit, try again after 60 seconds) in red, proving the Chapter 07 rate limit path works through the UI, not only through curl. A 429 response can only occur after 5 invalid attempts, so the invalid path was exercised to reach the block.

## Limitations, still present
- No session or token yet. Login success is only a message on the page, it does not keep the user logged in, redirect to a feed, or protect any page.
- No logout, no protected routes, no password reset.
- Pages are plain HTML, CSS, and JavaScript, with no build step, no component framework, and no client-side routing.

## Next steps, one at a time
1. Instagram-like feed page (followers, likes, comments, views)
2. Session handling so login actually keeps the user logged in for the feed

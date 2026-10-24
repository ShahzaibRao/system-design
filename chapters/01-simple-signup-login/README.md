# Chapter 01 - Simple Signup and Login

## Goal
Build the simplest working signup and login, run it, and test it.
No database, no filter, and no rate limiting yet, on purpose.
Enhancements come later, one at a time, with before/after comparison.

## What was built
- `GET /health` - returns `{"status":"ok","app":"social-lab"}`
- `POST /api/v1/auth/signup` - creates a user, returns id, username, email
- `POST /api/v1/auth/login` - checks username and password, returns login successful or 401
- Users are stored in an in-memory Python dict in `apps/social-lab/backend/app/main.py`

## Test results (verified with curl)
- Signup, new user: 201, user created
- Signup, same username again: 400, `username taken`
- Login, correct password: `login successful`
- Login, wrong password: 401, `invalid username or password`

## Limitations of this simple version (on purpose)
- Data lives only in memory. A server restart deletes all users.
- Password is stored plain in memory in this simple version only.
  Never use this in production. Password hashing is a later security step.
- Email duplicate check scans all users. This is O(n) and gets slower as users grow.
- No database, no username filter, no login rate limiting, no frontend page yet.

## Next enhancements, one at a time
1. Database (MongoDB) so data survives a restart
2. Password hashing
3. Username availability filter (Bloom / Cuckoo)
4. Login rate limiting, with an attack script
5. Signup and login frontend pages

## Method used in every chapter
Simple version -> measure -> enhanced version -> measure again -> comparison table.

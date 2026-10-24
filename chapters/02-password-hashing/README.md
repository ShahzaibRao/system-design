# Chapter 02 - Password Hashing with bcrypt

## Goal
Stop storing passwords in plain text, before any database is added.
This chapter is done before the database chapter on purpose,
so a plain password never reaches disk.

## BEFORE - Chapter 01 simple version
- Signup stored the password exactly as the user typed it.
- Login compared the typed password directly with the stored password.
- This was simple-only, never safe for production.

## AFTER - This chapter
- New dependency: `bcrypt` in `requirements.txt`.
- Signup: `bcrypt.hashpw(password, bcrypt.gensalt())`
  The password is stored only as a hash.
- Login: `bcrypt.checkpw(password, stored_hash)`
  The typed password is checked against the hash, never compared plain.
- Salt: bcrypt adds a random salt for every password.
  Two users with the same password get different hashes.
  A hash cannot be turned back into the original password.

## What changed, file by file
- `apps/social-lab/backend/requirements.txt`: added `bcrypt`.
- `apps/social-lab/backend/app/main.py`: signup now hashes, login now checks with bcrypt.
  No endpoint URL changed, no response shape changed.

## Test results (verified with curl)
- Signup new user: 201, user created
- Login correct password: `login successful`
- Login wrong password: 401, `invalid username or password`
- Outward results are the same as Chapter 01 on purpose.
  The change is internal: what is stored, and how it is checked.

## Limitations, still present
- Users are still in memory only. A restart deletes all users.
- No database yet, no username filter, no login rate limiting, no frontend page yet.
- Tradeoff: bcrypt is intentionally slow, so login takes a little longer.
  That slowness is a security feature, it makes brute-force attacks harder.

## Next steps, one at a time
1. Database (MongoDB) so data survives a restart, now safe because passwords are already hashed
2. Username availability filter (Bloom / Cuckoo)
3. Login rate limiting, with an attack script
4. Signup and login frontend pages

# Chapter 16 — Authorization / IDOR

## What this chapter builds on, and what it unlocks next

This chapter draws the line between the two words that are most often
confused in security:

- **Authentication** answers: who are you? Login, session, token — built in
  Chapter 10, hardened in Chapters 11, 12, and 14.
- **Authorization** answers: now that we know who you are, what are you
  allowed to touch? A logged-in user is not automatically allowed to touch
  every object in the system.

**IDOR** (Insecure Direct Object Reference) is the bug that appears when
the server checks the first question but not the second: it verifies the
user is logged in, then acts on whatever object ID the user supplies,
without checking whether that object belongs to that user. Changing one ID
in a request is enough to delete or modify someone else's data.

This chapter was chosen by the assistant under the ordering delegation
from Chapter 14 (the assistant decides chapter order so topics stay
connected, because Rao does not know which topic connects to which). It
builds directly on Chapter 10's token-user writes, and it unlocks the
performance chapters that follow (pagination and caching), because the
write paths are now fully guarded before read paths are optimised.

## Step 1 — Baseline: endpoint audit and attacker setup

Why the audit comes first: an IDOR test needs an object operation to
attack. If no delete or edit endpoint exists, there is nothing to attack
yet — and that itself is a finding worth recording, not skipping.

Audit results, from `grep` on `app/main.py`:

- Helpers: `post_to_dict` (line 112), `get_post_or_404` (line 125),
  `get_username_from_cookie` (line 136), `validate_csrf` (line 152).
- Endpoints: create post (line 406), feed (line 424), like (line 433),
  comment (line 444), view (line 461).
- **No delete or edit endpoint existed.** The IDOR door was not open — it
  did not exist yet, because delete did not exist yet.

Attacker user, created for this chapter only:

- Signup: `{"id": "6aded1f92765dc3891097d8a", "username": "bob",
  "email": "bob@test.local"}`. Login: successful.

Baseline test — the like endpoint already enforces the token user
(Chapter 10's proof, re-checked here):

- Ali's token, like request body `{"username": "hacker"}` on ali's post
  `6ade0898b1def89dd62e4a4b` ("with csrf test").
- Result: the like was recorded for ali, not for "hacker". The response
  showed `liked_by: ["ali", "raoshahzaib"]` — "hacker" appears nowhere.
  The server trusts the token, never the body username, for like and
  comment. That part was already correct before this chapter.

Conclusion of Step 1: like and comment were already token-bound, but
delete had no behaviour at all — safe by absence, not by a check. The
chapter therefore builds delete twice: first without the check, to prove
the attack, then with it.

## Step 2a — Delete without an ownership check, and the attack

Deliberately vulnerable version, built only to be attacked. It has
everything except the one check that matters: login is verified
(`get_username_from_cookie`), CSRF is verified (`validate_csrf`), and the
post's existence is verified (`get_post_or_404`) — but the `username`
variable that login returns is never compared to the post's owner.

Backend patch script (`/tmp/ch16_step02a_patch.py`, exactly-once,
backup `/tmp/main.py.ch16a.bak` — do NOT re-run it):

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

must("""@app.post("/api/v1/posts/{post_id}/view")
def view_post(post_id: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):""",
     """@app.delete("/api/v1/posts/{post_id}")
def delete_post(post_id: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    posts_collection.delete_one({"_id": post["_id"]})
    return {"message": "post deleted", "id": post_id}


@app.post("/api/v1/posts/{post_id}/view")
def view_post(post_id: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):""",
     "delete endpoint without ownership check")

shutil.copy(p, "/tmp/main.py.ch16a.bak")
p.write_text(text)
print("written, backup at /tmp/main.py.ch16a.bak")
```

Attack, in order. The historic measurement post `6ade0898...` was
deliberately NOT touched; a fresh victim post was created for this attack:

- Ali's victim post created: id `6aded3433bfb69f7c8ba6400`
  ("idor victim post"). Feed count for that id: **1**.
- Bob's DELETE on ali's post: `{"message": "post deleted",
  "id": "6aded3433bfb69f7c8ba6400"}`.
- Feed count for that id afterwards: **0**. The post was gone.
- DELETE with no login at all: `401 invalid or expired session`.

Reading: authentication was complete (no login gives 401), CSRF was
complete, existence was checked — and a user still deleted another
user's post. That is the whole IDOR lesson in one sequence:
authentication is not authorization.

## Step 2b — Backend patch: the ownership check

The fix is two lines plus a flag. The check runs after existence and
after CSRF, and before the delete:

- If the flag is on and the post's owner is not the token user, return
  `403 not your post` and delete nothing.
- If the flag is off, the old vulnerable behaviour returns — demo only,
  never production (see Step 3 and Limitations).

Flag: `OWNER_CHECK_ENABLED` (default `true`), defined before
`@app.get("/health")` like the other feature flags.

Backend patch script (`/tmp/ch16_step02b_patch.py`, exactly-once,
backup `/tmp/main.py.ch16b.bak` — do NOT re-run it):

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

must('@app.get("/health")',
     '''OWNER_CHECK_ENABLED = os.getenv("OWNER_CHECK_ENABLED", "true").lower() == "true"


@app.get("/health")''', "owner check flag")

must("""    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    posts_collection.delete_one({"_id": post["_id"]})""",
     """    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    if OWNER_CHECK_ENABLED and post.get("username") != username:
        raise HTTPException(status_code=403, detail="not your post")
    posts_collection.delete_one({"_id": post["_id"]})""",
     "ownership check")

shutil.copy(p, "/tmp/main.py.ch16b.bak")
p.write_text(text)
print("written, backup at /tmp/main.py.ch16b.bak")
```

Re-attack, in order:

- Ali's victim post 2 created: id `6aded4584c4cf9c3481da566`
  ("idor victim post 2").
- Bob's DELETE: `403 not your post`.
- Feed count for that id: still **1**. The post survived.
- Ali's DELETE on his own post: `post deleted`.
- Feed count for that id afterwards: **0**.

Reading: the owner can delete, the non-owner cannot, and the same call
that succeeded in Step 2a now fails with 403. The behaviour change
itself is the proof the patch is live.

One nuance, stated honestly: the check order is existence first
(`get_post_or_404`), then ownership. An attacker therefore sees a
difference — 404 for a missing post, 403 for someone else's existing
post — which leaks existence. That is acceptable here because posts are
public in the feed anyway: existence is not a secret in this app. If
posts were private, both cases should return 404, so existence itself
would not leak.

## Step 2b — Frontend patch: Delete button on your own posts only

The button is convenience, not security. Hiding it does not protect
anything — Bob's curl DELETE still returns 403, button or no button.
The server check is the security; the button only shows the user what
they are allowed to do.

`feed.js` `renderPost` already builds an `actionsDiv` with the Like
button, and `currentUsername` is set by `initFeed` from `/me`. The patch
adds a Delete button into the same `actionsDiv`, only when
`post.username === currentUsername`, calling DELETE with the CSRF
headers and refreshing the feed with `loadFeed(false)` (no view
increment on a refresh).

Frontend patch script (`/tmp/ch16_step02b_frontend_patch.py`,
exactly-once, backup `/tmp/feed.js.ch16.bak` — do NOT re-run it):

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

must("""  actionsDiv.appendChild(likeButton);
  postDiv.appendChild(actionsDiv);""",
     """  actionsDiv.appendChild(likeButton);
  if (post.username === currentUsername) {
    const deleteButton = document.createElement('button');
    deleteButton.textContent = 'Delete';
    deleteButton.addEventListener('click', function () {
      fetch('/api/v1/posts/' + post.id, {
        method: 'DELETE',
        headers: csrfHeaders(),
        credentials: 'same-origin',
      }).then(function () { loadFeed(false); });
    });
    actionsDiv.appendChild(deleteButton);
  }
  postDiv.appendChild(actionsDiv);""",
     "delete button for own posts only")

shutil.copy(p, "/tmp/feed.js.ch16.bak")
p.write_text(text)
print("written, backup at /tmp/feed.js.ch16.bak")
```

Verification: assert `found: 1`, diff showed exactly one inserted block,
`node --check` reported JS syntax OK, `grep` showed the delete button
lines only in that block. Browser check, Rao's report: posts delete
correctly, and the Delete button appears only in front of the user's own
posts, never in front of others' posts.

## Step 3 — Measure: the flag is the difference

Flag off (`OWNER_CHECK_ENABLED=false`), in order:

- Ali's victim post 3: id `6aded5dc15843972968664d2`.
- Bob's DELETE: `post deleted`.
- Feed count for that id afterwards: **0**. The vulnerable behaviour
  returned exactly when the flag went off.

Normal restart (no environment overrides), in order:

- Ali's victim post 4: id `6aded5f9765097068fb54eec`.
- Bob's DELETE: `403 not your post`.
- Feed count for that id: still **1**.
- Ali's DELETE on his own post: `post deleted`.
- Feed count for that id afterwards: **0**.

Final state: the server runs with the normal command, the flag on, and
the ownership check live.

## Comparison

| Check | Before (Step 2a, no check) | After (Step 2b, check on) | Flag off |
|---|---|---|---|
| Bob deletes ali's post | `post deleted`, feed 0 | `403 not your post`, feed 1 | `post deleted`, feed 0 |
| Ali deletes his own post | `post deleted`, feed 0 | `post deleted`, feed 0 | `post deleted`, feed 0 |
| DELETE with no login | `401 invalid or expired session` | `401 invalid or expired session` | `401 invalid or expired session` |
| Like with body username "hacker" | Recorded for token user ali, never "hacker" | Same (unchanged, already correct) | Same |
| Delete button in the browser | Did not exist | Only on your own posts | Only on your own posts (UI does not change the server rule) |
| Normal app (login, feed, post, like, comment, view, unique views, logout) | Works | Works, unchanged | Works, unchanged |

## Honest limitations

- Only delete is guarded in this chapter, because only delete changes
  another user's object today. Edit does not exist yet; when it is added,
  it needs the same ownership check, in the same place, before the write.
- The flag-off behaviour is deliberately vulnerable and exists only for
  the demo comparison. It must never be left on in any shared or
  production deployment. The default is on, and the final server state is
  on.
- The existence-vs-ownership order (404 then 403) leaks whether a post
  exists. Acceptable here because posts are public in the feed; for
  private objects, return 404 for both cases.
- Deleting a post does not delete its Redis unique-view sketch
  (`uv:post:<id>`). The sketch becomes invisible because the feed no
  longer lists the post, but the key remains until it is cleaned up.
  Sketch cleanup on delete is a small follow-up, not built here.
- Hiding the Delete button is not a security control. Any user can still
  send the DELETE request by hand; only the server's 403 stops them.
  That is why the curl tests, not the screenshots, are the proof in this
  chapter.
- Token and cookie values are never pasted into chat or notes. Only
  post ids, counts, and endpoint results are recorded.

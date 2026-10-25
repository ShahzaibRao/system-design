# Chapter 09 - Feed Page

## Goal
Build an Instagram-like feed: posts, likes, comments, and views, first as an API, then as a browser page, in plain HTML, CSS, and JavaScript.

## Step 1 - Feed API
- Collection: `posts` in MongoDB, fields username, content, created_at, liked_by list, comments list, views count, index on created_at descending.
- Endpoints: POST /api/v1/posts, GET /api/v1/feed newest first limit 20, POST /api/v1/posts/{id}/like, POST /api/v1/posts/{id}/comment, POST /api/v1/posts/{id}/view.
- Like is idempotent: likes_count is the length of liked_by, so a second like from the same user does not increase the count.
- Comment is stored inside the post document in simple version, no separate collection yet.
- View is total only in simple version, every view adds 1, refresh also adds 1.
- Test results: post ali Hello from Social Lab first post, post rao DevOps and System Design, post frontendtest1 Frontend now shows feed, feed order newest first frontendtest1, rao, ali. Like rao gave likes_count 1, second like from rao still 1, proving idempotence. Comment ali Nice post gave comments 1. View gave views 1. Final feed showed frontendtest1 likes 1, comments 1, views 1.

## Step 2 - Feed frontend page
- Files: `frontend/feed.html`, `frontend/feed.js`, `frontend/feed.css`, no backend change.
- User content is shown with textContent, not innerHTML, so HTML or script text in a post is shown as text, not run as code.
- Acting username is the username typed in the create post form, because there is no session yet.
- Views increase on initial page load only, not on like or comment refresh, otherwise every like would also inflate views.
- Test results: feed page showed ali post Hello from Social Lab first post, meta 1 likes | 18 views | 1 comments, versus 0, 0, 0 at API test time. Comment rao: hy from rao shown. Like, comment, and refresh view increase all worked, with multiple usernames tried.

## Limitations, still present
- No session or token yet, username is supplied in the request body or typed in the form, anyone can post or like as anyone.
- Views are total only, not unique, refresh counts again. Unique views with HyperLogLog is a later enhancement.
- No followers yet, feed shows all posts, not only followed users.
- No pagination beyond limit 20, no edit or delete, no HTML escaping beyond textContent for post and comment text.

## Next steps, one at a time
1. Session handling so login actually keeps the user logged in for the feed
2. Unique views with HyperLogLog, total plus unique
3. Followers, feed from followed users only

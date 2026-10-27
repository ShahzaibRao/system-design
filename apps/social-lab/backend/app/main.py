import os
import hashlib
import json
import redis
import hmac
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Cookie, Header, Request
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import bcrypt
from pymongo import MongoClient
from pymongo.read_preferences import ReadPreference
from pymongo.errors import DuplicateKeyError
from bson import ObjectId
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .core.bloom import BloomFilter

app = FastAPI(title="social-lab")

CSP_ENABLED = os.environ.get("CSP_ENABLED", "true").lower() == "true"
CSP_POLICY = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
FEED_CACHE_ENABLED = os.environ.get("FEED_CACHE_ENABLED", "true").lower() == "true"
FEED_CACHE_TTL_SECONDS = int(os.environ.get("FEED_CACHE_TTL_SECONDS", "60"))
FOLLOW_ENABLED = os.environ.get("FOLLOW_ENABLED", "true").lower() == "true"


@app.middleware("http")
async def add_csp_headers(request: Request, call_next):
    response = await call_next(request)
    if CSP_ENABLED:
        response.headers["Content-Security-Policy"] = CSP_POLICY
    return response

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
FILTER_ENABLED = os.environ.get("FILTER_ENABLED", "true").lower() == "true"
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() == "true"
MAX_FAILED_ATTEMPTS = 5
BLOCK_SECONDS = 60
SESSION_EXPIRY_HOURS = 24
client = MongoClient(MONGO_URI)
db = client["social_lab"]
users_collection = db["users"]
users_collection.create_index("username", unique=True)
users_collection.create_index("email", unique=True)
posts_collection = db["posts"]
posts_collection.create_index([("username", 1), ("created_at", -1), ("_id", -1)])
posts_collection.create_index([("created_at", -1), ("_id", -1)])
sessions_collection = db["sessions"]
sessions_collection.create_index("token_hash", unique=True)
sessions_collection.create_index("expires_at", expireAfterSeconds=0)
refresh_tokens_collection = db["refresh_tokens"]
refresh_tokens_collection.create_index("token_hash", unique=True)
refresh_tokens_collection.create_index("expires_at", expireAfterSeconds=0)
used_refresh_tokens_collection = db["used_refresh_tokens"]
used_refresh_tokens_collection.create_index("token_hash", unique=True)
used_refresh_tokens_collection.create_index("expires_at", expireAfterSeconds=0)

username_filter = BloomFilter(capacity=10000, error_rate=0.01)
login_attempts = {}
login_lock = threading.Lock()

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

USERNAME_CHECK_TOTAL = Counter(
    "sociallab_username_check_total",
    "Username availability checks by outcome",
    ["result"],
)
USERNAME_CHECK_DURATION = Histogram(
    "sociallab_username_check_duration_seconds",
    "Username availability check duration in seconds",
)
LOGIN_TOTAL = Counter(
    "sociallab_login_total",
    "Login attempts by outcome",
    ["result"],
)


@app.on_event("startup")
def load_filter_from_db():
    count = 0
    for user in users_collection.find({}, {"username": 1}):
        username_filter.add(user["username"])
        count += 1
    print(f"Bloom filter loaded: {count} usernames from MongoDB")


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class PostCreate(BaseModel):
    content: str


class LikeRequest(BaseModel):
    pass


class CommentRequest(BaseModel):
    text: str


def post_to_dict(post):
    return {
        "id": str(post["_id"]),
        "username": post["username"],
        "content": post["content"],
        "created_at": post["created_at"].isoformat() if post.get("created_at") else None,
        "likes_count": len(post.get("liked_by", [])),
        "liked_by": post.get("liked_by", []),
        "comments": post.get("comments", []),
        "views": post.get("views", 0),
    }


def get_post_or_404(post_id):
    try:
        object_id = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=404, detail="post not found")
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": object_id})
    if not post:
        raise HTTPException(status_code=404, detail="post not found")
    return post


def get_username_from_cookie(session_token):
    if not session_token:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    token_hash = hashlib.sha256(session_token.encode()).hexdigest()
    session = sessions_collection.find_one({"token_hash": token_hash})
    if not session:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    expires_at = session.get("expires_at")
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and datetime.now(timezone.utc) >= expires_at:
        sessions_collection.delete_one({"token_hash": token_hash})
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return session["username"]


def validate_csrf(csrf_token_cookie, csrf_token_header):
    if not csrf_token_cookie or not csrf_token_header:
        raise HTTPException(status_code=403, detail="csrf token missing")
    if not hmac.compare_digest(csrf_token_cookie, csrf_token_header):
        raise HTTPException(status_code=403, detail="csrf token invalid")


REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
UNIQUE_VIEWS_ENABLED = os.getenv("UNIQUE_VIEWS_ENABLED", "true").lower() == "true"
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2)
    redis_client.ping()
except Exception:
    redis_client = None


def track_unique_view(post_id, username):
    if not UNIQUE_VIEWS_ENABLED or redis_client is None:
        return
    try:
        redis_client.pfadd("uv:post:" + post_id, username)
    except Exception:
        pass


def get_unique_views(post_id):
    if not UNIQUE_VIEWS_ENABLED or redis_client is None:
        return None
    try:
        return redis_client.pfcount("uv:post:" + post_id)
    except Exception:
        return None


ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "900"))
REFRESH_TOKEN_TTL_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "7"))
REFRESH_TOKENS_ENABLED = os.getenv("REFRESH_TOKENS_ENABLED", "true").lower() == "true"


OWNER_CHECK_ENABLED = os.getenv("OWNER_CHECK_ENABLED", "true").lower() == "true"


CURSOR_PAGINATION_ENABLED = os.getenv("CURSOR_PAGINATION_ENABLED", "true").lower() == "true"


@app.get("/health")
def health():
    return {"status": "ok", "app": "social-lab"}


@app.get("/")
def root():
    return FileResponse(FRONTEND_DIR / "signup.html")


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/v1/auth/check-username")
def check_username(username: str):
    start_time = time.time()
    if FILTER_ENABLED and username not in username_filter:
        USERNAME_CHECK_TOTAL.labels(result="filter_no_db").inc()
        USERNAME_CHECK_DURATION.observe(time.time() - start_time)
        return {"username": username, "available": True}
    user = users_collection.find_one({"username": username})
    result = "db_taken" if user else "db_available"
    USERNAME_CHECK_TOTAL.labels(result=result).inc()
    USERNAME_CHECK_DURATION.observe(time.time() - start_time)
    return {"username": username, "available": user is None}


@app.post("/api/v1/auth/signup", status_code=201)
def signup(data: SignupRequest):
    if FILTER_ENABLED:
        if data.username in username_filter:
            if users_collection.find_one({"username": data.username}):
                raise HTTPException(status_code=400, detail="username taken")
    else:
        if users_collection.find_one({"username": data.username}):
            raise HTTPException(status_code=400, detail="username taken")
    if users_collection.find_one({"email": data.email}):
        raise HTTPException(status_code=400, detail="email taken")
    hashed_password = bcrypt.hashpw(data.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        result = users_collection.insert_one({
            "username": data.username,
            "email": data.email,
            "password": hashed_password,
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=400, detail="username or email taken")
    username_filter.add(data.username)
    return {"id": str(result.inserted_id), "username": data.username, "email": data.email}


@app.post("/api/v1/auth/login")
def login(data: LoginRequest, response: Response):
    if RATE_LIMIT_ENABLED:
        with login_lock:
            now = time.time()
            record = login_attempts.get(data.username)
            if record and record.get("blocked_until", 0) > now:
                LOGIN_TOTAL.labels(result="blocked").inc()
                raise HTTPException(status_code=429, detail="too many login attempts, try again later")
            if record and record.get("blocked_until", 0) and now >= record["blocked_until"]:
                login_attempts.pop(data.username, None)
                record = {"count": 0, "blocked_until": 0}
            if record is None:
                record = {"count": 0, "blocked_until": 0}
            record["count"] += 1
            if record["count"] >= MAX_FAILED_ATTEMPTS:
                record["blocked_until"] = time.time() + BLOCK_SECONDS
            login_attempts[data.username] = record
    user = users_collection.find_one({"username": data.username})
    if not user or not bcrypt.checkpw(data.password.encode("utf-8"), user["password"].encode("utf-8")):
        LOGIN_TOTAL.labels(result="invalid").inc()
        raise HTTPException(status_code=401, detail="invalid username or password")
    if RATE_LIMIT_ENABLED:
        with login_lock:
            login_attempts.pop(data.username, None)
    LOGIN_TOTAL.labels(result="success").inc()
    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    access_expires_at = now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS) if REFRESH_TOKENS_ENABLED else now + timedelta(hours=SESSION_EXPIRY_HOURS)
    sessions_collection.insert_one({
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "username": user["username"],
        "created_at": now,
        "expires_at": access_expires_at,
    })
    response.set_cookie(
        key="social_lab_session",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_TTL_SECONDS if REFRESH_TOKENS_ENABLED else 24 * 60 * 60,
        path="/",
        samesite="lax",
        secure=False,  # localhost HTTP demo only, production must be True with HTTPS
    )
    csrf_token = secrets.token_hex(32)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,  # frontend JS must read it to send as header, it is not a session token
        max_age=REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60 if REFRESH_TOKENS_ENABLED else 24 * 60 * 60,
        path="/",
        samesite="lax",
        secure=False,  # localhost HTTP demo only, production must be True with HTTPS
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
    return {"message": "login successful", "username": user["username"]}


@app.get("/api/v1/auth/me")
def me(social_lab_session: str | None = Cookie(None)):
    username = get_username_from_cookie(social_lab_session)
    return {"username": username}


@app.post("/api/v1/auth/logout")
def logout(response: Response, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None), social_lab_refresh: str | None = Cookie(None)):
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
    return {"message": "refreshed", "username": username}


@app.post("/api/v1/posts", status_code=201)
def create_post(data: PostCreate, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    result = posts_collection.insert_one({
        "username": username,
        "content": data.content.strip(),
        "created_at": datetime.now(timezone.utc),
        "liked_by": [],
        "comments": [],
        "views": 0,
    })
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": result.inserted_id})
    invalidate_feed_cache()
    return post_to_dict(post)


def _make_cursor(post):
    return post["created_at"].isoformat() + "_" + str(post["_id"])


def _parse_cursor(cursor):
    created_str, post_id = cursor.rsplit("_", 1)
    created_at = datetime.fromisoformat(created_str)
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    return created_at, ObjectId(post_id)


def _feed_cache_key(limit, cursor):
    return "feed:page:" + str(limit) + ":" + (cursor if cursor else "first")


def invalidate_feed_cache():
    if redis_client is None:
        return
    try:
        for key in redis_client.scan_iter("feed:*"):
            redis_client.delete(key)
    except Exception:
        pass


@app.get("/api/v1/feed")
def get_feed(response: Response, limit: int = 20, cursor: str | None = None):
    if not CURSOR_PAGINATION_ENABLED:
        posts = posts_collection.find().sort("created_at", -1).limit(20)
        result = [post_to_dict(post) for post in posts]
        for item in result:
            item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
        response.headers["X-Cache"] = "BYPASS"
        return result
    limit = max(1, min(limit, 100))
    cache_key = _feed_cache_key(limit, cursor)
    if FEED_CACHE_ENABLED and redis_client is not None:
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            return json.loads(cached)
    query = {}
    if cursor:
        try:
            cursor_time, cursor_id = _parse_cursor(cursor)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid cursor")
        query = {"$or": [{"created_at": {"$lt": cursor_time}}, {"created_at": cursor_time, "_id": {"$lt": cursor_id}}]}
    docs = list(posts_collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(limit + 1))
    has_more = len(docs) > limit
    docs = docs[:limit]
    result = [post_to_dict(post) for post in docs]
    for item in result:
        item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
    next_cursor = _make_cursor(docs[-1]) if has_more and docs else None
    payload = {"posts": result, "next_cursor": next_cursor}
    if FEED_CACHE_ENABLED and redis_client is not None:
        try:
            redis_client.setex(cache_key, FEED_CACHE_TTL_SECONDS, json.dumps(payload))
        except Exception:
            pass
    response.headers["X-Cache"] = "MISS"
    return payload


@app.post("/api/v1/posts/{post_id}/like")
def like_post(post_id: str, data: LikeRequest, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    if username not in post.get("liked_by", []):
        posts_collection.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
    invalidate_feed_cache()
    return post_to_dict(post)


@app.post("/api/v1/posts/{post_id}/comment")
def comment_post(post_id: str, data: CommentRequest, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="comment text is required")
    post = get_post_or_404(post_id)
    comment = {
        "username": username,
        "text": data.text.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    posts_collection.update_one({"_id": post["_id"]}, {"$push": {"comments": comment}})
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
    invalidate_feed_cache()
    return post_to_dict(post)


@app.delete("/api/v1/posts/{post_id}")
def delete_post(post_id: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    if OWNER_CHECK_ENABLED and post.get("username") != username:
        raise HTTPException(status_code=403, detail="not your post")
    posts_collection.delete_one({"_id": post["_id"]})
    try:
        if redis_client is not None:
            redis_client.delete("uv:post:" + post_id)
    except Exception:
        pass
    invalidate_feed_cache()
    return {"message": "post deleted", "id": post_id}


follows_collection = posts_collection.database["follows"]
try:
    follows_collection.create_index([("follower", 1), ("following", 1)], unique=True)
except Exception:
    pass


def _get_following(username):
    return [doc["following"] for doc in follows_collection.find({"follower": username}, {"following": 1})]


@app.post("/api/v1/users/{username}/follow")
def follow_user(username: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    if username == current:
        raise HTTPException(status_code=400, detail="cannot follow yourself")
    target = users_collection.find_one({"username": username})
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    follows_collection.update_one({"follower": current, "following": username}, {"$setOnInsert": {"created_at": datetime.now(timezone.utc).replace(tzinfo=None)}}, upsert=True)
    invalidate_feed_cache()
    return {"following": username, "status": "following"}


@app.post("/api/v1/users/{username}/unfollow")
def unfollow_user(username: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    if username == current:
        raise HTTPException(status_code=400, detail="cannot follow yourself")
    target = users_collection.find_one({"username": username})
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    follows_collection.delete_one({"follower": current, "following": username})
    invalidate_feed_cache()
    return {"following": username, "status": "not_following"}


@app.get("/api/v1/following")
def get_following_list(social_lab_session: str | None = Cookie(None)):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(social_lab_session)
    return {"following": _get_following(current)}


@app.get("/api/v1/feed/following")
def get_following_feed(response: Response, limit: int = 20, cursor: str | None = None, social_lab_session: str | None = Cookie(None)):
    if not FOLLOW_ENABLED:
        raise HTTPException(status_code=404, detail="follows disabled")
    current = get_username_from_cookie(social_lab_session)
    following = _get_following(current)
    if not CURSOR_PAGINATION_ENABLED:
        posts = posts_collection.find({"username": {"$in": following}}).sort("created_at", -1).limit(20)
        result = [post_to_dict(post) for post in posts]
        for item in result:
            item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
        response.headers["X-Cache"] = "BYPASS"
        return result
    limit = max(1, min(limit, 100))
    cache_key = "feed:following:" + current + ":" + str(limit) + ":" + (cursor if cursor else "first")
    if FEED_CACHE_ENABLED and redis_client is not None:
        try:
            cached = redis_client.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            return json.loads(cached)
    query = {"username": {"$in": following}}
    if cursor:
        try:
            cursor_time, cursor_id = _parse_cursor(cursor)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid cursor")
        query = {"username": {"$in": following}, "$or": [{"created_at": {"$lt": cursor_time}}, {"created_at": cursor_time, "_id": {"$lt": cursor_id}}]}
    docs = list(posts_collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(limit + 1))
    has_more = len(docs) > limit
    docs = docs[:limit]
    result = [post_to_dict(post) for post in docs]
    for item in result:
        item["unique_views"] = get_unique_views(item["id"]) if UNIQUE_VIEWS_ENABLED else None
    next_cursor = _make_cursor(docs[-1]) if has_more and docs else None
    payload = {"posts": result, "next_cursor": next_cursor}
    if FEED_CACHE_ENABLED and redis_client is not None:
        try:
            redis_client.setex(cache_key, FEED_CACHE_TTL_SECONDS, json.dumps(payload))
        except Exception:
            pass
    response.headers["X-Cache"] = "MISS"
    return payload


@app.post("/api/v1/posts/{post_id}/view")
def view_post(post_id: str, social_lab_session: str | None = Cookie(None), csrf_token: str | None = Cookie(None), x_csrf_token: str | None = Header(None)):
    username = get_username_from_cookie(social_lab_session)
    validate_csrf(csrf_token, x_csrf_token)
    post = get_post_or_404(post_id)
    posts_collection.update_one({"_id": post["_id"]}, {"$inc": {"views": 1}})
    track_unique_view(post_id, username)
    post = posts_collection.with_options(read_preference=ReadPreference.PRIMARY).find_one({"_id": post["_id"]})
    result = post_to_dict(post)
    result["unique_views"] = get_unique_views(post_id) if UNIQUE_VIEWS_ENABLED else None
    return result




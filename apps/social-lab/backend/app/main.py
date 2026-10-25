import os
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import bcrypt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from bson import ObjectId
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .core.bloom import BloomFilter

app = FastAPI(title="social-lab")

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
posts_collection.create_index([("created_at", -1)])
sessions_collection = db["sessions"]
sessions_collection.create_index("token", unique=True)
sessions_collection.create_index("expires_at", expireAfterSeconds=0)

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
    username: str
    content: str


class LikeRequest(BaseModel):
    username: str


class CommentRequest(BaseModel):
    username: str
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
    post = posts_collection.find_one({"_id": object_id})
    if not post:
        raise HTTPException(status_code=404, detail="post not found")
    return post


def get_username_from_token(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="invalid or expired session")
    token = authorization[7:]
    session = sessions_collection.find_one({"token": token})
    if not session:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    expires_at = session.get("expires_at")
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and datetime.now(timezone.utc) >= expires_at:
        sessions_collection.delete_one({"token": token})
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return session["username"]


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
def login(data: LoginRequest):
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
    sessions_collection.insert_one({
        "token": token,
        "username": user["username"],
        "created_at": now,
        "expires_at": now + timedelta(hours=SESSION_EXPIRY_HOURS),
    })
    return {"message": "login successful", "username": user["username"], "token": token}


@app.get("/api/v1/auth/me")
def me(authorization: str = Header(None)):
    username = get_username_from_token(authorization)
    return {"username": username}


@app.post("/api/v1/auth/logout")
def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        sessions_collection.delete_one({"token": authorization[7:]})
    return {"message": "logged out"}


@app.post("/api/v1/posts", status_code=201)
def create_post(data: PostCreate, authorization: str = Header(None)):
    username = get_username_from_token(authorization)
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
    post = posts_collection.find_one({"_id": result.inserted_id})
    return post_to_dict(post)


@app.get("/api/v1/feed")
def get_feed():
    posts = posts_collection.find().sort("created_at", -1).limit(20)
    return [post_to_dict(post) for post in posts]


@app.post("/api/v1/posts/{post_id}/like")
def like_post(post_id: str, data: LikeRequest, authorization: str = Header(None)):
    username = get_username_from_token(authorization)
    post = get_post_or_404(post_id)
    if username not in post.get("liked_by", []):
        posts_collection.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
    post = posts_collection.find_one({"_id": post["_id"]})
    return post_to_dict(post)


@app.post("/api/v1/posts/{post_id}/comment")
def comment_post(post_id: str, data: CommentRequest, authorization: str = Header(None)):
    username = get_username_from_token(authorization)
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="comment text is required")
    post = get_post_or_404(post_id)
    comment = {
        "username": username,
        "text": data.text.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    posts_collection.update_one({"_id": post["_id"]}, {"$push": {"comments": comment}})
    post = posts_collection.find_one({"_id": post["_id"]})
    return post_to_dict(post)


@app.post("/api/v1/posts/{post_id}/view")
def view_post(post_id: str):
    post = get_post_or_404(post_id)
    posts_collection.update_one({"_id": post["_id"]}, {"$inc": {"views": 1}})
    post = posts_collection.find_one({"_id": post["_id"]})
    return post_to_dict(post)




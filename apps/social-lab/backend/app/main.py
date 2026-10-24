import os
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import bcrypt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .core.bloom import BloomFilter

app = FastAPI(title="social-lab")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
FILTER_ENABLED = os.environ.get("FILTER_ENABLED", "true").lower() == "true"
client = MongoClient(MONGO_URI)
db = client["social_lab"]
users_collection = db["users"]
users_collection.create_index("username", unique=True)
users_collection.create_index("email", unique=True)

username_filter = BloomFilter(capacity=10000, error_rate=0.01)

USERNAME_CHECK_TOTAL = Counter(
    "sociallab_username_check_total",
    "Username availability checks by outcome",
    ["result"],
)
USERNAME_CHECK_DURATION = Histogram(
    "sociallab_username_check_duration_seconds",
    "Username availability check duration in seconds",
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


@app.get("/health")
def health():
    return {"status": "ok", "app": "social-lab"}


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
    user = users_collection.find_one({"username": data.username})
    if not user or not bcrypt.checkpw(data.password.encode("utf-8"), user["password"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"message": "login successful", "username": user["username"]}

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import bcrypt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
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


@app.get("/api/v1/auth/check-username")
def check_username(username: str):
    if FILTER_ENABLED and username not in username_filter:
        return {"username": username, "available": True}
    user = users_collection.find_one({"username": username})
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

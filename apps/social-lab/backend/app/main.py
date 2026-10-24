import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import bcrypt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

app = FastAPI(title="social-lab")

# Mongo connection: env var se, warna localhost default
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
client = MongoClient(MONGO_URI)
db = client["social_lab"]
users_collection = db["users"]
# Unique indexes: DB level par duplicate rokne ke liye
users_collection.create_index("username", unique=True)
users_collection.create_index("email", unique=True)


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


@app.post("/api/v1/auth/signup", status_code=201)
def signup(data: SignupRequest):
    # Pehle app level check, phir DB insert, DB index final guard hai
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
    return {"id": str(result.inserted_id), "username": data.username, "email": data.email}


@app.post("/api/v1/auth/login")
def login(data: LoginRequest):
    user = users_collection.find_one({"username": data.username})
    if not user or not bcrypt.checkpw(data.password.encode("utf-8"), user["password"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"message": "login successful", "username": user["username"]}

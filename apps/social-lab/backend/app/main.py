from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import bcrypt

app = FastAPI(title="social-lab")

users = {}
next_id = 1


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
    global next_id
    if data.username in users:
        raise HTTPException(status_code=400, detail="username taken")
    for user in users.values():
        if user["email"] == data.email:
            raise HTTPException(status_code=400, detail="email taken")
    # CHANGE: password plain nahi, bcrypt hash store ho raha hai
    hashed_password = bcrypt.hashpw(data.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user = {"id": next_id, "username": data.username, "email": data.email, "password": hashed_password}
    users[data.username] = user
    next_id += 1
    return {"id": user["id"], "username": user["username"], "email": user["email"]}


@app.post("/api/v1/auth/login")
def login(data: LoginRequest):
    user = users.get(data.username)
    # CHANGE: plain compare nahi, bcrypt check ho raha hai
    if not user or not bcrypt.checkpw(data.password.encode("utf-8"), user["password"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"message": "login successful", "username": user["username"]}

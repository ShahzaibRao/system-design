from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="social-lab")

# Simple version: users sirf memory me hain, koi DB nahi.
# Server restart hote hi ye data khatam ho jayega, yehi is version ki limit hai.
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
    # Simple duplicate check: username seedha dict me dekha
    if data.username in users:
        raise HTTPException(status_code=400, detail="username taken")
    # Email ke liye poori dict scan karni par rahi hai, ye slow tareeqa hai
    # Filter aur index wala step is ko behtar karega
    for user in users.values():
        if user["email"] == data.email:
            raise HTTPException(status_code=400, detail="email taken")
    user = {"id": next_id, "username": data.username, "email": data.email, "password": data.password}
    users[data.username] = user
    next_id += 1
    return {"id": user["id"], "username": user["username"], "email": user["email"]}


@app.post("/api/v1/auth/login")
def login(data: LoginRequest):
    user = users.get(data.username)
    if not user or user["password"] != data.password:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"message": "login successful", "username": user["username"]}

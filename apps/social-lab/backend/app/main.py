from fastapi import FastAPI

app = FastAPI(title="social-lab")

@app.get("/health")
def health():
    return {"status": "ok", "app": "social-lab"}

from fastapi import FastAPI

from app.routers import auth, centres

app = FastAPI(title="EVE Diagnostics API")

app.include_router(auth.router)
app.include_router(centres.router)


@app.get("/health")
def health():
    return {"status": "ok"}

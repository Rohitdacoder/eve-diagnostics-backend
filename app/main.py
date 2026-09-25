from fastapi import FastAPI

app = FastAPI(title="EVE Diagnostics API")


@app.get("/health")
def health():
    return {"status": "ok"}

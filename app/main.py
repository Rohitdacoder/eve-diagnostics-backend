from fastapi import FastAPI

from app.core.errors import AppError, app_error_handler
from app.routers import auth, bookings, centres

app = FastAPI(title="EVE Diagnostics API")

app.add_exception_handler(AppError, app_error_handler)

app.include_router(auth.router)
app.include_router(centres.router)
app.include_router(bookings.router)


@app.get("/health")
def health():
    return {"status": "ok"}

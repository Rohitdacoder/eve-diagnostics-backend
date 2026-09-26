from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models import BookingStatus, User
from app.schemas.booking import BookingCreate, BookingOut
from app.schemas.common import Page
from app.services import bookings as service

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("/", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(
    data: BookingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    booking = service.create_booking(db, user, data.centre_id, data.test_id, data.appointment_at)
    return BookingOut.from_model(booking)


@router.get("/", response_model=Page[BookingOut])
def list_bookings(
    status: BookingStatus | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, total = service.list_bookings(db, user, status, limit, offset)
    return Page(items=[BookingOut.from_model(b) for b in items], total=total, limit=limit, offset=offset)


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return BookingOut.from_model(service.get_booking(db, user, booking_id))


@router.post("/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return BookingOut.from_model(service.cancel_booking(db, user, booking_id))

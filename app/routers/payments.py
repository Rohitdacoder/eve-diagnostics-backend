import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models import PaymentStatus, User
from app.schemas.payment import PaymentCreate, PaymentOut, WebhookPayload, WebhookResponse
from app.services import payments as service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create_payment(
    data: PaymentCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payment, created = service.create_payment(db, user, data.booking_id, data.simulate, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    db.refresh(payment)
    return PaymentOut.from_model(payment)


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post(
    "/webhook/",
    response_model=WebhookResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": WebhookPayload.model_json_schema()}},
        }
    },
)
def payment_webhook(
    body: bytes = Depends(raw_body),
    x_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Called by the payment provider. Signed with HMAC-SHA256 of the raw body in X-Signature."""
    # check the signature on the exact bytes received, before parsing anything
    if not service.verify_signature(body, x_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        raw = json.loads(body)
        data = WebhookPayload.model_validate(raw)
    except (ValueError, ValidationError) as e:
        detail = e.errors() if isinstance(e, ValidationError) else "Invalid JSON"
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

    return service.handle_webhook(
        db, data.event_id, data.provider_payment_id, PaymentStatus(data.status), raw
    )

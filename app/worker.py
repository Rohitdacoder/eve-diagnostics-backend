"""Celery worker and scheduled jobs.

    celery -A app.worker worker --beat --loglevel=info
"""
import logging

from celery import Celery

from app.config import settings
from app.core.logging import setup_logging
from app.database import SessionLocal
from app.services.bookings import expire_unpaid_bookings
from app.services.payments import retry_pending_events

setup_logging()
log = logging.getLogger(__name__)

celery = Celery("eve", broker=settings.celery_broker_url)
celery.conf.update(
    timezone="UTC",
    worker_hijack_root_logger=False,
    beat_schedule={
        "retry-webhook-events": {"task": "app.worker.retry_webhook_events", "schedule": 30.0},
        "expire-unpaid-bookings": {"task": "app.worker.expire_bookings", "schedule": 300.0},
    },
)


@celery.task
def retry_webhook_events():
    with SessionLocal() as db:
        count = retry_pending_events(db)
    if count:
        log.info("retried %s webhook events", count)
    return count


@celery.task
def expire_bookings():
    with SessionLocal() as db:
        count = expire_unpaid_bookings(db)
    if count:
        log.info("cancelled %s unpaid bookings past their appointment time", count)
    return count

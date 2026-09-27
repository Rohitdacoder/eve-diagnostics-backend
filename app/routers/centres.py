from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core import cache
from app.core.deps import require_admin
from app.database import get_db
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest
from app.schemas.centre import (
    CentreCreate,
    CentreDetail,
    CentreOut,
    CentreUpdate,
    OfferingCreate,
    OfferingOut,
    OfferingUpdate,
    TestCreate,
    TestOut,
)
from app.schemas.common import Page

router = APIRouter(tags=["centres"])


def _get_centre_or_404(db: Session, centre_id: int) -> DiagnosticCentre:
    centre = db.get(DiagnosticCentre, centre_id)
    if centre is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Centre not found")
    return centre


def _offering_out(o: CentreTest) -> OfferingOut:
    return OfferingOut(test_id=o.test_id, test_name=o.test.name, price=o.price, is_active=o.is_active)


def _commit_or_409(db: Session, detail: str):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    cache.invalidate_catalog()


def _cached(name: str, build) -> Response:
    """Return the cached JSON for `name`, or build it, cache it and return it."""
    body = cache.get(name)
    hit = body is not None
    if not hit:
        body = build().model_dump_json()
        cache.set(name, body)
    return Response(content=body, media_type="application/json", headers={"X-Cache": "HIT" if hit else "MISS"})


def _escape_like(value: str) -> str:
    # so % and _ typed by the user are matched literally
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------- tests catalog ----------

@router.get("/tests/", response_model=Page[TestOut])
def list_tests(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    def build():
        total = db.scalar(select(func.count()).select_from(DiagnosticTest))
        items = db.scalars(
            select(DiagnosticTest).order_by(DiagnosticTest.id).limit(limit).offset(offset)
        ).all()
        return Page[TestOut](items=items, total=total, limit=limit, offset=offset)

    return _cached(f"tests:{limit}:{offset}", build)


@router.post("/tests/", response_model=TestOut, status_code=status.HTTP_201_CREATED)
def create_test(data: TestCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    test = DiagnosticTest(**data.model_dump())
    db.add(test)
    _commit_or_409(db, "A test with this name already exists")
    return test


# ---------- centres ----------

@router.get("/centres/", response_model=Page[CentreOut])
def list_centres(
    location: str | None = Query(None, max_length=100, description="Filter by location (partial match)"),
    test_id: int | None = Query(None, description="Only centres currently offering this test"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    def build():
        return _list_centres(db, location, test_id, limit, offset)

    key_location = (location or "").strip().lower()
    return _cached(f"centres:{key_location}:{test_id}:{limit}:{offset}", build)


def _list_centres(db, location, test_id, limit, offset) -> Page[CentreOut]:
    query = select(DiagnosticCentre)
    if location:
        query = query.where(
            DiagnosticCentre.location.ilike(f"%{_escape_like(location.strip())}%", escape="\\")
        )
    if test_id is not None:
        query = query.join(CentreTest).where(CentreTest.test_id == test_id, CentreTest.is_active)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(DiagnosticCentre.id).limit(limit).offset(offset)).all()
    return Page[CentreOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/centres/{centre_id}", response_model=CentreDetail)
def get_centre(centre_id: int, db: Session = Depends(get_db)):
    return _cached(f"centre:{centre_id}", lambda: _centre_detail(db, centre_id))


def _centre_detail(db: Session, centre_id: int) -> CentreDetail:
    centre = db.scalar(
        select(DiagnosticCentre)
        .where(DiagnosticCentre.id == centre_id)
        .options(selectinload(DiagnosticCentre.offerings).joinedload(CentreTest.test))
    )
    if centre is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Centre not found")

    # public view only shows tests that can be booked right now
    tests = [_offering_out(o) for o in centre.offerings if o.is_active]
    tests.sort(key=lambda t: t.test_name)
    return CentreDetail(id=centre.id, name=centre.name, location=centre.location, tests=tests)


@router.post("/centres/", response_model=CentreOut, status_code=status.HTTP_201_CREATED)
def create_centre(data: CentreCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    centre = DiagnosticCentre(**data.model_dump())
    db.add(centre)
    _commit_or_409(db, "A centre with this name already exists at this location")
    return centre


@router.patch("/centres/{centre_id}", response_model=CentreOut)
def update_centre(
    centre_id: int, data: CentreUpdate, db: Session = Depends(get_db), _=Depends(require_admin)
):
    centre = _get_centre_or_404(db, centre_id)
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(centre, field, value)
    _commit_or_409(db, "A centre with this name already exists at this location")
    return centre


# ---------- tests offered at a centre ----------

@router.post(
    "/centres/{centre_id}/tests",
    response_model=OfferingOut,
    status_code=status.HTTP_201_CREATED,
)
def add_offering(
    centre_id: int, data: OfferingCreate, db: Session = Depends(get_db), _=Depends(require_admin)
):
    _get_centre_or_404(db, centre_id)
    test = db.get(DiagnosticTest, data.test_id)
    if test is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test not found")

    offering = CentreTest(centre_id=centre_id, test_id=test.id, price=data.price)
    db.add(offering)
    _commit_or_409(db, "This centre already offers this test")
    return _offering_out(offering)


@router.patch("/centres/{centre_id}/tests/{test_id}", response_model=OfferingOut)
def update_offering(
    centre_id: int,
    test_id: int,
    data: OfferingUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    offering = db.get(CentreTest, (centre_id, test_id))
    if offering is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This centre does not offer this test")

    # existing bookings keep their own amount, so changing the price here is safe
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(offering, field, value)
    db.commit()
    cache.invalidate_catalog()
    db.refresh(offering)
    return _offering_out(offering)

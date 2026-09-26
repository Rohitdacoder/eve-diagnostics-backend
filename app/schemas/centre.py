from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import Name

Location = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Price = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]


class TestCreate(BaseModel):
    name: Name
    description: str | None = Field(default=None, max_length=2000)


class TestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None


class CentreCreate(BaseModel):
    name: Name
    location: Location


class CentreUpdate(BaseModel):
    name: Name | None = None
    location: Location | None = None


class CentreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    location: str


class OfferingCreate(BaseModel):
    test_id: int
    price: Price


class OfferingUpdate(BaseModel):
    price: Price | None = None
    is_active: bool | None = None


class OfferingOut(BaseModel):
    test_id: int
    test_name: str
    price: Decimal
    is_active: bool


class CentreDetail(CentreOut):
    tests: list[OfferingOut]

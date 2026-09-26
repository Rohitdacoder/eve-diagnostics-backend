from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, StringConstraints

T = TypeVar("T")

# trims spaces first, then checks it isn't empty
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

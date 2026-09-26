from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class DiagnosticCentre(TimestampMixin, Base):
    __tablename__ = "diagnostic_centres"
    __table_args__ = (UniqueConstraint("name", "location"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(255), index=True)

    offerings: Mapped[list["CentreTest"]] = relationship(
        back_populates="centre", cascade="all, delete-orphan"
    )


class DiagnosticTest(TimestampMixin, Base):
    __tablename__ = "diagnostic_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class CentreTest(TimestampMixin, Base):
    """A test offered at a specific centre. Price lives here since it varies by centre."""

    __tablename__ = "centre_tests"
    __table_args__ = (CheckConstraint("price > 0", name="price_positive"),)

    centre_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_centres.id", ondelete="CASCADE"), primary_key=True
    )
    test_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_tests.id", ondelete="RESTRICT"), primary_key=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    centre: Mapped[DiagnosticCentre] = relationship(back_populates="offerings")
    test: Mapped[DiagnosticTest] = relationship()

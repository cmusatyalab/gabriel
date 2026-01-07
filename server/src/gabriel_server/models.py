"""Model classes for the database."""

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for model."""

    pass


class EngineResult(Base):
    """Results from engines."""

    __tablename__ = "engine_results"

    id: Mapped[int] = mapped_column(primary_key=True)

    engine_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    frame_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    bucket: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    object_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    content_type: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_engine_results_engine_frame", "engine_id", "frame_id"),
    )

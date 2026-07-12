from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import JSON, DateTime, Engine, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from freqtrade.markets import CatalogSnapshot


class CatalogRepository(Protocol):
    def current(self) -> CatalogSnapshot: ...


class StaticCatalogRepository:
    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot = snapshot

    def current(self) -> CatalogSnapshot:
        return self._snapshot


class PlatformBase(DeclarativeBase):
    pass


class CatalogRevisionRecord(PlatformBase):
    __tablename__ = "platform_catalog_revisions"

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class SqlCatalogRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def initialize_schema(self) -> None:
        PlatformBase.metadata.create_all(
            self._engine,
            tables=[CatalogRevisionRecord.__table__],
        )

    def publish(self, snapshot: CatalogSnapshot, *, created_at: datetime) -> None:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        normalized_created_at = created_at.astimezone(UTC)
        with Session(self._engine) as session:
            if session.get(CatalogRevisionRecord, snapshot.revision_id) is not None:
                raise ValueError("catalog revision already exists")
            session.add(
                CatalogRevisionRecord(
                    revision_id=snapshot.revision_id,
                    payload=snapshot.model_dump(mode="json"),
                    created_at=normalized_created_at,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if session.get(CatalogRevisionRecord, snapshot.revision_id) is not None:
                    raise ValueError("catalog revision already exists") from None
                raise

    def current(self) -> CatalogSnapshot:
        with Session(self._engine) as session:
            record = session.scalar(
                select(CatalogRevisionRecord).order_by(
                    CatalogRevisionRecord.created_at.desc(),
                    CatalogRevisionRecord.revision_id.desc(),
                )
            )
            if record is None:
                raise LookupError("market catalog is not initialized")
            return CatalogSnapshot.model_validate(record.payload)

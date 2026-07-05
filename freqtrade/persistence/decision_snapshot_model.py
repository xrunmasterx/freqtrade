import json
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from freqtrade.persistence.base import ModelBase, SessionType
from freqtrade.util import dt_now


class DecisionSnapshot(ModelBase):
    """
    Decision-time evidence captured for chart and RPC explanations.
    """

    __tablename__ = "decision_snapshots"
    session: ClassVar[SessionType]

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    pair: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(25), nullable=False, index=True)
    candle_open: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    decision_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    strategy: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_type: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    _values: Mapped[str] = mapped_column("values", Text, nullable=False, default="{}")
    _context: Mapped[str] = mapped_column("context", Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=dt_now)

    def __init__(
        self,
        *args: Any,
        values: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if values is not None:
            self.values = values
        if context is not None:
            self.context = context

    @property
    def values(self) -> dict[str, Any]:
        return json.loads(self._values or "{}")

    @values.setter
    def values(self, value: dict[str, Any]) -> None:
        self._values = json.dumps(value)

    @property
    def context(self) -> dict[str, Any]:
        return json.loads(self._context or "{}")

    @context.setter
    def context(self, value: dict[str, Any]) -> None:
        self._context = json.dumps(value)

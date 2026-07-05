from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pandas import DataFrame

from freqtrade.rpc.api_server.api_schemas import (
    ChartLayerMeta,
    ChartOverlayMeta,
    ChartResponseMeta,
)


@dataclass(frozen=True)
class ChartFrame:
    dataframe: DataFrame
    pair: str
    timeframe: str
    requested_count: int
    warmup_count: int
    last_candle_complete: bool


@dataclass(frozen=True)
class ChartLayer:
    id: str
    source: str
    label: str
    dataframe: DataFrame
    plot_config: dict[str, Any] = field(default_factory=dict)
    meta: ChartLayerMeta | None = None


@dataclass(frozen=True)
class ChartComposition:
    frame: ChartFrame
    layers: list[ChartLayer]
    strategy_timeframe: str | None
    overlay: ChartOverlayMeta | None
    candle_mode: str
    plot_config: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    meta: ChartResponseMeta | None = None

    def legacy_update(self) -> dict[str, Any]:
        return {
            "plot_config": self.plot_config,
            "warnings": self.warnings,
            "meta": self.meta.model_dump() if self.meta else None,
            "last_candle_complete": self.frame.last_candle_complete,
        }

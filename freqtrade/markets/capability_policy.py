import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import Field, field_serializer, field_validator, model_validator

from freqtrade.markets.catalog import CatalogModel, ProductType
from freqtrade.markets.instrument import MarketType


_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class CapabilityName(StrEnum):
    MARKET_DATA = "market_data"
    RESEARCH = "research"
    BACKTEST = "backtest"
    SIMULATION = "simulation"
    PAPER_TRADING = "paper_trading"
    LIVE_TRADING = "live_trading"
    SHORT = "short"
    LEVERAGE = "leverage"
    OPTIONS_CHAIN = "options_chain"
    OPTIONS_BACKTEST = "options_backtest"
    OPTIONS_EXECUTION = "options_execution"
    MANUAL_ORDER = "manual_order"
    AI_ORDER_INTENT = "ai_order_intent"


class CapabilityDecision(CatalogModel):
    allowed: bool
    reason_code: str | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> "CapabilityDecision":
        if self.allowed and self.reason_code is not None:
            raise ValueError("allowed capability cannot have a denial reason")
        if not self.allowed and not self.reason_code:
            raise ValueError("denied capability requires a reason code")
        if (
            self.reason_code is not None
            and _REASON_CODE_PATTERN.fullmatch(self.reason_code) is None
        ):
            raise ValueError("reason code must match ^[a-z][a-z0-9_]*$")
        return self

    @classmethod
    def allow(cls) -> "CapabilityDecision":
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason_code: str) -> "CapabilityDecision":
        return cls(allowed=False, reason_code=reason_code)


class ProductCapabilityPolicy(CatalogModel):
    market_id: MarketType
    product_id: ProductType
    decisions: Mapping[CapabilityName, CapabilityDecision] = Field(default_factory=dict)

    @field_validator("decisions", mode="after")
    @classmethod
    def freeze_decisions(
        cls,
        decisions: Mapping[CapabilityName, CapabilityDecision],
    ) -> Mapping[CapabilityName, CapabilityDecision]:
        return MappingProxyType(dict(decisions))

    @field_serializer("decisions")
    def serialize_decisions(
        self,
        decisions: Mapping[CapabilityName, CapabilityDecision],
    ) -> dict[CapabilityName, CapabilityDecision]:
        return dict(decisions)

    def decision(self, capability: CapabilityName) -> CapabilityDecision:
        return self.decisions.get(
            capability,
            CapabilityDecision.deny("capability_not_declared"),
        )

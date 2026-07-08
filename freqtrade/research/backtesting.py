import math
from typing import Any

import pandas as pd
from pandas import DataFrame
from pydantic import BaseModel, ConfigDict, Field, model_validator

from freqtrade.markets import AShareMarketRules, AShareStatusStore, CachedAShareCalendar
from freqtrade.research.a_share_sessions import is_a_share_regular_session_timestamp
from freqtrade.research.feature_context import (
    ResearchFeatureContext,
    ResearchFeatureFilterConfig,
)
from freqtrade.research.strategies import add_sma_cross_signals, apply_feature_filter


class ResearchBacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_cash: float = Field(gt=0, allow_inf_nan=False)
    fast: int = Field(default=20, ge=1)
    slow: int = Field(default=60, ge=2)

    @model_validator(mode="after")
    def validate_period_order(self):
        if self.fast >= self.slow:
            raise ValueError("SMA fast period must be less than slow period")
        return self


class ResearchBacktestResult(BaseModel):
    instrument: str
    strategy: str = "sma_cross"
    capability: dict[str, str] = Field(
        default_factory=lambda: {"kind": "research_backtest", "execution": "none"}
    )
    data_provenance: dict[str, Any] | None = None
    metrics: dict[str, Any]
    trades: list[dict[str, Any]] = Field(default_factory=list)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResearchMarketContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    calendar: CachedAShareCalendar | None = None
    status_store: AShareStatusStore | None = None


def run_research_backtest(
    instrument: str,
    dataframe: DataFrame,
    config: ResearchBacktestConfig,
    market_context: ResearchMarketContext | None = None,
    feature_context: ResearchFeatureContext | None = None,
    feature_filter: ResearchFeatureFilterConfig | None = None,
) -> ResearchBacktestResult:
    _validate_backtest_prices(dataframe)
    dataframe = _with_strategy_signals(dataframe, config)
    warnings: list[str] = []
    strategy = "sma_cross"
    if feature_filter is not None:
        if feature_context is None:
            raise ValueError("Feature filter requires ResearchFeatureContext")
        dataframe, feature_warnings = apply_feature_filter(
            dataframe,
            feature_context,
            feature_filter,
        )
        warnings.extend(feature_context.warnings)
        warnings.extend(feature_warnings)
        strategy = "sma_cross_feature_filter"
    market_rules = AShareMarketRules()
    cash = float(config.initial_cash)
    shares = 0
    entry: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    rows = list(dataframe.itertuples(index=False))
    signals = _build_signal_records(rows)

    for index, row in enumerate(rows):
        row_date = row.date
        open_price = float(row.open)
        close_price = float(row.close)

        if index > 0:
            signal_row = rows[index - 1]
            if (
                int(signal_row.exit_long)
                and shares > 0
                and entry is not None
                and _can_execute_on_row(row_date, market_context, warnings)
            ):
                if not market_rules.can_sell(
                    entry["date"],
                    row_date,
                    calendar=market_context.calendar if market_context else None,
                ):
                    warnings.append(f"Blocked sell fill by T+1 on {_date_string(row_date)}")
                else:
                    status = _status_for_row(instrument, row_date, market_context)
                    if not market_rules.can_fill_order("sell", open_price, status):
                        warnings.append(f"Blocked sell fill on {_date_string(row_date)}")
                    else:
                        trade_value = shares * open_price
                        commission, stamp_tax = market_rules.exit_fee(trade_value)
                        cash += trade_value - commission - stamp_tax
                        total_commission = entry["commission"] + commission
                        cost_basis = entry["value"] + entry["commission"]
                        profit_abs = trade_value - commission - stamp_tax - cost_basis
                        trades.append(
                            {
                                "instrument": instrument,
                                "entry_date": _date_string(entry["date"]),
                                "exit_date": _date_string(row_date),
                                "entry_price": entry["price"],
                                "exit_price": open_price,
                                "shares": shares,
                                "entry_value": entry["value"],
                                "exit_value": trade_value,
                                "commission": total_commission,
                                "stamp_tax": stamp_tax,
                                "profit_abs": profit_abs,
                                "profit_ratio": profit_abs / cost_basis,
                            }
                        )
                        shares = 0
                        entry = None

            if (
                int(signal_row.enter_long)
                and shares == 0
                and _can_execute_on_row(row_date, market_context, warnings)
            ):
                status = _status_for_row(instrument, row_date, market_context)
                if not market_rules.can_fill_order("buy", open_price, status):
                    warnings.append(f"Blocked buy fill on {_date_string(row_date)}")
                else:
                    shares_to_buy = market_rules.whole_lot_shares(cash, open_price)
                    if shares_to_buy > 0:
                        trade_value = shares_to_buy * open_price
                        commission = market_rules.entry_fee(trade_value)
                        cash -= trade_value + commission
                        shares = shares_to_buy
                        entry = {
                            "date": row_date,
                            "price": open_price,
                            "shares": shares_to_buy,
                            "value": trade_value,
                            "commission": commission,
                        }

        equity_curve.append(
            {
                "date": _date_string(row_date),
                "cash": cash,
                "shares": shares,
                "close": close_price,
                "equity": cash + shares * close_price,
            }
        )

    final_equity = equity_curve[-1]["equity"] if equity_curve else cash
    return_ratio = final_equity / config.initial_cash - 1
    return ResearchBacktestResult(
        instrument=instrument,
        strategy=strategy,
        metrics={
            "initial_cash": config.initial_cash,
            "final_equity": final_equity,
            "return_ratio": return_ratio,
            "total_return": return_ratio,
            "trade_count": len(trades),
            "position_shares": shares,
            "cash": cash,
        },
        trades=trades,
        equity_curve=equity_curve,
        signals=signals,
        warnings=warnings,
    )


def _can_execute_on_row(
    row_date: Any,
    market_context: ResearchMarketContext | None,
    warnings: list[str],
) -> bool:
    if _is_intraday_timestamp(row_date) and not is_a_share_regular_session_timestamp(row_date):
        warnings.append(f"Blocked fill outside A-share session on {_date_string(row_date)}")
        return False

    if market_context is None or market_context.calendar is None:
        return True
    if market_context.calendar.is_trading_day(row_date):
        return True
    warnings.append(f"Blocked fill on non-trading day {_date_string(row_date)}")
    return False


def _status_for_row(
    instrument: str,
    row_date: Any,
    market_context: ResearchMarketContext | None,
):
    if market_context is None or market_context.status_store is None:
        return None
    return market_context.status_store.get_status(instrument, row_date)


def _validate_backtest_prices(dataframe: DataFrame) -> None:
    for column in ("open", "close"):
        prices = pd.to_numeric(dataframe[column], errors="raise")
        for value in prices:
            price = float(value)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("Invalid OHLCV price")


def _build_signal_records(rows: list[Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        execution_row = rows[index + 1] if index + 1 < len(rows) else None
        if int(row.enter_long):
            signals.append(_signal_record(row, execution_row, "enter_long"))
        if int(row.exit_long):
            signals.append(_signal_record(row, execution_row, "exit_long"))
    return signals


def _with_strategy_signals(
    dataframe: DataFrame,
    config: ResearchBacktestConfig,
) -> DataFrame:
    return add_sma_cross_signals(dataframe, config.fast, config.slow)


def _signal_record(
    signal_row: Any,
    execution_row: Any | None,
    signal_type: str,
) -> dict[str, Any]:
    record = {
        "date": _date_string(signal_row.date),
        "type": signal_type,
        "price": float(signal_row.close),
    }
    if execution_row is not None:
        record.update(
            {
                "execution_date": _date_string(execution_row.date),
                "execution_price": float(execution_row.open),
            }
        )
    return record


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))


def _is_intraday_timestamp(value: Any) -> bool:
    timestamp = pd.to_datetime(value, utc=True)
    return any(
        (
            timestamp.hour != 0,
            timestamp.minute != 0,
            timestamp.second != 0,
            timestamp.microsecond != 0,
        )
    )

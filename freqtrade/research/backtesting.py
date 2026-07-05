import math
from typing import Any

import pandas as pd
from pandas import DataFrame
from pydantic import BaseModel, Field, model_validator

from freqtrade.research.strategies import add_sma_cross_signals


class ResearchBacktestConfig(BaseModel):
    initial_cash: float = Field(gt=0, allow_inf_nan=False)
    fast: int = Field(default=20, ge=1)
    slow: int = Field(default=60, ge=2)
    lot_size: int = Field(default=100, ge=1)
    commission_rate: float = Field(default=0.0003, ge=0)
    stamp_tax_rate: float = Field(default=0.001, ge=0)

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
    metrics: dict[str, Any]
    trades: list[dict[str, Any]] = Field(default_factory=list)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def run_research_backtest(
    instrument: str,
    dataframe: DataFrame,
    config: ResearchBacktestConfig,
) -> ResearchBacktestResult:
    _validate_backtest_prices(dataframe)
    dataframe = add_sma_cross_signals(dataframe, config.fast, config.slow)
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
            if int(signal_row.exit_long) and shares > 0 and entry is not None:
                trade_value = shares * open_price
                commission = trade_value * config.commission_rate
                stamp_tax = trade_value * config.stamp_tax_rate
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

            if int(signal_row.enter_long) and shares == 0:
                shares_to_buy = _whole_lot_shares(
                    cash,
                    open_price,
                    config.lot_size,
                    config.commission_rate,
                )
                if shares_to_buy > 0:
                    trade_value = shares_to_buy * open_price
                    commission = trade_value * config.commission_rate
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
    )


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


def _whole_lot_shares(
    cash: float,
    price: float,
    lot_size: int,
    commission_rate: float,
) -> int:
    lot_cost = price * lot_size * (1 + commission_rate)
    return int(cash // lot_cost) * lot_size


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))

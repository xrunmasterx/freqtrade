from typing import Any

import pandas as pd
from pandas import DataFrame
from pydantic import BaseModel, Field, model_validator

from freqtrade.research.strategies import add_sma_cross_signals


class ResearchBacktestConfig(BaseModel):
    initial_cash: float = Field(gt=0)
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
    dataframe = add_sma_cross_signals(dataframe, config.fast, config.slow)
    cash = float(config.initial_cash)
    shares = 0
    entry: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []

    for row in dataframe.itertuples(index=False):
        row_date = row.date
        open_price = float(row.open)
        close_price = float(row.close)
        if open_price <= 0 or close_price <= 0:
            raise ValueError("Invalid OHLCV price")

        enter_long = int(row.enter_long)
        exit_long = int(row.exit_long)

        if enter_long:
            signals.append(
                {
                    "date": _date_string(row_date),
                    "type": "enter_long",
                    "price": open_price,
                }
            )
            if shares == 0:
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

        if exit_long:
            signals.append(
                {
                    "date": _date_string(row_date),
                    "type": "exit_long",
                    "price": open_price,
                }
            )
            if shares > 0 and entry is not None and not _same_session(entry["date"], row_date):
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


def _whole_lot_shares(
    cash: float,
    price: float,
    lot_size: int,
    commission_rate: float,
) -> int:
    lot_cost = price * lot_size * (1 + commission_rate)
    return int(cash // lot_cost) * lot_size


def _same_session(left: Any, right: Any) -> bool:
    return pd.to_datetime(left, utc=True).date() == pd.to_datetime(right, utc=True).date()


def _date_string(value: Any) -> str:
    return str(pd.to_datetime(value, utc=True))

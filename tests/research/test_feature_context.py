import json

import pandas as pd
import pytest

from freqtrade.research.exceptions import ResearchConfigError
from freqtrade.research.feature_context import create_research_feature_context
from freqtrade.research.market_context import create_research_market_context
from freqtrade.research.profiles import load_research_profiles


def _write_calendar(root) -> None:
    (root / "calendar").mkdir(parents=True)
    (root / "calendar" / "trade_dates.csv").write_text(
        "date,is_open,source\n"
        "2026-07-07,1,test\n"
        "2026-07-08,1,test\n"
        "2026-07-09,1,test\n",
        encoding="utf-8",
    )


def _write_feature(root, *, publish_time: str = "2026-07-07T15:30:00+08:00") -> None:
    (root / "features" / "fund_flow_daily").mkdir(parents=True)
    (root / "features" / "fund_flow_daily" / "600519.SH.csv").write_text(
        "date,instrument,main_net_inflow,large_net_inflow,medium_net_inflow,"
        "small_net_inflow,source,publish_time,ingest_time\n"
        f"2026-07-07,600519.SH,1000,800,100,100,eastmoney,{publish_time},"
        "2026-07-07T16:00:00+08:00\n",
        encoding="utf-8",
    )


def _write_feature_manifest(root, *, warnings: list[str] | None = None) -> None:
    (root / ".manifests").mkdir(parents=True)
    (root / ".manifests" / "fund-flow.json").write_text(
        json.dumps(
            {
                "run_id": "phase3b-fixture",
                "provider": "akshare",
                "provider_version": "1.17.0",
                "created_at": "2026-07-07T20:30:00+08:00",
                "files": [
                    {
                        "path": "features/fund_flow_daily/600519.SH.csv",
                        "dataset": "fund_flow_daily",
                        "kind": "feature",
                        "rows": 1,
                        "start": "2026-07-07",
                        "stop": "2026-07-07",
                        "status": "ok",
                        "warnings": warnings or ["fixture warning"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _profile(tmp_path, *, side_data: bool = True, market_data: bool = True):
    meta_root = tmp_path / "research_data" / "a_share_meta"
    _write_calendar(meta_root)
    _write_feature(meta_root)
    _write_feature_manifest(meta_root)
    profile = {
        "id": "a-share-local",
        "label": "A Share Local",
        "market": "a_share",
        "data_source": {"type": "local_csv", "root": "research_data/a_share"},
    }
    if market_data:
        profile["market_data"] = {"meta_root": "research_data/a_share_meta"}
    if side_data:
        profile["side_data"] = {
            "root": "research_data/a_share_meta",
            "enabled_datasets": ["fund_flow_daily"],
        }
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [profile],
    }
    return load_research_profiles(config)[0]


def _candle_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2026-07-07", "2026-07-08", "2026-07-09"],
                utc=True,
            ),
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [1000.0, 1100.0, 1200.0],
        }
    )


def test_create_research_feature_context_aligns_post_close_feature_to_next_candle(
    tmp_path,
) -> None:
    profile = _profile(tmp_path)
    market_context = create_research_market_context(profile)

    context = create_research_feature_context(
        profile,
        "600519.SH",
        ["fund_flow_daily"],
        _candle_frame(),
        market_context,
    )

    assert context.instrument == "600519.SH"
    assert context.datasets == ["fund_flow_daily"]
    assert list(context.frame["date"]) == list(_candle_frame()["date"])
    column = "feature_fund_flow_daily_main_net_inflow"
    assert pd.isna(context.frame.loc[0, column])
    assert context.frame.loc[1, column] == 1000.0
    assert pd.isna(context.frame.loc[2, column])


def test_create_research_feature_context_returns_feature_provenance(tmp_path) -> None:
    profile = _profile(tmp_path)
    market_context = create_research_market_context(profile)

    context = create_research_feature_context(
        profile,
        "600519.SH",
        ["fund_flow_daily"],
        _candle_frame(),
        market_context,
    )

    provenance = context.provenance["fund_flow_daily"]
    assert provenance["provider"] == "akshare"
    assert provenance["provider_version"] == "1.17.0"
    assert provenance["manifest_run_id"] == "phase3b-fixture"
    assert provenance["start"] == "2026-07-07"
    assert provenance["stop"] == "2026-07-07"
    assert provenance["warnings"] == ["fixture warning"]


def test_create_research_feature_context_propagates_missing_feature_artifact(
    tmp_path,
) -> None:
    profile = _profile(tmp_path)
    market_context = create_research_market_context(profile)
    feature_path = (
        tmp_path
        / "research_data"
        / "a_share_meta"
        / "features"
        / "fund_flow_daily"
        / "600519.SH.csv"
    )
    feature_path.unlink()

    with pytest.raises(FileNotFoundError):
        create_research_feature_context(
            profile,
            "600519.SH",
            ["fund_flow_daily"],
            _candle_frame(),
            market_context,
        )


def test_create_research_feature_context_aggregates_provenance_warnings(
    tmp_path,
) -> None:
    meta_root = tmp_path / "research_data" / "a_share_meta"
    _write_calendar(meta_root)
    _write_feature(meta_root)
    _write_feature_manifest(meta_root, warnings=["fixture warning", "late publish"])
    profile = load_research_profiles(
        {
            "user_data_dir": tmp_path,
            "research_bots": [
                {
                    "id": "a-share-local",
                    "label": "A Share Local",
                    "market": "a_share",
                    "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                    "market_data": {"meta_root": "research_data/a_share_meta"},
                    "side_data": {
                        "root": "research_data/a_share_meta",
                        "enabled_datasets": ["fund_flow_daily"],
                    },
                }
            ],
        }
    )[0]
    market_context = create_research_market_context(profile)

    context = create_research_feature_context(
        profile,
        "600519.SH",
        ["fund_flow_daily"],
        _candle_frame(),
        market_context,
    )

    assert context.warnings == ["fixture warning", "late publish"]


def test_create_research_feature_context_requires_side_data_config(tmp_path) -> None:
    profile = _profile(tmp_path, side_data=False)
    market_context = create_research_market_context(profile)

    with pytest.raises(
        ResearchConfigError,
        match=r"Feature-aware research backtest requires side_data config\.",
    ):
        create_research_feature_context(
            profile,
            "600519.SH",
            ["fund_flow_daily"],
            _candle_frame(),
            market_context,
        )


def test_create_research_feature_context_requires_market_calendar(tmp_path) -> None:
    profile = _profile(tmp_path, market_data=False)

    with pytest.raises(
        ResearchConfigError,
        match=r"Feature-aware research backtest requires market_data calendar\.",
    ):
        create_research_feature_context(
            profile,
            "600519.SH",
            ["fund_flow_daily"],
            _candle_frame(),
            None,
        )


def test_create_research_feature_context_rejects_incompatible_dataset_kind(
    tmp_path,
) -> None:
    profile = _profile(tmp_path)
    market_context = create_research_market_context(profile)

    with pytest.raises(ValueError, match=r"Unknown research side dataset: announcements"):
        create_research_feature_context(
            profile,
            "600519.SH",
            ["announcements"],
            _candle_frame(),
            market_context,
        )

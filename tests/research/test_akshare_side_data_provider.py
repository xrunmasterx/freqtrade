import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from freqtrade.markets import CachedAShareCalendar
from freqtrade.research.side_data.providers.akshare_side_data import (
    AkshareAshareSideDataProvider,
)


VALID_CALENDAR_CSV = "date,is_open,source\n2026-07-07,1,test\n2026-07-08,1,test\n"


class FakeAkshare:
    def stock_individual_fund_flow(self, stock, market):
        assert stock == "600519"
        assert market == "sh"
        return pd.DataFrame(
            {
                "日期": ["2026-07-07"],
                "主力净流入-净额": [1000.0],
                "大单净流入-净额": [800.0],
                "中单净流入-净额": [100.0],
                "小单净流入-净额": [100.0],
            }
        )
    def stock_zt_pool_em(self, date):
        assert date == "20260707"
        return pd.DataFrame(
            {
                "代码": ["600519"],
                "名称": ["贵州茅台"],
                "涨停统计": ["1/1"],
                "封板资金": [123.0],
                "首次封板时间": ["09:35:00"],
                "最后封板时间": ["14:50:00"],
                "所属行业": ["白酒"],
            }
        )

    def stock_individual_notice_report(
        self,
        security,
        symbol="全部",
        begin_date=None,
        end_date=None,
    ):
        assert security == "600519"
        assert symbol == "全部"
        return pd.DataFrame(
            {
                "公告标题": ["Announcement"],
                "公告类型": ["重大事项"],
                "公告日期": ["2026-07-07"],
                "网址": ["https://example.invalid/a.pdf"],
            }
        )


class FailingSideDataProvider:
    provider_name = "fake"
    provider_version = "1.0"

    def fetch_fund_flow_daily(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> pd.DataFrame:
        raise RuntimeError("provider exploded")

    def fetch_limit_pool(self, trade_date: str) -> list[dict]:
        raise AssertionError("unexpected limit_pool call")

    def fetch_announcements(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict]:
        raise AssertionError("unexpected announcements call")


@pytest.fixture
def calendar() -> CachedAShareCalendar:
    return CachedAShareCalendar(
        open_dates={
            pd.Timestamp("2026-07-07").date(),
            pd.Timestamp("2026-07-08").date(),
        },
        known_dates={
            pd.Timestamp("2026-07-07").date(),
            pd.Timestamp("2026-07-08").date(),
        },
    )


def test_akshare_provider_import_does_not_import_akshare() -> None:
    sys.modules.pop("akshare", None)

    importlib.reload(
        sys.modules["freqtrade.research.side_data.providers.akshare_side_data"]
    )

    assert "akshare" not in sys.modules


def test_side_data_cli_help_does_not_import_live_provider_deps() -> None:
    script = Path(__file__).resolve().parents[3] / "tools" / "download_a_share_side_data.py"
    command = [
        sys.executable,
        "-c",
        (
            "import json, runpy, sys; "
            f"sys.argv = [{str(script)!r}, '--help']; "
            "code = 0\n"
            "try:\n"
            f"    runpy.run_path({str(script)!r}, run_name='__main__')\n"
            "except SystemExit as exc:\n"
            "    code = int(exc.code or 0)\n"
            "print(json.dumps({"
            "'code': code, "
            "'akshare': 'akshare' in sys.modules, "
            "'provider': "
            "'freqtrade.research.side_data.providers.akshare_side_data' in sys.modules"
            "}))"
        ),
    ]

    result = subprocess.run(command, check=True, capture_output=True, text=True)
    status = json.loads(result.stdout.splitlines()[-1])

    assert status == {"code": 0, "akshare": False, "provider": False}


def test_side_data_cli_reports_malformed_calendar(tmp_path, capsys) -> None:
    cli = _load_side_data_cli()
    config_path = _write_side_data_config(tmp_path, calendar_csv="bad\n1\n")

    code = cli.main(
        [
            "--config",
            str(config_path),
            "--bot-id",
            "a-share-research",
            "--datasets",
            "limit_pool",
            "--instruments",
            "600519.SH",
            "--timerange",
            "20260707-20260707",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "Missing A-share calendar columns" in captured.err
    assert captured.out == ""


def test_side_data_cli_reports_provider_failures_to_stderr(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    cli = _load_side_data_cli()
    config_path = _write_side_data_config(tmp_path)
    monkeypatch.setattr(
        cli,
        "_create_provider",
        lambda provider_name, calendar: FailingSideDataProvider(),
    )

    code = cli.main(
        [
            "--config",
            str(config_path),
            "--bot-id",
            "a-share-research",
            "--datasets",
            "fund_flow_daily",
            "--instruments",
            "600519.SH",
            "--timerange",
            "20260707-20260707",
        ]
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "A-share side-data collection failed for 1 artifact(s)." in captured.err
    assert "features/fund_flow_daily/600519.SH.csv: provider exploded" in captured.err
    payload = json.loads(captured.out)
    assert payload["failed"] == 1
    assert payload["files"][0]["error"] == "provider exploded"


def test_akshare_provider_normalizes_fund_flow(mocker, calendar) -> None:
    mocker.patch(
        "freqtrade.research.side_data.providers.akshare_side_data.import_module",
        return_value=FakeAkshare(),
    )

    frame = AkshareAshareSideDataProvider(calendar).fetch_fund_flow_daily(
        "600519.SH",
        "20260701",
        "20260707",
    )

    assert frame.iloc[0]["instrument"] == "600519.SH"
    assert frame.iloc[0]["main_net_inflow"] == 1000.0
    assert "ingest_time" in frame.columns


def test_akshare_provider_normalizes_limit_pool_with_calendar(mocker, calendar) -> None:
    mocker.patch(
        "freqtrade.research.side_data.providers.akshare_side_data.import_module",
        return_value=FakeAkshare(),
    )

    records = AkshareAshareSideDataProvider(calendar).fetch_limit_pool("2026-07-07")

    assert records[0]["dataset"] == "limit_pool"
    assert records[0]["instrument"] == "600519.SH"
    assert records[0]["event_type"] == "limit_up"
    assert records[0]["effective_candle_time"] == "2026-07-08 00:00:00+00:00"


def test_akshare_provider_normalizes_announcements_with_calendar(mocker, calendar) -> None:
    mocker.patch(
        "freqtrade.research.side_data.providers.akshare_side_data.import_module",
        return_value=FakeAkshare(),
    )

    records = AkshareAshareSideDataProvider(calendar).fetch_announcements(
        "600519.SH",
        "20260701",
        "20260707",
    )

    assert records[0]["dataset"] == "announcements"
    assert records[0]["document_type"] == "announcement"
    assert records[0]["title"] == "Announcement"
    assert records[0]["effective_candle_time"] == "2026-07-08 00:00:00+00:00"


def test_akshare_event_datasets_require_calendar() -> None:
    with pytest.raises(ValueError, match="requires a trading calendar"):
        AkshareAshareSideDataProvider().fetch_limit_pool("2026-07-07")


def _load_side_data_cli():
    script = Path(__file__).resolve().parents[3] / "tools" / "download_a_share_side_data.py"
    spec = importlib.util.spec_from_file_location("download_a_share_side_data_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_side_data_config(
    tmp_path: Path,
    *,
    calendar_csv: str = VALID_CALENDAR_CSV,
) -> Path:
    calendar_path = tmp_path / "research_data" / "a_share_meta" / "calendar" / "trade_dates.csv"
    calendar_path.parent.mkdir(parents=True)
    calendar_path.write_text(calendar_csv, encoding="utf-8")

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "user_data_dir": str(tmp_path),
                "research_bots": [
                    {
                        "id": "a-share-research",
                        "label": "A Share Research",
                        "market": "a_share",
                        "data_source": {
                            "type": "local_csv",
                            "root": "research_data/a_share",
                        },
                        "market_data": {
                            "meta_root": "research_data/a_share_meta",
                            "calendar": "calendar/trade_dates.csv",
                            "daily_status": "status/daily_status.csv",
                        },
                        "side_data": {
                            "root": "research_data/a_share_side",
                            "enabled_datasets": [
                                "fund_flow_daily",
                                "limit_pool",
                                "announcements",
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return config_path

import logging

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException

from freqtrade.research import (
    LocalCsvResearchDataSource,
    ResearchBotProfile,
    load_research_profiles,
)
from freqtrade.research.backtesting import ResearchBacktestConfig, run_research_backtest
from freqtrade.research.chart import build_research_chart_candles_response
from freqtrade.rpc.api_server.api_schemas import (
    ChartCandlesResponse,
    ResearchBacktestRequest,
    ResearchBotsResponse,
    ResearchChartCandlesRequest,
    ResearchInstrumentsResponse,
)
from freqtrade.rpc.api_server.deps import get_config


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/research/bots", response_model=ResearchBotsResponse)
def research_bots(config=Depends(get_config)):
    profiles = load_research_profiles(config)
    return {
        "bots": [
            {
                "id": profile.id,
                "label": profile.label,
                "market": profile.market,
                "capabilities": profile.capabilities,
            }
            for profile in profiles
        ]
    }


@router.get("/research/instruments", response_model=ResearchInstrumentsResponse)
def research_instruments(bot_id: str, config=Depends(get_config)):
    profile = _get_research_profile(config, bot_id)
    try:
        instruments = _get_local_csv_data_source(profile).list_instruments()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"instruments": [instrument.model_dump(mode="json") for instrument in instruments]}


@router.post("/research/chart_candles", response_model=ChartCandlesResponse)
def research_chart_candles(
    payload: ResearchChartCandlesRequest,
    config=Depends(get_config),
):
    profile = _get_research_profile(config, payload.bot_id)
    try:
        return build_research_chart_candles_response(profile, payload)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Research OHLCV not found for {payload.instrument} {payload.timeframe}",
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid research OHLCV request")
    except Exception:
        logger.error("Research chart data unavailable")
        raise HTTPException(status_code=502, detail="Research chart data unavailable")


@router.post("/research/backtest")
def research_backtest(
    payload: ResearchBacktestRequest,
    config=Depends(get_config),
):
    profile = _get_research_profile(config, payload.bot_id)
    try:
        dataframe = _get_local_csv_data_source(profile).load_ohlcv(
            payload.instrument,
            payload.timeframe,
        )
        backtest_config = ResearchBacktestConfig(
            initial_cash=payload.initial_cash,
            fast=payload.strategy.fast,
            slow=payload.strategy.slow,
        )
        result = run_research_backtest(payload.instrument, dataframe, backtest_config)
        return result.model_dump(mode="json")
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Research OHLCV not found for {payload.instrument} {payload.timeframe}",
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid research backtest request")
    except Exception:
        logger.error("Research backtest unavailable")
        raise HTTPException(status_code=502, detail="Research backtest unavailable")


def _get_research_profile(config: dict, bot_id: str) -> ResearchBotProfile:
    profiles = {profile.id: profile for profile in load_research_profiles(config)}
    if bot_id not in profiles:
        raise HTTPException(status_code=404, detail=f"Unknown research bot: {bot_id}")
    return profiles[bot_id]


def _get_local_csv_data_source(profile: ResearchBotProfile) -> LocalCsvResearchDataSource:
    if profile.data_source.type != "local_csv":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported research data source: {profile.data_source.type}",
        )
    return LocalCsvResearchDataSource(profile.data_root)

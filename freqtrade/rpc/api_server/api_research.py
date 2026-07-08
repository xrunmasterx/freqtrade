import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException

from freqtrade.research import (
    ResearchBotProfile,
    create_research_data_source,
    load_research_profiles,
)
from freqtrade.research.a_share_timeframes import is_a_share_minute_timeframe
from freqtrade.research.backtesting import ResearchBacktestConfig, run_research_backtest
from freqtrade.research.chart import build_research_chart_candles_response
from freqtrade.research.exceptions import ResearchConfigError, ResearchUnsupportedFeatureError
from freqtrade.research.feature_context import (
    ResearchFeatureContext,
    ResearchFeatureFilterConfig,
    create_research_feature_context,
)
from freqtrade.research.market_context import create_research_market_context
from freqtrade.research.side_data.store import LocalResearchSideDataStore
from freqtrade.research.windowing import apply_research_timerange
from freqtrade.rpc.api_server.api_schemas import (
    ChartCandlesResponse,
    ResearchBacktestRequest,
    ResearchBotsResponse,
    ResearchChartCandlesRequest,
    ResearchDatasetsResponse,
    ResearchInstrumentsResponse,
)
from freqtrade.rpc.api_server.deps import get_config


logger = logging.getLogger(__name__)

router = APIRouter()

MAX_RESEARCH_BACKTEST_ROWS = 5000


@router.get("/research/bots", response_model=ResearchBotsResponse)
def research_bots(config=Depends(get_config)):
    try:
        profiles = load_research_profiles(config)
    except ResearchConfigError as e:
        raise HTTPException(status_code=400, detail=_research_config_error_detail(str(e))) from e

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
        data_source = create_research_data_source(profile)
        instruments = data_source.list_instruments()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "instruments": [
            {
                **instrument.model_dump(mode="json"),
                "available_timeframes": data_source.available_timeframes(instrument.key),
            }
            for instrument in instruments
        ]
    }


@router.get("/research/datasets", response_model=ResearchDatasetsResponse)
def research_datasets(
    bot_id: str,
    instrument: str | None = None,
    kind: Literal["feature", "event", "document"] | None = None,
    config=Depends(get_config),
):
    profile = _get_research_profile(config, bot_id)
    try:
        store = _create_side_data_store(profile)
        if store is None:
            return {"datasets": []}
        return {
            "datasets": [
                descriptor.model_dump(mode="json")
                for descriptor in store.list_datasets(instrument_key=instrument, kind=kind)
            ]
        }
    except ResearchConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid research dataset request")


@router.post("/research/chart_candles", response_model=ChartCandlesResponse)
def research_chart_candles(
    payload: ResearchChartCandlesRequest,
    config=Depends(get_config),
):
    profile = _get_research_profile(config, payload.bot_id)
    try:
        _reject_unsupported_adjustment(payload.adjustment)
        return build_research_chart_candles_response(profile, payload)
    except ResearchUnsupportedFeatureError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ResearchConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Research OHLCV not found for {payload.instrument} {payload.timeframe}",
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid research OHLCV request")
    except Exception as e:
        logger.error(
            "Research chart data unavailable: error_type=%s bot_id=%s instrument=%s timeframe=%s",
            type(e).__name__,
            payload.bot_id,
            payload.instrument,
            payload.timeframe,
        )
        raise HTTPException(status_code=502, detail="Research chart data unavailable")


@router.post("/research/backtest")
def research_backtest(
    payload: dict[str, Any],
    config=Depends(get_config),
):
    try:
        request = ResearchBacktestRequest.model_validate(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid research backtest request")

    profile = _get_research_profile(config, request.bot_id)
    try:
        _reject_unsupported_adjustment(request.adjustment)
        data_source = create_research_data_source(profile)
        dataframe = data_source.load_ohlcv(
            request.instrument,
            request.timeframe,
            request.adjustment,
        )
        provenance = data_source.get_ohlcv_provenance(
            request.instrument,
            request.timeframe,
            request.adjustment,
        )
        dataframe = apply_research_timerange(dataframe, request.timerange)
        if len(dataframe) > MAX_RESEARCH_BACKTEST_ROWS:
            raise HTTPException(
                status_code=413,
                detail="Research backtest input exceeds 5000 rows.",
            )
        backtest_config = ResearchBacktestConfig(
            initial_cash=request.initial_cash,
            fast=request.strategy.fast,
            slow=request.strategy.slow,
        )
        market_context = create_research_market_context(profile)
        feature_context: ResearchFeatureContext | None = None
        feature_filter: ResearchFeatureFilterConfig | None = None
        if request.strategy.type == "sma_cross_feature_filter":
            if is_a_share_minute_timeframe(request.timeframe):
                raise ResearchUnsupportedFeatureError(
                    "Feature-aware research backtest supports 1d only."
                )
            feature_filter = ResearchFeatureFilterConfig(
                **request.strategy.feature_filter.model_dump()
            )
            try:
                feature_context = create_research_feature_context(
                    profile,
                    request.instrument,
                    [feature_filter.dataset],
                    dataframe,
                    market_context,
                )
            except FileNotFoundError as e:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Research side data not found for "
                        f"{request.instrument} {feature_filter.dataset}"
                    ),
                ) from e
        result = run_research_backtest(
            request.instrument,
            dataframe,
            backtest_config,
            market_context=market_context,
            feature_context=feature_context,
            feature_filter=feature_filter,
        )
        result.data_provenance = _merge_backtest_provenance(
            provenance.model_dump(),
            feature_context,
        )
        return result.model_dump(mode="json")
    except ResearchUnsupportedFeatureError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ResearchConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Research OHLCV not found for {request.instrument} {request.timeframe}",
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid research backtest request")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Research backtest unavailable: error_type=%s bot_id=%s instrument=%s timeframe=%s",
            type(e).__name__,
            request.bot_id,
            request.instrument,
            request.timeframe,
        )
        raise HTTPException(status_code=502, detail="Research backtest unavailable")


def _get_research_profile(config: dict, bot_id: str) -> ResearchBotProfile:
    try:
        profiles = {profile.id: profile for profile in load_research_profiles(config)}
    except ResearchConfigError as e:
        raise HTTPException(status_code=400, detail=_research_config_error_detail(str(e))) from e

    if bot_id not in profiles:
        raise HTTPException(status_code=404, detail=f"Unknown research bot: {bot_id}")
    return profiles[bot_id]


def _create_side_data_store(profile: ResearchBotProfile) -> LocalResearchSideDataStore | None:
    if profile.side_data is None or profile.side_data_root is None:
        return None
    return LocalResearchSideDataStore(
        profile.side_data_root,
        enabled_datasets=profile.side_data.enabled_datasets,
    )


def _merge_backtest_provenance(
    ohlcv_provenance: dict[str, Any],
    feature_context: ResearchFeatureContext | None,
) -> dict[str, Any]:
    if feature_context is None:
        return ohlcv_provenance
    return {
        **ohlcv_provenance,
        "features": feature_context.provenance,
    }


def _research_config_error_detail(message: str) -> dict[str, str]:
    return {"code": "invalid_research_config", "message": message}


def _reject_unsupported_adjustment(adjustment: str) -> None:
    if adjustment != "raw":
        raise ResearchUnsupportedFeatureError(
            f"Research adjustment {adjustment} is not supported yet."
        )

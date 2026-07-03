import logging

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException

from freqtrade.rpc import RPC
from freqtrade.rpc.api_server.api_schemas import ChartCandlesRequest, ChartCandlesResponse
from freqtrade.rpc.api_server.deps import get_config, get_rpc
from freqtrade.rpc.chart_data import build_chart_candles_response


logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chart_candles", response_model=ChartCandlesResponse, tags=["Candle data"])
def chart_candles(
    payload: ChartCandlesRequest, rpc: RPC = Depends(get_rpc), config=Depends(get_config)
):
    try:
        return build_chart_candles_response(rpc, config, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error in chart_candles")
        raise HTTPException(status_code=502, detail=str(e))

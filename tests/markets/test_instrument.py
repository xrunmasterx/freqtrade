import pytest

from freqtrade.markets import BotCapabilities, MarketType, parse_instrument_key


def test_parse_a_share_sse_instrument_key() -> None:
    instrument = parse_instrument_key("600519.SH", market=MarketType.A_SHARE)

    assert instrument.key == "600519.SH"
    assert instrument.market == MarketType.A_SHARE
    assert instrument.market.value == "a_share"
    assert instrument.venue == "SSE"
    assert instrument.symbol == "600519"
    assert instrument.currency == "CNY"
    assert instrument.asset_type == "equity"


def test_parse_a_share_szse_instrument_key() -> None:
    instrument = parse_instrument_key("000001.SZ", market=MarketType.A_SHARE)

    assert instrument.key == "000001.SZ"
    assert instrument.venue == "SZSE"
    assert instrument.symbol == "000001"


def test_parse_a_share_instrument_key_normalizes_suffix() -> None:
    instrument = parse_instrument_key("600519.sh", market=MarketType.A_SHARE)

    assert instrument.key == "600519.SH"
    assert instrument.venue == "SSE"


@pytest.mark.parametrize("key", ["600519", "600519.HK", "ABC123.SH", "60051.SH"])
def test_parse_a_share_invalid_key_raises_value_error(key: str) -> None:
    with pytest.raises(ValueError):
        parse_instrument_key(key, market=MarketType.A_SHARE)


def test_parse_unsupported_market_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_instrument_key("00700.HK", market=MarketType.HK_STOCK)


def test_research_bot_capabilities_disable_trading_surfaces() -> None:
    capabilities = BotCapabilities.research()

    assert capabilities.chart is True
    assert capabilities.indicators is True
    assert capabilities.backtest is True
    assert capabilities.live_trade is False
    assert capabilities.account is False
    assert capabilities.orders is False

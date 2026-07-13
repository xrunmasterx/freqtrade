from pandas import DataFrame

from freqtrade.configuration.timerange import TimeRange
from freqtrade.data.converter import trim_dataframe
from freqtrade.exceptions import ConfigurationError
from freqtrade.research.exceptions import ResearchConfigError


def apply_research_timerange(dataframe: DataFrame, timerange_text: str | None) -> DataFrame:
    if not timerange_text:
        return dataframe

    try:
        timerange = TimeRange.parse_timerange(timerange_text)
    except ConfigurationError:
        raise ResearchConfigError(f"Invalid research timerange: {timerange_text}") from None

    return trim_dataframe(dataframe, timerange, df_date_col="date").reset_index(drop=True)

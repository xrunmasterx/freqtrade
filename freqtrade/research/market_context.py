from pathlib import Path

from freqtrade.markets import AShareStatusStore, CachedAShareCalendar
from freqtrade.research.backtesting import ResearchMarketContext
from freqtrade.research.exceptions import ResearchConfigError
from freqtrade.research.profiles import ResearchBotProfile


def create_research_market_context(
    profile: ResearchBotProfile,
) -> ResearchMarketContext | None:
    if profile.market_data is None or profile.market_data_root is None:
        return None

    calendar_path = _resolve_market_data_path(
        profile.market_data_root,
        profile.market_data.calendar,
        "market_data.calendar",
    )
    status_path = _resolve_market_data_path(
        profile.market_data_root,
        profile.market_data.daily_status,
        "market_data.daily_status",
    )
    calendar = CachedAShareCalendar.from_csv(calendar_path) if calendar_path.is_file() else None
    status_store = AShareStatusStore.from_csv(status_path) if status_path.is_file() else None

    if calendar is None and status_store is None:
        return None

    return ResearchMarketContext(
        calendar=calendar,
        status_store=status_store,
    )


def _resolve_market_data_path(root: Path, configured_path: str, location: str) -> Path:
    candidate_path = Path(configured_path)
    if candidate_path.is_absolute():
        raise ResearchConfigError(f"Invalid {location}")

    resolved_root = root.resolve()
    resolved_path = (resolved_root / candidate_path).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ResearchConfigError(f"Invalid {location}")

    return resolved_path

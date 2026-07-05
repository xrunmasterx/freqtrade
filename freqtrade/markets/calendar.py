from datetime import datetime, time
from zoneinfo import ZoneInfo


_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MORNING_START = time(9, 30)
_MORNING_END = time(11, 30)
_AFTERNOON_START = time(13, 0)
_AFTERNOON_END = time(15, 0)


class AShareCalendar:
    def __init__(self, closed_dates: set[str] | None = None) -> None:
        self.closed_dates = closed_dates or set()

    def is_session_open(self, dt: datetime) -> bool:
        local_dt = dt.astimezone(_ASIA_SHANGHAI)

        if local_dt.weekday() >= 5 or local_dt.date().isoformat() in self.closed_dates:
            return False

        local_time = local_dt.time()
        return (
            _MORNING_START <= local_time < _MORNING_END
            or _AFTERNOON_START <= local_time < _AFTERNOON_END
        )

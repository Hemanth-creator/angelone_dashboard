"""
IST (Indian Standard Time) helpers.

NSE hours and Angel One's APIs are IST-based, so everything market-related
is anchored to IST regardless of the host machine's timezone.
Uses a fixed UTC+05:30 offset (India has no DST), so no tzdata package is needed.
"""

from datetime import datetime, time as dtime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

MARKET_OPEN_TIME = dtime(9, 15)
MARKET_CLOSE_TIME = dtime(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist_str() -> str:
    return now_ist().strftime("%Y-%m-%d")


def market_open_today() -> datetime:
    return now_ist().replace(hour=9, minute=15, second=0, microsecond=0)


def market_close_today() -> datetime:
    return now_ist().replace(hour=15, minute=30, second=0, microsecond=0)


def is_trading_day(dt: datetime | None = None) -> bool:
    """Mon-Fri. (Does not know about NSE exchange holidays.)"""
    dt = dt or now_ist()
    return dt.weekday() < 5


def is_market_hours(dt: datetime | None = None) -> bool:
    dt = dt or now_ist()
    return is_trading_day(dt) and MARKET_OPEN_TIME <= dt.time() <= MARKET_CLOSE_TIME
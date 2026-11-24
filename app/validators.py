"""
Data validation for train rows -- used by /add_train, /edit_train, /import,
and /import_auto_schedule so bad data (impossible times, out-of-range
platforms) is rejected with a clear message instead of silently corrupting
the schedule or crashing later during conflict detection.
"""

from datetime import timedelta, datetime

MIN_PLATFORM = 1
MAX_PLATFORM = 50


def _parse_time(value):
    """Parses 'HH:MM' or 'HH:MM:SS' into a timedelta, or None if invalid."""
    try:
        parts = str(value).strip().split(":")
        if len(parts) == 2:
            h, m = parts
            s = 0
        elif len(parts) == 3:
            h, m, s = parts
        else:
            return None
        h, m, s = int(h), int(m), int(s)
        if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
            return None
        return timedelta(hours=h, minutes=m, seconds=s)
    except (ValueError, TypeError):
        return None


def _parse_date(value):
    """Parses 'YYYY-MM-DD' into a date, or None if invalid."""
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def validate_train_row(row: dict, check_platform: bool = True):
    """
    Validates one train row dict before it is inserted/updated.
    Raises ValueError with a clear, user-facing message on the first
    problem found. Pass check_platform=False for rows where the platform
    hasn't been auto-assigned yet (e.g. Auto-Schedule Import, before
    auto_assign_platform runs).
    """
    if check_platform and "platform_number" in row and row["platform_number"] is not None:
        try:
            platform = int(row["platform_number"])
        except (TypeError, ValueError):
            raise ValueError("Platform number must be a whole number.")
        if platform < MIN_PLATFORM or platform > MAX_PLATFORM:
            raise ValueError(f"Platform number must be between {MIN_PLATFORM} and {MAX_PLATFORM}.")

    arrival = row.get("scheduled_arrival")
    departure = row.get("scheduled_departure")
    if arrival and departure:
        arr_td = _parse_time(arrival)
        dep_td = _parse_time(departure)
        if arr_td is None or dep_td is None:
            raise ValueError("Arrival and departure times must be a valid time (HH:MM or HH:MM:SS).")
        if dep_td < arr_td:
            raise ValueError("Departure time cannot be before arrival time at the same station.")

    travel_date = row.get("travel_date")
    if travel_date:
        if _parse_date(travel_date) is None:
            raise ValueError("Travel date must be a valid date (YYYY-MM-DD).")
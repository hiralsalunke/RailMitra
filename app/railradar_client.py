import os
import requests
from datetime import date, timedelta
from dotenv import load_dotenv
from app.database import get_train_live_tracking

load_dotenv()

RAILRADAR_BASE_URL = "https://api.railradar.in/v1"
RAILRADAR_API_KEY = os.getenv("RAILRADAR_API_KEY", "")

_WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def fetch_live_train_status(train_number: str, date_str: str | None = None):
    """
    Calls RailRadar's live status endpoint EXACTLY ONCE per user click --
    never on a timer, never in the background.
    Returns the raw 'data' dict on success, or None if not found/failed,
    so the caller can fall back to schedule-simulation tracking.
    """
    if not RAILRADAR_API_KEY:
        return None

    url = f"{RAILRADAR_BASE_URL}/trains/{train_number}/live"
    headers = {"Authorization": f"Bearer {RAILRADAR_API_KEY}"}
    params = {"date": date_str} if date_str else {}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=8)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    payload = response.json()
    if not payload.get("success"):
        return None

    return payload.get("data")


def _normalize_live_api_data(raw, train_number):
    route = raw.get("route", []) or []
    current_loc = raw.get("currentLocation", {}) or {}
    next_halt = raw.get("nextHalt", {}) or {}

    stops = []
    current_index = 0
    for i, r in enumerate(route):
        stops.append({
            "station_name": r.get("stationName"),
            "platform_number": r.get("platform"),
            "arrival": (r.get("actualArrival") or r.get("scheduledArrival") or "")[11:16],
            "departure": (r.get("actualDeparture") or r.get("scheduledDeparture") or "")[11:16],
        })
        if r.get("stationCode") == current_loc.get("stationCode"):
            current_index = i

    total = len(route)
    seg_progress = current_loc.get("segmentProgress", 0) or 0
    progress_fraction = 0.0
    if total > 1:
        progress_fraction = max(0.0, min(100.0, round(((current_index + seg_progress) / (total - 1)) * 100, 1)))

    status = raw.get("status")
    if status == "running":
        if current_loc.get("isHalt"):
            human_status = f"Halted at {stops[current_index]['station_name']}" if stops else "Running"
        elif next_halt.get("stationName"):
            human_status = f"En route to {next_halt['stationName']}"
        else:
            human_status = "Running"
    elif status == "not-started":
        human_status = f"Yet to depart from {stops[0]['station_name']}" if stops else "Not yet started"
    elif status == "completed":
        human_status = f"Journey completed at {stops[-1]['station_name']}" if stops else "Journey completed"
        progress_fraction = 100.0
        current_index = max(total - 1, 0)
    elif status == "cancelled":
        human_status = "Train cancelled"
    else:
        human_status = status or "Unknown"

    return {
        "source": "live_api",
        "train_number": raw.get("trainNumber", train_number),
        "train_name": raw.get("trainName"),
        "current_status": human_status,
        "delay_minutes": raw.get("delayMinutes"),
        "progress_fraction": progress_fraction,
        "current_index": current_index,
        "stations_remaining": max(total - 1 - current_index, 0),
        "speed_kmh": current_loc.get("speedKmh"),
        "next_station": next_halt.get("stationName"),
        "last_updated": raw.get("lastUpdatedAt"),
        "stops": stops,
    }


def _normalize_simulated_data(tracking):
    stops = []
    for s in tracking["stops"]:
        stops.append({
            "station_name": s["station_name"],
            "platform_number": s["platform_number"],
            "arrival": s["arrival"].strftime("%H:%M"),
            "departure": s["departure"].strftime("%H:%M"),
            "lat": s["lat"],
            "lng": s["lng"],
        })
    idx = tracking["current_index"]
    return {
        "source": "simulated",
        "train_number": tracking["train_number"],
        "train_name": tracking["train_name"],
        "current_status": tracking["current_status"],
        "delay_minutes": None,
        "progress_fraction": tracking["progress_fraction"],
        "current_index": idx,
        "stations_remaining": tracking["stations_remaining"],
        "current_lat": tracking["current_lat"],
        "current_lng": tracking["current_lng"],
        "speed_kmh": None,
        "next_station": stops[idx + 1]["station_name"] if idx + 1 < len(stops) else None,
        "last_updated": None,
        "stops": stops,
    }


def get_hybrid_train_tracking(train_number: str, date_str: str):
    """
    main.py sirf isi function ko call karega. Pehle REAL RailRadar data try
    karta hai (ek hi API call, on-demand). Agar wahan train nahi mili, apni
    schedule-simulation tracking par fallback karta hai. Dono me kuch na mile
    to None return karta hai.
    """
    raw = fetch_live_train_status(train_number, date_str)
    if raw:
        return _normalize_live_api_data(raw, train_number)

    simulated = get_train_live_tracking(train_number, date_str)
    if simulated:
        return _normalize_simulated_data(simulated)

    return None


def fetch_trains_for_station(station_code: str, include_intermediate: bool = False):
    """
    Calls RailRadar's Station Timetable Board API to get every REAL train
    that halts at a given station -- one call fetches a whole list of real
    trains (with their real running days), instead of typing them by hand.
    Returns the raw 'data' dict on success, or None on any failure.
    """
    if not RAILRADAR_API_KEY:
        return None

    url = f"{RAILRADAR_BASE_URL}/stations/{station_code.upper()}/trains"
    headers = {"Authorization": f"Bearer {RAILRADAR_API_KEY}"}
    params = {"includeIntermediate": str(include_intermediate).lower()}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    payload = response.json()
    if not payload.get("success"):
        return None

    return payload.get("data")


def _next_date_for_weekday(day_abbr):
    """Finds the next real calendar date that falls on the given weekday (mon/tue/...)."""
    today = date.today()
    target = _WEEKDAY_INDEX.get(day_abbr)
    if target is None:
        return today
    delta = (target - today.weekday()) % 7
    return today + timedelta(days=delta)


def build_rows_from_station_board(board_data):
    """
    Converts RailRadar's station-board response into rows shaped for our
    own `trains` table -- one row PER WEEKDAY the train actually runs, so
    the dashboard's Mon-Sun tabs reflect each train's real running days.
    """
    if not board_data:
        return []

    station_name = board_data.get("station", {}).get("name", "")
    rows = []

    for entry in board_data.get("trains", []):
        train = entry.get("train", {})
        stop = entry.get("stop", {})
        run_days = train.get("runDays", [])

        arrival = stop.get("arrival") or stop.get("departure")
        departure = stop.get("departure") or stop.get("arrival")
        if not arrival or not departure:
            continue
        if len(arrival) == 5:
            arrival += ":00"
        if len(departure) == 5:
            departure += ":00"

        for day_abbr in run_days:
            travel_date = _next_date_for_weekday(day_abbr)
            rows.append({
                "train_number": train.get("number"),
                "train_name": train.get("name"),
                "station_name": station_name,
                "source_station": train.get("source", {}).get("name", ""),
                "destination_station": train.get("destination", {}).get("name", ""),
                "scheduled_arrival": arrival,
                "scheduled_departure": departure,
                "travel_date": travel_date.strftime("%Y-%m-%d"),
                "run_day": day_abbr,
            })

    return rows


def fetch_trains_between_stations(from_code: str, to_code: str, date_str: str | None = None):
    """
    Calls RailRadar's real 'Trains Between Stations' API -- returns every
    REAL train running between two station codes. One API call regardless
    of how many trains run that route.
    """
    if not RAILRADAR_API_KEY:
        return None

    url = f"{RAILRADAR_BASE_URL}/trains/between/{from_code.upper()}/{to_code.upper()}"
    headers = {"Authorization": f"Bearer {RAILRADAR_API_KEY}"}
    params = {"date": date_str} if date_str else {}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    payload = response.json()
    if not payload.get("success"):
        return None

    return payload.get("data")


def build_rows_from_between_stations(data):
    """
    Converts RailRadar's between-stations response into rows shaped for our
    own `trains` table -- one row PER WEEKDAY the train actually runs.
    The FROM station is used as this row's halting station (station_name).
    """
    if not data:
        return []

    from_name = data.get("from", {}).get("name", "")
    to_name = data.get("to", {}).get("name", "")
    rows = []

    for entry in data.get("trains", []):
        train = entry.get("train", {})
        run_days = train.get("runDays", [])

        departure = entry.get("from", {}).get("departure")
        if not departure:
            continue
        if len(departure) == 5:
            departure += ":00"

        for day_abbr in run_days:
            travel_date = _next_date_for_weekday(day_abbr)
            rows.append({
                "train_number": train.get("number"),
                "train_name": train.get("name"),
                "station_name": from_name,
                "source_station": from_name,
                "destination_station": to_name,
                "scheduled_arrival": departure,
                "scheduled_departure": departure,
                "travel_date": travel_date.strftime("%Y-%m-%d"),
                "run_day": day_abbr,
            })

    return rows


def resolve_station_code(station_name: str):
    """Looks up a real station CODE from a typed station NAME (e.g. 'New Delhi' -> 'NDLS')."""
    if not RAILRADAR_API_KEY:
        return None

    url = f"{RAILRADAR_BASE_URL}/lookup/search/stations"
    headers = {"Authorization": f"Bearer {RAILRADAR_API_KEY}"}
    params = {"q": station_name}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=8)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    payload = response.json()
    if not payload.get("success"):
        return None

    results = payload.get("data") or []
    if isinstance(results, dict):
        results = results.get("stations") or results.get("results") or []
    if not results:
        return None

    first = results[0]
    return first.get("code") or first.get("stationCode")
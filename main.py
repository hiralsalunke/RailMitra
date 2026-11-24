import json
import os
import secrets
import socket
from collections import defaultdict
from datetime import datetime, timedelta
import pandas as pd
import bokeh
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from app.auth import verify_admin
from app.database import (
    get_all_trains, get_all_trains_full, insert_trains_bulk, detect_and_store_conflicts,
    get_all_conflicts, apply_recommendation, clear_all_data, clear_everything, get_analytics_data,
    update_train, delete_train, get_conflict_history, insert_trains_auto_schedule,
    smart_query_search, get_station_risk_summary,
    get_distinct_stations, get_train_live_tracking, find_duplicate_trains, auto_assign_platform,
    delete_trains_by_ids, get_distinct_route_stations, is_fetch_cached, store_to_master_cache,
    get_cached_rows_for_key, get_cached_station_code, store_station_code,
    get_platform_timeline_data, get_distinct_travel_dates, get_operational_analytics,
    clear_fetched_data, extract_station_mentions, has_route_data
)
from app.railradar_client import get_hybrid_train_tracking, fetch_trains_for_station, build_rows_from_station_board, resolve_station_code, fetch_trains_between_stations, build_rows_from_between_stations
from app.priority_classifier import auto_assign_priority
from app.live_status_updater import update_live_statuses
from app.bokeh_charts import build_analytics_charts
from app.validators import validate_train_row

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ---- Secure session key ----
# Reads SECRET_KEY from .env so it's not hardcoded in source and stays
# stable across restarts (a stable key means logged-in sessions survive a
# server restart; a random key generated fresh every run would silently
# log everyone out on every restart).
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print(
        "WARNING: SECRET_KEY not set in .env -- using a temporary key generated "
        "for this run only. Sessions will NOT survive a server restart. "
        "Add SECRET_KEY=<random value> to your .env file to fix this."
    )

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=False,
    max_age=14 * 24 * 60 * 60,
)

scheduler = BackgroundScheduler()
scheduler.add_job(update_live_statuses, "interval", seconds=60)

# ---- Login attempt limiting ----
# In-memory, per-client-IP tracking of failed login attempts. After
# MAX_LOGIN_ATTEMPTS failures within LOCKOUT_MINUTES, further attempts from
# that IP are blocked until the window rolls past. This resets on server
# restart (acceptable for a single-admin app of this scale) -- for a
# multi-instance production deployment this would need a shared store
# (Redis, DB) instead of an in-memory dict.
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
_login_attempts = defaultdict(list)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_locked_out(key: str):
    """Returns (locked: bool, seconds_left: int) -- seconds_left is precise
    (not rounded to minutes) so the login page can show a live countdown."""
    now = datetime.now()
    cutoff = now - timedelta(minutes=LOCKOUT_MINUTES)
    _login_attempts[key] = [t for t in _login_attempts[key] if t > cutoff]
    if len(_login_attempts[key]) >= MAX_LOGIN_ATTEMPTS:
        oldest = min(_login_attempts[key])
        unlock_at = oldest + timedelta(minutes=LOCKOUT_MINUTES)
        seconds_left = max(int((unlock_at - now).total_seconds()), 1)
        return True, seconds_left
    return False, 0


def _record_failed_attempt(key: str):
    _login_attempts[key].append(datetime.now())


def _clear_attempts(key: str):
    _login_attempts.pop(key, None)


def _get_lan_ip() -> str:
    """
    Best-effort local network IP (e.g. 192.168.x.x), NOT 127.0.0.1 --
    needed because a QR code (or any link) meant to be opened from a
    different device (like a phone) must point at an address that
    device can actually reach. 127.0.0.1 always means "this same
    device", so a phone scanning a 127.0.0.1 link tries to connect to
    itself and fails. Falls back to 127.0.0.1 only if no network is
    available at all.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


SERVER_LAN_IP = _get_lan_ip()

# Optional override for demo scenarios where the phone can't share a WiFi
# network with the laptop (e.g. venue WiFi is unreliable and the phone is
# on its own mobile data). Set PUBLIC_BASE_URL in .env to a tunnel URL
# (e.g. from ngrok: https://xxxx.ngrok-free.app) and every QR code will
# use that instead of the LAN IP -- works over the real internet, no
# network-matching needed at all.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


@app.on_event("startup")
def start_scheduler():
    scheduler.start()


@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown()


def is_logged_in(request: Request) -> bool:
    return request.session.get("logged_in", False)


def _resolve_code_cached(name):
    cached = get_cached_station_code(name)
    if cached:
        return cached
    code = resolve_station_code(name)
    if code:
        store_station_code(name, code)
    return code


def _store_fetched_rows(rows):
    if not rows:
        return
    for row in rows:
        row["platform_number"] = auto_assign_platform(
            row["station_name"], row["travel_date"], row["scheduled_arrival"], row["scheduled_departure"]
        )
        row["priority"] = auto_assign_priority(row["train_name"])
    insert_trains_bulk(rows, is_base=True)
    detect_and_store_conflicts()


def _fetch_and_store_station(station_name):
    """Ensures the given station's trains exist in the DB (fetching from
    RailRadar if not already known/cached). Returns the resolved/official
    station name, or None if it couldn't be resolved at all."""
    known_stops = get_distinct_stations()
    match = next((s for s in known_stops if s.lower() == station_name.lower()), None)
    if match:
        return match

    code = _resolve_code_cached(station_name)
    if not code:
        return None

    fetch_key = f"STATION:{code.upper()}"
    if is_fetch_cached(fetch_key):
        _store_fetched_rows(get_cached_rows_for_key(fetch_key))
        return station_name

    board_data = fetch_trains_for_station(code)
    if not board_data:
        return None
    rows = build_rows_from_station_board(board_data)
    store_to_master_cache(fetch_key, rows)
    _store_fetched_rows(rows)
    official_name = board_data.get("station", {}).get("name")
    return official_name or station_name


def _fetch_and_store_route(from_station, to_station):
    """Ensures trains for a from->to route exist in the DB (fetching from
    RailRadar if needed). Returns (from_name, to_name) resolved/official
    names, or (None, None) if it couldn't be resolved. Uses a real
    same-train-stops-at-both-stations check (has_route_data) rather than
    just checking if both names appear anywhere in source/destination
    columns -- that loose check could wrongly skip a fetch even when no
    train actually connecting these two specific stations existed yet."""
    if has_route_data(from_station, to_station):
        return from_station, to_station

    from_code = _resolve_code_cached(from_station)
    to_code = _resolve_code_cached(to_station)
    if not (from_code and to_code):
        return None, None

    fetch_key = f"ROUTE:{from_code.upper()}-{to_code.upper()}"
    if is_fetch_cached(fetch_key):
        _store_fetched_rows(get_cached_rows_for_key(fetch_key))
        return from_station, to_station

    data = fetch_trains_between_stations(from_code, to_code)
    if not data:
        return None, None
    rows = build_rows_from_between_stations(data)
    store_to_master_cache(fetch_key, rows)
    _store_fetched_rows(rows)
    official_from = data.get("from", {}).get("name") or from_station
    official_to = data.get("to", {}).get("name") or to_station
    return official_from, official_to


@app.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    key = _client_key(request)
    locked, seconds_left = _is_locked_out(key)
    if locked:
        return templates.TemplateResponse(
            request, "login.html",
            {
                "error": f"Too many failed login attempts. You have {MAX_LOGIN_ATTEMPTS} tries every {LOCKOUT_MINUTES} minutes -- please wait for the timer below.",
                "locked": True,
                "lockout_seconds": seconds_left,
            }
        )
    return templates.TemplateResponse(request, "login.html", {})


@app.get("/live-status", response_class=HTMLResponse)
def live_status_page(request: Request, from_station: str = None, to_station: str = None, date: str = None, hour: str = None):
    from_station = (from_station or "").strip() or None
    to_station = (to_station or "").strip() or None
    date = (date or "").strip() or None
    hour_int = int(hour) if hour not in (None, "") else None

    if from_station and to_station:
        resolved_from, resolved_to = _fetch_and_store_route(from_station, to_station)
        if resolved_from:
            from_station = resolved_from
        if resolved_to:
            to_station = resolved_to
    elif from_station:
        resolved = _fetch_and_store_station(from_station)
        if resolved:
            from_station = resolved
    elif to_station:
        resolved = _fetch_and_store_station(to_station)
        if resolved:
            to_station = resolved

    if from_station or to_station or date or hour_int is not None:
        trains = get_all_trains(date=date, from_station=from_station, to_station=to_station, hour=hour_int)
    else:
        # No search this visit -- start empty rather than showing
        # whatever trains happen to still be in the DB from an earlier
        # search (by anyone). The page should feel "fresh" each visit.
        trains = []

    all_stations = get_distinct_route_stations()

    return templates.TemplateResponse(
        request, "live_status.html",
        {
            "trains": trains,
            "all_stations": all_stations,
            "selected_from_station": from_station,
            "selected_to_station": to_station,
            "selected_date": date,
            "selected_hour": hour_int,
        }
    )


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    key = _client_key(request)
    locked, seconds_left = _is_locked_out(key)
    if locked:
        return templates.TemplateResponse(
            request, "login.html",
            {
                "error": f"Too many failed login attempts. You have {MAX_LOGIN_ATTEMPTS} tries every {LOCKOUT_MINUTES} minutes -- please wait for the timer below.",
                "locked": True,
                "lockout_seconds": seconds_left,
            }
        )

    if verify_admin(username, password):
        _clear_attempts(key)
        request.session["logged_in"] = True
        return RedirectResponse(url="/dashboard?welcome=1", status_code=303)

    _record_failed_attempt(key)
    locked_now, seconds_left = _is_locked_out(key)
    if locked_now:
        return templates.TemplateResponse(
            request, "login.html",
            {
                "error": f"Too many failed login attempts. You have {MAX_LOGIN_ATTEMPTS} tries every {LOCKOUT_MINUTES} minutes -- please wait for the timer below.",
                "locked": True,
                "lockout_seconds": seconds_left,
            }
        )

    remaining = MAX_LOGIN_ATTEMPTS - len(_login_attempts[key])
    error = f"Invalid credentials. You have {remaining} attempt(s) remaining before a {LOCKOUT_MINUTES}-minute lockout."
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.get("/logout")
def logout(request: Request):
    # NOTE: fetched/imported trains are intentionally NOT deleted here anymore.
    # They must persist across login/logout and only get removed when the
    # user explicitly presses "Clear Imported Data" (/clear_data) or
    # "Clear Fetched Data" (/clear_fetched) on the dashboard.
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, day: str = None, date: str = None, from_station: str = None, to_station: str = None, hour: str = None):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)

    hour_int = int(hour) if hour not in (None, "") else None

    from_station = (from_station or "").strip() or None
    to_station = (to_station or "").strip() or None

    if from_station and to_station:
        resolved_from, resolved_to = _fetch_and_store_route(from_station, to_station)
        if resolved_from:
            from_station = resolved_from
        if resolved_to:
            to_station = resolved_to
    elif from_station:
        resolved = _fetch_and_store_station(from_station)
        if resolved:
            from_station = resolved
    elif to_station:
        resolved = _fetch_and_store_station(to_station)
        if resolved:
            to_station = resolved

    trains = get_all_trains(day=day, date=date, from_station=from_station, to_station=to_station, hour=hour_int)
    conflicts = get_all_conflicts(day=day, date=date)
    station_risk = get_station_risk_summary()
    all_stations = get_distinct_route_stations()
    import_error = request.session.pop("import_error", None)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "trains": trains, "conflicts": conflicts, "selected_day": day,
            "station_risk": station_risk, "all_stations": all_stations,
            "selected_date": date, "selected_from_station": from_station,
            "selected_to_station": to_station, "selected_hour": hour_int,
            "import_error": import_error,
        }
    )


@app.post("/import")
async def import_trains(request: Request, file: UploadFile = File(...)):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)

    try:
        contents = await file.read()
        temp_path = f"temp_{file.filename}"
        with open(temp_path, "wb") as f:
            f.write(contents)

        df = pd.read_excel(temp_path, keep_default_na=False)

        required_columns = [
            "train_number", "train_name", "station_name", "source_station",
            "destination_station", "platform_number", "scheduled_arrival",
            "scheduled_departure", "travel_date"
        ]
        missing = [c for c in required_columns if c not in df.columns]
        if missing:
            raise ValueError(f"Your Excel file is missing these column(s): {', '.join(missing)}")

        if len(df) == 0:
            raise ValueError("The Excel file has no data rows.")

        rows = []
        for idx, r in df.iterrows():
            try:
                train_name = str(r["train_name"])
                row = {
                    "train_number": str(r["train_number"]),
                    "train_name": train_name,
                    "station_name": str(r["station_name"]),
                    "source_station": str(r["source_station"]),
                    "destination_station": str(r["destination_station"]),
                    "platform_number": int(r["platform_number"]),
                    "scheduled_arrival": str(r["scheduled_arrival"]),
                    "scheduled_departure": str(r["scheduled_departure"]),
                    "travel_date": str(r["travel_date"]),
                    "priority": auto_assign_priority(train_name),
                }
            except (ValueError, TypeError):
                raise ValueError(f"Row {idx + 2} has invalid data (check platform_number is a whole number, and no cells are empty).")

            try:
                validate_train_row(row)
            except ValueError as ve:
                raise ValueError(f"Row {idx + 2}: {ve}")

            rows.append(row)

        seen_in_file = set()
        in_file_duplicates = []
        for row in rows:
            key = (row["train_number"], row["station_name"], row["travel_date"])
            if key in seen_in_file:
                in_file_duplicates.append(f"Train {row['train_number']} at {row['station_name']} on {row['travel_date']}")
            seen_in_file.add(key)

        if in_file_duplicates:
            raise ValueError(
                "Duplicate rows found within the file itself: " + "; ".join(in_file_duplicates[:5]) +
                (f" and {len(in_file_duplicates)-5} more" if len(in_file_duplicates) > 5 else "")
            )

        existing_duplicates = find_duplicate_trains(rows)
        if existing_duplicates:
            raise ValueError(
                "These trains already exist in the schedule: " + "; ".join(existing_duplicates[:5]) +
                (f" and {len(existing_duplicates)-5} more" if len(existing_duplicates) > 5 else "") +
                ". Edit or delete the existing entry first, or use different values."
            )

        insert_trains_bulk(rows)
        detect_and_store_conflicts()

    except Exception as e:
        request.session["import_error"] = str(e)
        return RedirectResponse(url="/dashboard", status_code=303)

    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/import_auto_schedule")
async def import_auto_schedule(request: Request, file: UploadFile = File(...)):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)

    try:
        contents = await file.read()
        temp_path = f"temp_auto_{file.filename}"
        with open(temp_path, "wb") as f:
            f.write(contents)

        df = pd.read_excel(temp_path, keep_default_na=False)

        required_columns = [
            "train_number", "train_name", "station_name", "source_station",
            "destination_station", "scheduled_arrival", "scheduled_departure", "travel_date"
        ]
        missing = [c for c in required_columns if c not in df.columns]
        if missing:
            raise ValueError(f"Your Excel file is missing these column(s): {', '.join(missing)}")

        if len(df) == 0:
            raise ValueError("The Excel file has no data rows.")

        rows = []
        for idx, r in df.iterrows():
            try:
                train_name = str(r["train_name"])
                row = {
                    "train_number": str(r["train_number"]),
                    "train_name": train_name,
                    "station_name": str(r["station_name"]),
                    "source_station": str(r["source_station"]),
                    "destination_station": str(r["destination_station"]),
                    "scheduled_arrival": str(r["scheduled_arrival"]),
                    "scheduled_departure": str(r["scheduled_departure"]),
                    "travel_date": str(r["travel_date"]),
                    "priority": auto_assign_priority(train_name),
                }
            except (ValueError, TypeError):
                raise ValueError(f"Row {idx + 2} has invalid or missing data.")

            try:
                validate_train_row(row, check_platform=False)
            except ValueError as ve:
                raise ValueError(f"Row {idx + 2}: {ve}")

            rows.append(row)

        insert_trains_auto_schedule(rows)
        detect_and_store_conflicts()

    except Exception as e:
        request.session["import_error"] = str(e)
        return RedirectResponse(url="/dashboard", status_code=303)

    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/add_train")
def add_train(
    request: Request,
    train_number: str = Form(...),
    train_name: str = Form(...),
    station_name: str = Form(...),
    source_station: str = Form(...),
    destination_station: str = Form(...),
    platform_number: int = Form(...),
    scheduled_arrival: str = Form(...),
    scheduled_departure: str = Form(...),
    travel_date: str = Form(...),
):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)

    row = {
        "train_number": train_number,
        "train_name": train_name,
        "station_name": station_name,
        "source_station": source_station,
        "destination_station": destination_station,
        "platform_number": platform_number,
        "scheduled_arrival": scheduled_arrival,
        "scheduled_departure": scheduled_departure,
        "travel_date": travel_date,
        "priority": auto_assign_priority(train_name),
    }

    try:
        validate_train_row(row)
    except ValueError as ve:
        request.session["import_error"] = str(ve)
        return RedirectResponse(url="/dashboard", status_code=303)

    duplicates = find_duplicate_trains([row])
    if duplicates:
        request.session["import_error"] = f"This train already exists: {duplicates[0]}. Edit or delete the existing entry first, or use different values."
        return RedirectResponse(url="/dashboard", status_code=303)

    insert_trains_bulk([row])
    detect_and_store_conflicts()

    weekday_name = datetime.strptime(travel_date, "%Y-%m-%d").strftime("%A")
    return RedirectResponse(url=f"/dashboard?day={weekday_name}", status_code=303)


@app.post("/edit_train/{train_id}")
def edit_train(
    request: Request,
    train_id: int,
    train_number: str = Form(...),
    train_name: str = Form(...),
    station_name: str = Form(...),
    source_station: str = Form(...),
    destination_station: str = Form(...),
    platform_number: int = Form(...),
    scheduled_arrival: str = Form(...),
    scheduled_departure: str = Form(...),
    travel_date: str = Form(...),
):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)

    row = {
        "train_number": train_number,
        "train_name": train_name,
        "station_name": station_name,
        "source_station": source_station,
        "destination_station": destination_station,
        "platform_number": platform_number,
        "scheduled_arrival": scheduled_arrival,
        "scheduled_departure": scheduled_departure,
        "travel_date": travel_date,
        "priority": auto_assign_priority(train_name),
    }

    try:
        validate_train_row(row)
    except ValueError as ve:
        request.session["import_error"] = str(ve)
        return RedirectResponse(url="/dashboard", status_code=303)

    update_train(train_id, row)

    weekday_name = datetime.strptime(travel_date, "%Y-%m-%d").strftime("%A")
    return RedirectResponse(url=f"/dashboard?day={weekday_name}", status_code=303)


@app.post("/delete_train/{train_id}")
def delete_train_route(request: Request, train_id: int, day: str = Form(default=None)):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    delete_train(train_id)
    if day:
        return RedirectResponse(url=f"/dashboard?day={day}", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/apply_recommendation/{conflict_id}")
def apply_recommendation_route(request: Request, conflict_id: int):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    apply_recommendation(conflict_id)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/clear_data")
def clear_data_route(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    clear_all_data()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/clear_everything")
def clear_everything_route(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    clear_everything()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/clear_fetched")
def clear_fetched_route(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    clear_fetched_data()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    data = get_operational_analytics()
    return templates.TemplateResponse(request, "analytics.html", data)


@app.get("/conflict_history", response_class=HTMLResponse)
def conflict_history(request: Request, day: str = None, date: str = None):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    history = get_conflict_history(day=day, date=date)
    return templates.TemplateResponse(
        request, "conflict_history.html",
        {"history": history, "selected_day": day, "selected_date": date}
    )


@app.get("/export_report")
def export_report(request: Request, day: str = None, date: str = None):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)

    trains = get_all_trains_full(day=day, date=date)
    conflicts = get_all_conflicts(day=day, date=date)

    trains_df = pd.DataFrame(trains) if trains else pd.DataFrame(
        columns=["train_number", "train_name", "station_name", "source_station",
                 "destination_station", "platform_number", "scheduled_arrival",
                 "scheduled_departure", "travel_date", "priority", "status", "delay_minutes"]
    )
    conflicts_df = pd.DataFrame(conflicts) if conflicts else pd.DataFrame(
        columns=["station_name", "platform_number", "travel_date", "suggestion", "estimated_delay"]
    )

    output_path = "railmitra_report.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        trains_df.to_excel(writer, sheet_name="Trains", index=False)
        conflicts_df.to_excel(writer, sheet_name="Conflicts", index=False)

    label = date or day or "All"
    return FileResponse(
        output_path,
        filename=f"RailMitra_Report_{label}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.get("/export_history")
def export_history(request: Request, day: str = None, date: str = None):
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)

    history = get_conflict_history(day=day, date=date)
    history_df = pd.DataFrame(history) if history else pd.DataFrame(
        columns=["train_1_number", "train_1_name", "train_2_number", "train_2_name",
                 "station_name", "platform_number", "travel_date", "suggestion",
                 "outcome", "estimated_delay", "logged_at"]
    )

    output_path = "railmitra_history.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        history_df.to_excel(writer, sheet_name="Conflict History", index=False)

    label = date or day or "All"
    return FileResponse(
        output_path,
        filename=f"RailMitra_History_{label}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.get("/platform_timeline", response_class=HTMLResponse)
def platform_timeline(request: Request, date: str = None):
    """
    Replaces the old Network Map. Reads live from the `trains` table only
    -- no external API calls, no geocoding, instant load. Whatever trains
    currently exist (imported, added, or fetched from RailRadar) show up
    here as colored blocks per platform, on a 24-hour axis; overlapping
    (conflicting) trains are automatically highlighted red, reusing the
    SAME conflict detection already computed elsewhere in the app.
    """
    if not is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    all_dates = get_distinct_travel_dates()
    selected_date = date or (all_dates[0] if all_dates else None)
    timeline_data = get_platform_timeline_data(selected_date) if selected_date else {}
    return templates.TemplateResponse(
        request, "platform_timeline.html",
        {"timeline_data": timeline_data, "all_dates": all_dates, "selected_date": selected_date}
    )


@app.get("/track_train", response_class=HTMLResponse)
def track_train(request: Request, train_number: str = None, date: str = None):
    # Public on purpose: a phone scanning the QR code from /track_qr is a
    # DIFFERENT device with no admin session, so this page must work
    # without login.

    # page can be opened directly (no train_number yet) -- shows just the
    # search box, so the user can type a train number/date manually
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    tracking = get_hybrid_train_tracking(train_number, date) if train_number else None

    return templates.TemplateResponse(
        request, "track_train.html",
        {"tracking": tracking, "train_number": train_number, "date": date}
    )


@app.get("/track_qr", response_class=HTMLResponse)
def track_qr_page(request: Request, train_number: str = None, date: str = None):
    """
    Public, minimal "scan to track" page -- reached by clicking Track next
    to a train on /live-status. Deliberately shows NOTHING but a QR code
    (no stats, no timeline, no admin-style layout): the point is to hand
    the person off to their own phone, not to duplicate the dashboard
    here. Does NOT call the RailRadar API itself (that would burn quota
    for a page that's just a QR label) -- the actual live lookup happens
    once, on /track_train, when the QR is scanned.
    """
    if not train_number:
        return RedirectResponse(url="/live-status", status_code=303)
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    if PUBLIC_BASE_URL:
        base = PUBLIC_BASE_URL
    else:
        port = request.url.port or 8000
        base = f"http://{SERVER_LAN_IP}:{port}"
    tracking_url_for_qr = f"{base}/track_train?train_number={train_number}&date={date}"

    return templates.TemplateResponse(
        request, "track_qr.html",
        {
            "train_number": train_number,
            "date": date,
            "tracking_url_for_qr": tracking_url_for_qr,
        }
    )


@app.post("/smart_query")
async def smart_query(request: Request, query: str = Form(...)):
    if not is_logged_in(request):
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    # If the question mentions a station that isn't in the DB yet, fetch it
    # live from RailRadar first so the answer isn't empty just because
    # nobody has looked up that station before.
    known_stations = get_distinct_route_stations()
    mentioned = extract_station_mentions(query, known_stations)
    if mentioned:
        _fetch_and_store_station(mentioned[0])

    data = smart_query_search(query)

    def clean(obj):
        return {k: str(v) if v is not None else "" for k, v in obj.items()}

    data["results"] = [clean(r) for r in data.get("results", [])]
    return JSONResponse(data)
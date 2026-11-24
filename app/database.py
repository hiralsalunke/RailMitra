import os
from dotenv import load_dotenv
load_dotenv()

import mysql.connector
from datetime import datetime, timedelta, date as date_cls
from app.ai_predictor import predict_delay
from app.station_coordinates import get_station_coordinates

def get_db_connection():
    conn = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3307")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "railmitra")
    )
    return conn


def get_all_trains(day=None, date=None, from_station=None, to_station=None, hour=None):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if date or from_station or to_station or hour is not None:
        query = "SELECT * FROM trains WHERE 1=1"
        params = []
        if date:
            query += " AND travel_date = %s"
            params.append(date)
        elif day:
            query += " AND DAYNAME(travel_date) = %s"
            params.append(day)
        if from_station:
            query += " AND LOWER(source_station) LIKE LOWER(%s)"
            params.append(f"%{from_station}%")
        if to_station:
            query += " AND LOWER(destination_station) LIKE LOWER(%s)"
            params.append(f"%{to_station}%")
        if hour is not None:
            query += " AND HOUR(scheduled_arrival) = %s"
            params.append(hour)
        query += " ORDER BY station_name, scheduled_arrival"
        cursor.execute(query, params)
    elif day:
        cursor.execute(
            "SELECT * FROM trains WHERE DAYNAME(travel_date) = %s ORDER BY station_name, scheduled_arrival",
            (day,)
        )
    else:
        # Show everything currently in the trains table -- imported,
        # manually-added, AND fetched (is_base_schedule = TRUE) trains.
        # Previously this filtered to is_base_schedule = FALSE only, which
        # made fetched trains disappear from the default dashboard view
        # even though they were still in the database.
        cursor.execute(
            "SELECT * FROM trains ORDER BY travel_date, station_name, scheduled_arrival"
        )

    trains = cursor.fetchall()
    cursor.close()
    conn.close()
    return trains


def get_distinct_route_stations():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT DISTINCT source_station AS s FROM trains UNION SELECT DISTINCT destination_station AS s FROM trains ORDER BY s")
    stations = [r["s"] for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return stations


def get_distinct_stations():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT DISTINCT station_name FROM trains ORDER BY station_name")
    stations = [r["station_name"] for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return stations


def has_route_data(from_station, to_station):
    """
    Real check for whether we already have usable data for a from->to
    route -- i.e. at least one train that actually stops at BOTH stations
    (same train_number + travel_date, departing from_station before
    arriving to_station). This replaces a weaker check that only looked
    at whether both names appeared ANYWHERE in source/destination
    columns, which could wrongly skip a fetch even when no train
    connecting these two specific stations existed yet.
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT 1 FROM trains a
        JOIN trains b ON a.train_number = b.train_number AND a.travel_date = b.travel_date
        WHERE LOWER(a.station_name) LIKE LOWER(%s)
          AND LOWER(b.station_name) LIKE LOWER(%s)
          AND a.scheduled_departure < b.scheduled_arrival
        LIMIT 1
    """, (f"%{from_station}%", f"%{to_station}%"))
    found = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return found


def get_all_trains_full(day=None, date=None):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = "SELECT * FROM trains WHERE 1=1"
    params = []
    if day:
        query += " AND DAYNAME(travel_date) = %s"
        params.append(day)
    if date:
        query += " AND travel_date = %s"
        params.append(date)
    query += " ORDER BY travel_date, station_name, scheduled_arrival"
    cursor.execute(query, params)
    trains = cursor.fetchall()
    cursor.close()
    conn.close()
    return trains


def insert_trains_bulk(rows, is_base=False):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        INSERT IGNORE INTO trains
        (train_number, train_name, station_name, source_station, destination_station,
         platform_number, scheduled_arrival, scheduled_departure, travel_date, priority, is_base_schedule)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    inserted_ids = []
    for row in rows:
        cursor.execute(query, (
            row["train_number"],
            row["train_name"],
            row["station_name"],
            row["source_station"],
            row["destination_station"],
            row["platform_number"],
            row["scheduled_arrival"],
            row["scheduled_departure"],
            row["travel_date"],
            row.get("priority", "Medium"),
            is_base,
        ))
        if cursor.lastrowid:
            inserted_ids.append(cursor.lastrowid)

    conn.commit()
    cursor.close()
    conn.close()
    return inserted_ids


def delete_trains_by_ids(ids):
    if not ids:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conflicts")
    placeholders = ",".join(["%s"] * len(ids))
    cursor.execute(f"DELETE FROM trains WHERE id IN ({placeholders})", tuple(ids))
    conn.commit()
    cursor.close()
    conn.close()
    detect_and_store_conflicts()


def update_train(train_id, row):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conflicts")
    cursor.execute("""
        UPDATE trains SET
            train_number=%s, train_name=%s, station_name=%s, source_station=%s,
            destination_station=%s, platform_number=%s, scheduled_arrival=%s,
            scheduled_departure=%s, travel_date=%s, priority=%s
        WHERE id=%s
    """, (
        row["train_number"], row["train_name"], row["station_name"], row["source_station"],
        row["destination_station"], row["platform_number"], row["scheduled_arrival"],
        row["scheduled_departure"], row["travel_date"], row.get("priority", "Medium"), train_id
    ))
    conn.commit()
    cursor.close()
    conn.close()
    detect_and_store_conflicts()


def delete_train(train_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conflicts")
    cursor.execute("DELETE FROM trains WHERE id = %s", (train_id,))
    conn.commit()
    cursor.close()
    conn.close()
    detect_and_store_conflicts()


def auto_assign_platform(station_name, travel_date, arrival, departure):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT platform_number, scheduled_arrival, scheduled_departure FROM trains WHERE station_name=%s AND travel_date=%s",
        (station_name, travel_date)
    )
    existing = cursor.fetchall()
    cursor.close()
    conn.close()

    new_start = _to_timedelta(arrival)
    new_end = _to_timedelta(departure)

    used_platforms = sorted(set(t["platform_number"] for t in existing)) if existing else []
    max_platform = max(used_platforms) if used_platforms else 0

    for p in range(1, max_platform + 1):
        occupied = False
        for t in existing:
            if t["platform_number"] != p:
                continue
            t_start = _to_timedelta(t["scheduled_arrival"])
            t_end = _to_timedelta(t["scheduled_departure"])
            if new_start < t_end and t_start < new_end:
                occupied = True
                break
        if not occupied:
            return p

    return max_platform + 1


def insert_trains_auto_schedule(rows, is_base=False):
    for row in rows:
        row["platform_number"] = auto_assign_platform(
            row["station_name"], row["travel_date"], row["scheduled_arrival"], row["scheduled_departure"]
        )
        insert_trains_bulk([row], is_base=is_base)


def _to_timedelta(value):
    if isinstance(value, timedelta):
        return value
    h, m, s = str(value).split(":")
    return timedelta(hours=int(h), minutes=int(m), seconds=int(s))


_PRIORITY_RANK = {"High": 3, "Medium": 2, "Low": 1}


def _log_conflict_history(cursor, a, b, station_name, platform_number, travel_date, suggestion, outcome, estimated_delay):
    first, second = sorted([a, b], key=lambda t: t["train_number"])
    cursor.execute("""
        INSERT IGNORE INTO conflict_history
        (train_1_number, train_1_name, train_2_number, train_2_name, station_name,
         platform_number, travel_date, suggestion, outcome, estimated_delay)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        first["train_number"], first["train_name"], second["train_number"], second["train_name"],
        station_name, platform_number, travel_date, suggestion, outcome, estimated_delay
    ))


def detect_and_store_conflicts():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("DELETE FROM conflicts")

    cursor.execute("SELECT * FROM trains")
    all_trains = cursor.fetchall()

    platform_groups = {}
    station_day_trains = {}

    for t in all_trains:
        pkey = (t["station_name"], t["platform_number"], str(t["travel_date"]))
        platform_groups.setdefault(pkey, []).append(t)

        skey = (t["station_name"], str(t["travel_date"]))
        station_day_trains.setdefault(skey, []).append(t)

    insert_query = """
        INSERT INTO conflicts
        (train_id_1, train_id_2, station_name, platform_number, travel_date,
         keep_train_id, reschedule_train_id, suggested_platform, estimated_delay, suggestion)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    conflicts_found = 0

    for pkey, group_trains in platform_groups.items():
        if len(group_trains) < 2:
            continue

        for i in range(len(group_trains)):
            for j in range(i + 1, len(group_trains)):
                a = group_trains[i]
                b = group_trains[j]

                if a["train_number"] == b["train_number"]:
                    continue

                a_start = _to_timedelta(a["scheduled_arrival"])
                a_end = _to_timedelta(a["scheduled_departure"])
                b_start = _to_timedelta(b["scheduled_arrival"])
                b_end = _to_timedelta(b["scheduled_departure"])

                if not (a_start < b_end and b_start < a_end):
                    continue

                a_rank = _PRIORITY_RANK.get(a["priority"], 2)
                b_rank = _PRIORITY_RANK.get(b["priority"], 2)

                if a_rank >= b_rank:
                    keep, move = a, b
                    move_start, move_end = b_start, b_end
                else:
                    keep, move = b, a
                    move_start, move_end = a_start, a_end

                skey = (keep["station_name"], str(keep["travel_date"]))
                station_trains = station_day_trains.get(skey, [])
                all_platforms_here = sorted(set(t["platform_number"] for t in station_trains))

                suggested_platform = None
                for p in all_platforms_here:
                    if p == keep["platform_number"]:
                        continue
                    occupied = False
                    for st in station_trains:
                        if st["platform_number"] != p:
                            continue
                        st_start = _to_timedelta(st["scheduled_arrival"])
                        st_end = _to_timedelta(st["scheduled_departure"])
                        if move_start < st_end and st_start < move_end:
                            occupied = True
                            break
                    if not occupied:
                        suggested_platform = p
                        break

                if suggested_platform is not None:
                    estimated_delay = 0
                    suggestion = (
                        f"Keep {keep['train_name']} ({keep['train_number']}) on Platform "
                        f"{keep['platform_number']} — higher priority. "
                        f"Reschedule {move['train_name']} ({move['train_number']}) to Platform {suggested_platform}."
                    )
                    cursor.execute(
                        "UPDATE trains SET status = %s, delay_minutes = 0 WHERE id = %s",
                        ("Reassignment Suggested", move["id"])
                    )
                    history_outcome = "Reassignment Suggested"
                else:
                    estimated_delay = predict_delay(
                        station_name=keep["station_name"],
                        platform_number=keep["platform_number"],
                        travel_date=keep["travel_date"],
                        arrival_time=move["scheduled_arrival"]
                    )
                    suggestion = (
                        f"No alternate platform available. Hold {move['train_name']} "
                        f"({move['train_number']}) at outer signal — ML-predicted delay: ~{estimated_delay} min."
                    )
                    cursor.execute(
                        "UPDATE trains SET status = %s, delay_minutes = %s WHERE id = %s",
                        ("Delayed", estimated_delay, move["id"])
                    )
                    history_outcome = "Delayed"

                cursor.execute(insert_query, (
                    a["id"], b["id"], a["station_name"], a["platform_number"], a["travel_date"],
                    keep["id"], move["id"], suggested_platform, estimated_delay, suggestion
                ))

                _log_conflict_history(
                    cursor, a, b, a["station_name"], a["platform_number"], a["travel_date"],
                    suggestion, history_outcome, estimated_delay
                )

                conflicts_found += 1

    conn.commit()
    cursor.close()
    conn.close()
    return conflicts_found


def get_all_conflicts(day=None, date=None):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT c.conflict_id, c.station_name, c.platform_number, c.travel_date,
               c.suggested_platform, c.estimated_delay, c.suggestion,
               t1.train_name AS train_1_name, t1.train_number AS train_1_number,
               t1.priority AS train_1_priority,
               t1.scheduled_arrival AS train_1_arrival, t1.scheduled_departure AS train_1_departure,
               t2.train_name AS train_2_name, t2.train_number AS train_2_number,
               t2.priority AS train_2_priority,
               t2.scheduled_arrival AS train_2_arrival, t2.scheduled_departure AS train_2_departure,
               k.train_name AS keep_train_name, k.train_number AS keep_train_number,
               m.train_name AS move_train_name, m.train_number AS move_train_number,
               m.id AS move_train_id
        FROM conflicts c
        JOIN trains t1 ON c.train_id_1 = t1.id
        JOIN trains t2 ON c.train_id_2 = t2.id
        JOIN trains k ON c.keep_train_id = k.id
        JOIN trains m ON c.reschedule_train_id = m.id
        WHERE 1=1
    """
    params = []
    if day:
        query += " AND DAYNAME(c.travel_date) = %s"
        params.append(day)
    if date:
        query += " AND c.travel_date = %s"
        params.append(date)
    query += " ORDER BY c.travel_date, c.station_name"

    cursor.execute(query, params)
    conflicts = cursor.fetchall()
    cursor.close()
    conn.close()
    return conflicts


def get_conflict_history(day=None, date=None):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = "SELECT * FROM conflict_history WHERE 1=1"
    params = []
    if day:
        query += " AND DAYNAME(travel_date) = %s"
        params.append(day)
    if date:
        query += " AND travel_date = %s"
        params.append(date)
    query += " ORDER BY logged_at DESC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def clear_all_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conflicts")
    cursor.execute("DELETE FROM trains WHERE is_base_schedule = FALSE")
    conn.commit()
    cursor.close()
    conn.close()
    detect_and_store_conflicts()


def clear_everything():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conflicts")
    cursor.execute("DELETE FROM trains")
    conn.commit()
    cursor.close()
    conn.close()
    detect_and_store_conflicts()


def apply_recommendation(conflict_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM conflicts WHERE conflict_id = %s", (conflict_id,))
    conflict = cursor.fetchone()

    if conflict and conflict["suggested_platform"] is not None:
        cursor.execute(
            "UPDATE trains SET platform_number = %s, status = %s, delay_minutes = 0 WHERE id = %s",
            (conflict["suggested_platform"], "On Time", conflict["reschedule_train_id"])
        )

        cursor.execute("SELECT train_number FROM trains WHERE id = %s", (conflict["keep_train_id"],))
        keep_num = cursor.fetchone()["train_number"]
        cursor.execute("SELECT train_number FROM trains WHERE id = %s", (conflict["reschedule_train_id"],))
        move_num = cursor.fetchone()["train_number"]

        cursor.execute("""
            UPDATE conflict_history SET outcome = 'Reassigned (Applied)'
            WHERE travel_date = %s
            AND ((train_1_number=%s AND train_2_number=%s) OR (train_1_number=%s AND train_2_number=%s))
        """, (conflict["travel_date"], keep_num, move_num, move_num, keep_num))

        conn.commit()

    cursor.close()
    conn.close()
    detect_and_store_conflicts()


import re


# Fallback list of major Indian cities/stations, used so the Ask chat can
# recognize a route even BEFORE it has ever been fetched into the DB --
# e.g. "Ahmedabad se Surat" works even if neither station exists in
# `trains` yet. Real DB station names (passed in separately) always take
# priority since they're matched first (longest-name-first sort).
KNOWN_CITY_FALLBACK = [
    "New Delhi", "Delhi", "Mumbai Central", "Mumbai", "Ahmedabad", "Surat",
    "Vadodara", "Rajkot", "Bhavnagar", "Jamnagar", "Gandhinagar", "Anand",
    "Bharuch", "Vapi", "Valsad", "Navsari", "Godhra", "Nadiad", "Mehsana",
    "Surendranagar", "Junagadh", "Porbandar", "Somnath", "Dwarka", "Udaipur",
    "Jodhpur", "Ajmer", "Kota", "Jaipur", "Pune", "Nagpur", "Nashik",
    "Bangalore", "Bengaluru", "Chennai", "Hyderabad", "Kolkata", "Lucknow",
    "Kanpur", "Varanasi", "Prayagraj", "Agra", "Patna", "Ranchi", "Bhopal",
    "Indore", "Gwalior", "Chandigarh", "Amritsar", "Ludhiana", "Jalandhar",
    "Goa", "Kochi", "Trivandrum", "Guwahati", "Bhubaneswar",
]


def extract_station_mentions(query_text, known_stations=None):
    """
    Finds station/city names mentioned in free text, in the order they
    appear. Checks real DB stations first (passed in via known_stations)
    plus the fallback city list above -- so a route works in chat even if
    it hasn't been fetched yet. Longest names are matched first so
    "New Delhi" wins over a bare "Delhi" contained inside it.
    """
    ql = query_text.lower()
    all_candidates = list(dict.fromkeys((known_stations or []) + KNOWN_CITY_FALLBACK))
    all_candidates.sort(key=len, reverse=True)

    found = []
    used_spans = []
    for name in all_candidates:
        nl = name.lower()
        if len(nl) < 3:
            continue
        idx = ql.find(nl)
        if idx == -1:
            continue
        overlap = any(not (idx + len(nl) <= s or idx >= e) for s, e in used_spans)
        if overlap:
            continue
        found.append((idx, name))
        used_spans.append((idx, idx + len(nl)))

    found.sort(key=lambda x: x[0])
    ordered = []
    for _, name in found:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _fmt_duration(td):
    total_min = int(td.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def smart_query_search(query_text):
    q = query_text.lower().strip()

    greetings = ["hello", "hi", "hey", "good morning", "good afternoon",
                 "good evening", "how are you", "thanks", "thank you", "bye"]
    if any(q == g or q.startswith(g + " ") or q.startswith(g + "!") for g in greetings):
        return {
            "result_type": "greeting", "results": [],
            "message": "Hi! Try things like \"how many trains are delayed\", \"Ahmedabad trains\", or \"Monday conflicts\"."
        }

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT DISTINCT station_name FROM trains")
    db_stations = [r["station_name"] for r in cursor.fetchall()]
    mentioned = extract_station_mentions(query_text, db_stations)
    matched_station = mentioned[0] if mentioned else None

    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    matched_day = next((d.capitalize() for d in days if d in q), None)

    status_map = {
        "delayed": "Delayed", "on time": "On Time", "departed": "Departed",
        "arriving": "Arriving Soon", "reassign": "Reassignment Suggested",
    }
    matched_status = next((v for k, v in status_map.items() if k in q), None)

    matched_hour = None
    time_match = re.search(r'(\d{1,2})\s*(am|pm)', q)
    if time_match:
        hour = int(time_match.group(1)) % 12
        if time_match.group(2) == "pm":
            hour += 12
        matched_hour = hour

    is_conflict_query = "conflict" in q
    is_count_query = bool(re.search(r'\bkitn[ie]\b', q)) or "how many" in q or "count" in q

    # ---- Count query ("how many trains ...") ----
    if is_count_query:
        query = "SELECT * FROM trains WHERE 1=1"
        params = []
        if matched_station:
            query += " AND LOWER(station_name) LIKE LOWER(%s)"
            params.append(f"%{matched_station}%")
        if matched_day:
            query += " AND DAYNAME(travel_date) = %s"
            params.append(matched_day)
        if matched_status:
            query += " AND status = %s"
            params.append(matched_status)
        query += " ORDER BY travel_date, scheduled_arrival"
        cursor.execute(query, params)
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        parts = [p for p in [matched_station, matched_day, matched_status] if p]
        context = " ".join(parts) if parts else "Total"
        return {
            "result_type": "count", "results": results[:10],
            "message": f"{context}: {len(results)} train(s) found.",
            "count": len(results),
        }

    has_any_filter = any([matched_station, matched_day, matched_status,
                           matched_hour is not None, is_conflict_query])

    if not has_any_filter:
        cursor.close()
        conn.close()
        return {
            "result_type": "no_match", "results": [],
            "message": "I couldn't quite understand that. Try mentioning a station name, a day (e.g. Monday), a time (e.g. \"10 AM\"), or a status (e.g. \"delayed\")."
        }

    if is_conflict_query:
        query = """
            SELECT c.*, t1.train_name AS train_1_name, t1.train_number AS train_1_number,
                   t2.train_name AS train_2_name, t2.train_number AS train_2_number
            FROM conflicts c
            JOIN trains t1 ON c.train_id_1 = t1.id
            JOIN trains t2 ON c.train_id_2 = t2.id
            WHERE 1=1
        """
        params = []
        if matched_station:
            query += " AND c.station_name = %s"
            params.append(matched_station)
        if matched_day:
            query += " AND DAYNAME(c.travel_date) = %s"
            params.append(matched_day)
        cursor.execute(query, params)
        results = cursor.fetchall()
        result_type = "conflicts"
    else:
        query = "SELECT * FROM trains WHERE 1=1"
        params = []
        if matched_station:
            query += " AND station_name = %s"
            params.append(matched_station)
        if matched_day:
            query += " AND DAYNAME(travel_date) = %s"
            params.append(matched_day)
        if matched_status:
            query += " AND status = %s"
            params.append(matched_status)
        if matched_hour is not None:
            query += " AND HOUR(scheduled_arrival) = %s"
            params.append(matched_hour)
        query += " ORDER BY travel_date, scheduled_arrival"
        cursor.execute(query, params)
        results = cursor.fetchall()
        result_type = "trains"

    cursor.close()
    conn.close()

    message = None
    if not results:
        message = "No trains matched that -- try a different station, day, or time."
    else:
        message = f"Found {len(results)} result(s)."

    return {
        "result_type": result_type,
        "results": results[:10],
        "matched_station": matched_station,
        "matched_day": matched_day,
        "matched_status": matched_status,
        "matched_hour": matched_hour,
        "message": message,
    }


def get_station_risk_summary():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT station_name, COUNT(*) AS cnt FROM trains GROUP BY station_name ORDER BY cnt DESC")
    station_counts = cursor.fetchall()

    cursor.execute("SELECT station_name, COUNT(*) AS cnt FROM conflict_history GROUP BY station_name")
    history_counts = {r["station_name"]: r["cnt"] for r in cursor.fetchall()}

    cursor.close()
    conn.close()

    station_risk = []
    for row in station_counts:
        station = row["station_name"]
        total = row["cnt"]
        conflict_cnt = history_counts.get(station, 0)
        ratio = conflict_cnt / total if total else 0
        if conflict_cnt >= 3 or ratio >= 0.5:
            risk = "High"
        elif conflict_cnt >= 1 or ratio >= 0.2:
            risk = "Medium"
        else:
            risk = "Low"
        station_risk.append({
            "station_name": station, "total_trains": total,
            "conflict_count": conflict_cnt, "risk": risk
        })

    return station_risk


def get_analytics_data():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS cnt FROM trains")
    total_trains = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) AS cnt FROM conflicts")
    total_conflicts = cursor.fetchone()["cnt"]

    cursor.execute("SELECT AVG(estimated_delay) AS avg_delay FROM conflicts WHERE estimated_delay > 0")
    row = cursor.fetchone()
    avg_delay = round(row["avg_delay"], 1) if row["avg_delay"] is not None else 0

    cursor.execute("""
        SELECT station_name, COUNT(*) AS cnt FROM trains
        GROUP BY station_name ORDER BY cnt DESC
    """)
    station_counts = cursor.fetchall()
    busiest_station = station_counts[0]["station_name"] if station_counts else "—"

    cursor.execute("SELECT priority, COUNT(*) AS cnt FROM trains GROUP BY priority")
    priority_counts = cursor.fetchall()

    cursor.execute("SELECT status, COUNT(*) AS cnt FROM trains GROUP BY status")
    status_counts = cursor.fetchall()

    cursor.execute("""
        SELECT station_name, COUNT(*) AS cnt FROM conflicts
        GROUP BY station_name ORDER BY cnt DESC
    """)
    conflicts_by_station = cursor.fetchall()

    cursor.close()
    conn.close()

    return {
        "total_trains": total_trains,
        "total_conflicts": total_conflicts,
        "avg_delay": avg_delay,
        "busiest_station": busiest_station,
        "station_counts": station_counts,
        "priority_counts": priority_counts,
        "status_counts": status_counts,
        "conflicts_by_station": conflicts_by_station,
    }


import requests
import time


def _geocode_station_live(station_name):
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{station_name} railway station India", "format": "json", "limit": 1},
            headers={"User-Agent": "RailMitra-Student-Project/1.0"},
            timeout=5
        )
        results = response.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return None


def get_station_coordinates_auto(station_name, live_geocode_budget=None):
    coords = get_station_coordinates(station_name)
    if coords:
        return coords

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT lat, lng FROM station_geocode_cache WHERE station_name = %s", (station_name,))
    cached = cursor.fetchone()
    if cached:
        cursor.close()
        conn.close()
        return (float(cached["lat"]), float(cached["lng"]))

    if live_geocode_budget is not None:
        if live_geocode_budget[0] <= 0:
            cursor.close()
            conn.close()
            return None
        live_geocode_budget[0] -= 1

    geocoded = _geocode_station_live(station_name)
    if geocoded:
        try:
            cursor.execute(
                "INSERT IGNORE INTO station_geocode_cache (station_name, lat, lng) VALUES (%s, %s, %s)",
                (station_name, geocoded[0], geocoded[1])
            )
            conn.commit()
        except Exception:
            conn.rollback()

    cursor.close()
    conn.close()
    return geocoded


def get_train_live_tracking(train_number, travel_date):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM trains WHERE train_number = %s AND travel_date = %s ORDER BY scheduled_arrival",
        (train_number, travel_date)
    )
    stops = cursor.fetchall()
    cursor.close()
    conn.close()

    if not stops:
        return None

    def combine(date_val, time_val):
        if isinstance(time_val, timedelta):
            t = time_val
        else:
            h, m, s = str(time_val).split(":")
            t = timedelta(hours=int(h), minutes=int(m), seconds=int(s))
        return datetime.combine(date_val, datetime.min.time()) + t

    stop_times = []
    for s in stops:
        coords = get_station_coordinates_auto(s["station_name"])
        stop_times.append({
            "station_name": s["station_name"],
            "platform_number": s["platform_number"],
            "arrival": combine(s["travel_date"], s["scheduled_arrival"]),
            "departure": combine(s["travel_date"], s["scheduled_departure"]),
            "lat": coords[0] if coords else None,
            "lng": coords[1] if coords else None,
            "status": s["status"],
        })

    now = datetime.now()
    n = len(stop_times)
    current_status = ""
    current_index = 0
    progress_fraction = 0.0
    current_lat, current_lng = stop_times[0]["lat"], stop_times[0]["lng"]

    if now < stop_times[0]["arrival"]:
        current_status = f"Yet to depart from {stop_times[0]['station_name']}"
    elif now > stop_times[-1]["departure"]:
        current_status = f"Journey completed at {stop_times[-1]['station_name']}"
        current_index = n - 1
        progress_fraction = 1.0
        current_lat, current_lng = stop_times[-1]["lat"], stop_times[-1]["lng"]
    else:
        found = False
        for i in range(n):
            s = stop_times[i]
            if s["arrival"] <= now <= s["departure"]:
                current_status = f"At {s['station_name']} (halted at Platform {s['platform_number']})"
                current_index = i
                current_lat, current_lng = s["lat"], s["lng"]
                progress_fraction = i / max(n - 1, 1)
                found = True
                break
            if i < n - 1 and s["departure"] < now < stop_times[i + 1]["arrival"]:
                total_gap = (stop_times[i + 1]["arrival"] - s["departure"]).total_seconds()
                elapsed = (now - s["departure"]).total_seconds()
                frac = elapsed / total_gap if total_gap > 0 else 0
                current_status = f"En route: {s['station_name']} → {stop_times[i + 1]['station_name']}"
                current_index = i
                progress_fraction = (i + frac) / max(n - 1, 1)
                if s["lat"] and stop_times[i + 1]["lat"]:
                    current_lat = s["lat"] + (stop_times[i + 1]["lat"] - s["lat"]) * frac
                    current_lng = s["lng"] + (stop_times[i + 1]["lng"] - s["lng"]) * frac
                found = True
                break
        if not found:
            current_status = "Status unavailable"

    return {
        "train_number": train_number,
        "train_name": stops[0]["train_name"],
        "stops": stop_times,
        "current_status": current_status,
        "current_index": current_index,
        "current_lat": current_lat,
        "current_lng": current_lng,
        "stations_remaining": max(n - 1 - current_index, 0),
        "progress_fraction": round(progress_fraction * 100, 1),
    }


def find_duplicate_trains(rows):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    duplicates = []
    for row in rows:
        cursor.execute(
            "SELECT id FROM trains WHERE train_number = %s AND station_name = %s AND travel_date = %s",
            (row["train_number"], row["station_name"], row["travel_date"])
        )
        if cursor.fetchone():
            duplicates.append(f"Train {row['train_number']} at {row['station_name']} on {row['travel_date']}")
    cursor.close()
    conn.close()
    return duplicates


_WEEKDAY_INDEX_CACHE = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def is_fetch_cached(fetch_key):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM real_trains_master WHERE fetch_key = %s LIMIT 1", (fetch_key,))
    found = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return found


def store_to_master_cache(fetch_key, rows):
    if not rows:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        INSERT IGNORE INTO real_trains_master
        (fetch_key, train_number, train_name, station_name, source_station,
         destination_station, scheduled_arrival, scheduled_departure, run_day)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    for row in rows:
        cursor.execute(query, (
            fetch_key, row["train_number"], row["train_name"], row["station_name"],
            row["source_station"], row["destination_station"],
            row["scheduled_arrival"], row["scheduled_departure"], row.get("run_day"),
        ))
    conn.commit()
    cursor.close()
    conn.close()


def get_cached_rows_for_key(fetch_key):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM real_trains_master WHERE fetch_key = %s", (fetch_key,))
    cached = cursor.fetchall()
    cursor.close()
    conn.close()

    today = date_cls.today()
    rows = []
    for c in cached:
        target = _WEEKDAY_INDEX_CACHE.get(c["run_day"])
        if target is None:
            continue
        delta = (target - today.weekday()) % 7
        travel_date = today + timedelta(days=delta)
        rows.append({
            "train_number": c["train_number"],
            "train_name": c["train_name"],
            "station_name": c["station_name"],
            "source_station": c["source_station"],
            "destination_station": c["destination_station"],
            "scheduled_arrival": str(c["scheduled_arrival"]),
            "scheduled_departure": str(c["scheduled_departure"]),
            "travel_date": travel_date.strftime("%Y-%m-%d"),
        })
    return rows


def get_cached_station_code(station_name):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT code FROM station_code_cache WHERE station_name = %s", (station_name,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row["code"] if row else None


def store_station_code(station_name, code):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT IGNORE INTO station_code_cache (station_name, code) VALUES (%s, %s)",
            (station_name, code)
        )
        conn.commit()
    except Exception:
        conn.rollback()
    cursor.close()
    conn.close()


def _time_to_pct(time_val):
    """Converts a TIME value (HH:MM:SS string or timedelta) into a 0-100 position along a 24-hour axis."""
    if isinstance(time_val, timedelta):
        seconds = time_val.total_seconds() % 86400
    else:
        h, m, s = str(time_val).split(":")
        seconds = int(h) * 3600 + int(m) * 60 + int(s)
    return round(seconds / 86400 * 100, 2)


def get_distinct_travel_dates():
    """All dates that currently have trains -- used to populate the Platform Timeline's date selector."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT DISTINCT travel_date FROM trains ORDER BY travel_date")
    dates = [str(r["travel_date"]) for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return dates


def get_platform_timeline_data(travel_date=None):
    """
    Builds a Gantt-style view: every station -> every platform -> every train
    on it that day, each positioned on a 0-24hr axis, with conflicted trains
    flagged (reusing the SAME conflicts already detected and stored by
    detect_and_store_conflicts() -- no separate overlap logic here).
    Reads live from the `trains` table -- whatever is currently there
    (imported, added, or fetched from RailRadar) shows up automatically,
    and disappears automatically once cleared/deleted. No external calls.
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if travel_date:
        cursor.execute(
            "SELECT * FROM trains WHERE travel_date = %s ORDER BY station_name, platform_number, scheduled_arrival",
            (travel_date,)
        )
    else:
        cursor.execute("SELECT * FROM trains ORDER BY station_name, platform_number, scheduled_arrival")
    trains = cursor.fetchall()

    cursor.execute("SELECT train_id_1, train_id_2 FROM conflicts")
    conflicted_ids = set()
    for r in cursor.fetchall():
        conflicted_ids.add(r["train_id_1"])
        conflicted_ids.add(r["train_id_2"])

    cursor.close()
    conn.close()

    grouped = {}
    for t in trains:
        station = t["station_name"]
        platform = t["platform_number"]
        start_pct = _time_to_pct(t["scheduled_arrival"])
        end_pct = _time_to_pct(t["scheduled_departure"])
        if end_pct <= start_pct:
            end_pct = min(start_pct + 1.5, 100)

        grouped.setdefault(station, {}).setdefault(platform, []).append({
            "id": t["id"],
            "train_number": t["train_number"],
            "train_name": t["train_name"],
            "arrival": str(t["scheduled_arrival"])[:5],
            "departure": str(t["scheduled_departure"])[:5],
            "status": t["status"],
            "start_pct": start_pct,
            "width_pct": round(end_pct - start_pct, 2),
            "conflicted": t["id"] in conflicted_ids,
        })

    return grouped


def get_operational_analytics():
    """
    Action-oriented analytics for an admin actively managing conflicts --
    NOT historical charts. Everything here reads from the LIVE `trains`
    and `conflicts` tables only (never conflict_history, which is a
    permanent audit log), so clearing the schedule (Clear All / Clear
    Everything) makes every number here go back to zero/empty too.
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS cnt FROM trains")
    total_trains = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) AS cnt FROM conflicts")
    total_conflicts = cursor.fetchone()["cnt"]

    cursor.execute("SELECT AVG(estimated_delay) AS avg_delay FROM conflicts WHERE estimated_delay > 0")
    row = cursor.fetchone()
    avg_delay = round(row["avg_delay"], 1) if row["avg_delay"] is not None else 0

    cursor.execute("SELECT station_name, COUNT(*) AS cnt FROM trains GROUP BY station_name ORDER BY cnt DESC LIMIT 1")
    row = cursor.fetchone()
    busiest_station = row["station_name"] if row else "—"

    # 1. Needs Attention Now -- conflicts with NO alternate platform found
    # (these are the ones that will actually cause a delay), priority-sorted
    cursor.execute("""
        SELECT c.conflict_id, c.station_name, c.platform_number, c.travel_date,
               c.estimated_delay, c.suggestion,
               m.train_name AS move_train_name, m.train_number AS move_train_number,
               m.priority AS move_priority
        FROM conflicts c
        JOIN trains m ON c.reschedule_train_id = m.id
        WHERE c.suggested_platform IS NULL
        ORDER BY FIELD(m.priority, 'High', 'Medium', 'Low'), c.estimated_delay DESC
        LIMIT 10
    """)
    needs_attention = cursor.fetchall()

    # 2. Resolution breakdown -- how many conflicts got auto-resolved via a
    # platform switch vs how many genuinely need a delay
    cursor.execute("SELECT COUNT(*) AS cnt FROM conflicts WHERE suggested_platform IS NOT NULL")
    auto_resolved = cursor.fetchone()["cnt"]
    cursor.execute("SELECT COUNT(*) AS cnt FROM conflicts WHERE suggested_platform IS NULL")
    needs_delay = cursor.fetchone()["cnt"]
    max_resolution = max(auto_resolved, needs_delay, 1)

    # 3. Trouble spots -- which station+platform combo has the most
    # conflicts RIGHT NOW (not historically)
    cursor.execute("""
        SELECT station_name, platform_number, COUNT(*) AS cnt
        FROM conflicts
        GROUP BY station_name, platform_number
        ORDER BY cnt DESC
        LIMIT 6
    """)
    trouble_spots = cursor.fetchall()
    max_trouble = trouble_spots[0]["cnt"] if trouble_spots else 1

    cursor.close()
    conn.close()

    return {
        "total_trains": total_trains,
        "total_conflicts": total_conflicts,
        "avg_delay": avg_delay,
        "busiest_station": busiest_station,
        "needs_attention": needs_attention,
        "auto_resolved": auto_resolved,
        "needs_delay": needs_delay,
        "max_resolution": max_resolution,
        "trouble_spots": trouble_spots,
        "max_trouble": max_trouble,
    }


def clear_fetched_data():
    """
    Clears only REAL trains fetched from RailRadar (is_base_schedule = TRUE)
    from the live `trains` table. Imported/manually-added trains are
    untouched, and the PERMANENT cache (real_trains_master) is untouched
    too -- so the same station/route can be re-fetched instantly later
    without hitting RailRadar again.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conflicts")
    cursor.execute("DELETE FROM trains WHERE is_base_schedule = TRUE")
    conn.commit()
    cursor.close()
    conn.close()
    detect_and_store_conflicts()
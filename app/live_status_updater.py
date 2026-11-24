from datetime import datetime, timedelta, date
from app.database import get_db_connection


def _to_timedelta(value):
    if isinstance(value, timedelta):
        return value
    h, m, s = str(value).split(":")
    return timedelta(hours=int(h), minutes=int(m), seconds=int(s))


def update_live_statuses():
    """
    Runs automatically in the background every 60 seconds.
    Compares each TODAY-scheduled train's timing against the real current clock
    and updates its status -- without touching trains already flagged
    'Delayed' or 'Reassignment Suggested' by the conflict detection engine
    (that logic always takes priority over the time-based simulation).
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    today = date.today()
    now = datetime.now()
    now_as_timedelta = timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)

    cursor.execute("SELECT * FROM trains WHERE travel_date = %s", (today,))
    todays_trains = cursor.fetchall()

    for t in todays_trains:
        if t["status"] in ("Delayed", "Reassignment Suggested"):
            continue  # conflict engine already decided this train's fate

        departure = _to_timedelta(t["scheduled_departure"])
        arrival = _to_timedelta(t["scheduled_arrival"])

        if now_as_timedelta > departure:
            new_status = "Departed"
        elif arrival - timedelta(minutes=15) <= now_as_timedelta <= arrival:
            new_status = "Arriving Soon"
        else:
            new_status = "On Time"

        if new_status != t["status"]:
            cursor.execute(
                "UPDATE trains SET status = %s WHERE id = %s",
                (new_status, t["id"])
            )

    conn.commit()
    cursor.close()
    conn.close()
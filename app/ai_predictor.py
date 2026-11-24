import joblib
import os
from datetime import date

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "delay_model.pkl")
_bundle = joblib.load(_MODEL_PATH)
_model = _bundle["model"]
_station_encoder = _bundle["station_encoder"]


def predict_delay(station_name: str, platform_number: int, travel_date, arrival_time) -> int:
    """
    Predicts expected delay (in minutes) for a conflict using the trained ML model.
    Falls back to a safe default if the station wasn't seen during training.
    """
    try:
        station_encoded = _station_encoder.transform([station_name])[0]
    except ValueError:
        station_encoded = 0

    if isinstance(travel_date, date):
        day_of_week = travel_date.weekday()
    else:
        day_of_week = 0

    hour_of_day = int(str(arrival_time).split(":")[0])

    features = [[station_encoded, platform_number, hour_of_day, day_of_week]]
    predicted_minutes = _model.predict(features)[0]
    return round(predicted_minutes)
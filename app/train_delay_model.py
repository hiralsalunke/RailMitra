import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
import joblib

df = pd.read_csv("historical_delays.csv")

# encode station name (text) into a number the model can use
station_encoder = LabelEncoder()
df["station_encoded"] = station_encoder.fit_transform(df["station_name"])

features = ["station_encoded", "platform_number", "hour_of_day", "day_of_week"]
X = df[features]
y = df["delay_minutes"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

model = RandomForestRegressor(n_estimators=150, max_depth=6, random_state=42)
model.fit(X_train, y_train)

preds = model.predict(X_test)
mae = mean_absolute_error(y_test, preds)
print(f"Model trained. Mean Absolute Error on test data: {mae:.2f} minutes")

# save both the model and the station encoder together
joblib.dump({"model": model, "station_encoder": station_encoder}, "delay_model.pkl")
print("Saved delay_model.pkl")
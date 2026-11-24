import pandas as pd
import numpy as np

np.random.seed(42)

stations = ["Ahmedabad Junction", "Mumbai Central", "Delhi Junction", "Surat", "Vadodara Junction"]
n = 600

rows = []
for _ in range(n):
    station = np.random.choice(stations)
    platform = np.random.randint(1, 7)
    hour = np.random.randint(0, 24)
    day_of_week = np.random.randint(0, 7)  # 0=Monday

    # realistic-ish pattern: peak hours (7-10, 17-20) + busy platforms (1-3) + weekends -> more delay
    base_delay = 5
    if hour in [7, 8, 9, 17, 18, 19, 20]:
        base_delay += 15
    if platform <= 3:
        base_delay += 8
    if day_of_week >= 5:  # weekend, slightly less traffic
        base_delay -= 5

    noise = np.random.normal(0, 6)
    delay = max(0, base_delay + noise)

    rows.append({
        "station_name": station,
        "platform_number": platform,
        "hour_of_day": hour,
        "day_of_week": day_of_week,
        "delay_minutes": round(delay, 1)
    })

df = pd.DataFrame(rows)
df.to_csv("historical_delays.csv", index=False)
print(df.head())
print(f"\nSaved {len(df)} rows to historical_delays.csv")
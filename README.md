# RailMitra – Smart Scheduling and Conflict Detection System

A web-based system that helps railway station staff find platform/time
conflicts between trains and resolve them. Passengers can also check live
train status without logging in.

## Features
- Import schedules from Excel, manual entry, or Auto-Schedule Generator
- Automatic platform/time conflict detection
- Priority rules based on Indian Railways precedence
- Delay prediction using Random Forest (MAE about 4.5 minutes)
- Live train status using RailRadar API (with schedule-based fallback)
- Analytics dashboard with Station Risk Score
- Conflict history and Excel reports
- QR-based train tracking
- Secure admin login (bcrypt, login lockout)

## Tech Stack
Python, FastAPI, MySQL, scikit-learn, Jinja2, HTML, CSS, JavaScript, Bokeh

## How to Run
1. Install Python and MySQL, then create the database: `CREATE DATABASE railmitra;`
2. Create a virtual environment and install packages:
   `pip install -r requirements.txt`
3. Create the tables and add an admin user in MySQL.
4. Create a `.env` file in the project folder with these values:
      SECRET_KEY=your_secret_key
   RAILRADAR_API_KEY=your_api_key
   PUBLIC_BASE_URL=http://localhost:8000
   DB_PASSWORD=your_mysql_password
5. Start the app: `uvicorn main:app --reload --host 0.0.0.0`
6. Open the app in your browser and log in as admin.

## Author
Hiral Salunke

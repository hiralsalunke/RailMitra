# RailMitra – Smart Scheduling and Conflict Detection System

RailMitra is a web-based system that helps railway station staff find platform and time conflicts between trains and resolve them. Passengers can also check live train status and track trains using a QR code, without logging in.

This is my M.Sc. Computer Science major project at P P Savani University.

## Features

- Import train schedules from Excel, add trains manually, or use the Auto-Schedule Generator
- Automatic platform and time conflict detection
- Smart recommendations: alternate platform or hold the lower-priority train
- Priority rules based on Indian Railways precedence
- Delay prediction using a Random Forest model (MAE about 4.5 minutes)
- Live train status using the RailRadar API, with schedule-based fallback
- Analytics dashboard with Station Risk Score and Bokeh charts
- Conflict history with Excel export
- QR code based train tracking
- Secure admin login with bcrypt, sessions and login lockout

## Screenshots

### Admin Login
![Admin Login](images/login.png)

### Admin Dashboard
![Dashboard](images/dashboard.png)

### Conflict Detection and Recommendation
![Conflict Detection](images/conflict-detection.png)

### Analytics Dashboard
![Analytics](images/analytics.png)

### Track Train
![Track Train](images/track-train.png)

### Track with QR Code
![QR Tracking](images/track-qr.png)

## System Architecture

![Architecture](images/architecture.png)

## Database (ER Diagram)

![ER Diagram](images/er-diagram.png)

## Tech Stack

- Backend: Python, FastAPI, Uvicorn
- Database: MySQL
- Machine Learning: scikit-learn (Random Forest), pandas, joblib
- Charts: Bokeh
- Frontend: HTML, CSS, JavaScript, Jinja2
- Background jobs: APScheduler
- Security: bcrypt, python-dotenv
- External services: RailRadar API, OpenStreetMap Nominatim

## How to Run

1. Install Python and MySQL, then create the database: CREATE DATABASE railmitra;
2. Create a virtual environment and install packages: pip install -r requirements.txt
3. Create the tables in MySQL and add an admin user with a bcrypt-hashed password.
4. Create a .env file in the project folder with these values: SECRET_KEY, RAILRADAR_API_KEY, PUBLIC_BASE_URL and DB_PASSWORD.
5. Start the app: uvicorn main:app --reload --host 0.0.0.0
6. Open the app in your browser and log in as admin.

## Author

Hiral Dipak Salunke
M.Sc. Computer Science, P P Savani University

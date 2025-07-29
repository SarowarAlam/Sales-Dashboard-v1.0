# Sales-Dashboard Version 1.0
This project is a comprehensive solution for sales teams to track call data in a simple Google Sheet and visualize it in a real-time dashboard. It sets up an automated data pipeline that synchronizes Google Sheet data with a PostgreSQL database and serves an interactive dashboard built with Streamlit. It is made officially for Next Ventures.


### Sales Call Dashboard with Automated Google Sheets Sync

[![Project Status: Active](https://img.shields.io/badge/Status-Active-brightgreen.svg)](https://github.com/your-username/your-repo)
[![Python Version](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/downloads/)

#### Introduction

This project provides a robust, end-to-end solution for sales teams to visualize call data in real-time. By simply entering and updating call records in a Google Sheet, the data is automatically synchronized with a PostgreSQL database and immediately reflected in an interactive Streamlit dashboard. This eliminates manual data transfers and provides a single source of truth for all sales metrics.

#### Key Features

* **Automated, Event-Driven Sync:** The system is triggered automatically by an `onEdit` event in the Google Sheet, initiating a webhook to the backend.
* **Full Data Synchronization:** The Python backend uses a "truncate-and-load" strategy, ensuring the PostgreSQL database is an exact mirror of the Google Sheet's data. This handles additions, updates, and deletions seamlessly.
* **Secure & Robust Backend:** A lightweight Flask server acts as a secure webhook listener, validating requests with a secret key before executing the data sync pipeline as a background process.
* **Interactive Streamlit Dashboard:** The dashboard (`app_v1.py`) connects directly to PostgreSQL to provide real-time visualizations, key performance indicators (KPIs), and filters for agents, countries, and call statuses.
* **Professional Analytics:** The dashboard includes advanced data processing to calculate metrics like total call counts, answered rates, sales pipeline distribution, and agent performance from the raw data.

#### How It Works: The Architecture

The project's workflow is powered by a multi-component architecture that handles the entire data lifecycle.

1.  **Data Input:** Users update call records in a Google Sheet.
2.  **Webhook Trigger:** A Google Apps Script `onEdit` trigger detects the change and sends a webhook (an HTTP POST request) to a publicly accessible server.
3.  **Webhook Listener:** The Flask server (`webhook_server.py`) receives the webhook.
4.  **Data Sync Pipeline:** The Flask server executes `google_sheets_to_postgres_sync.py`, which connects to Google Sheets, truncates the `sales_data` table in a PostgreSQL database, and inserts the fresh data.
5.  **PostgreSQL Database:** The `sales_data` table acts as the single source of truth, storing the most up-to-date information.
6.  **Streamlit Dashboard:** The `app_v1.py` dashboard connects to the PostgreSQL database, retrieves the latest data, and displays the dashboard visualizations. It uses a caching mechanism to ensure fast performance.

#### Getting Started

Follow these steps to set up and run the project locally.

##### Prerequisites

* **Python 3.9+**
* **PostgreSQL:** A running PostgreSQL database server.
* **Google Cloud Service Account:** A `dashboard-sales-team-26f7b3ff3eb2.json` service account file with editor access to your Google Sheet.
* **ngrok:** For creating a public URL to your local server.

##### Installation

1.  Clone this repository:
    ```bash
    git clone [https://github.com/your-username/your-repo.git](https://github.com/your-username/your-repo.git)
    cd sales-call-dashboard
    ```
2.  Create a virtual environment and install dependencies:
    ```bash
    python -m venv .venv
    .venv\Scripts\activate  # On Windows
    # source .venv/bin/activate  # On macOS/Linux
    pip install -r requirements.txt
    ```

##### Configuration

1.  **Google Sheet Credentials:** Place your `dashboard-sales-team-26f7b3ff3eb2.json` file in the root directory of the project.
2.  **Environment Variables:** Create a `.env` file in the project root to store sensitive information.
    ```ini
    PG_DBNAME="sales_dashboard_db"
    PG_USER="postgres"
    PG_PASSWORD="YourPostgresPassword"
    PG_HOST="localhost"
    PG_PORT="5432"
    WEBHOOK_SECRET_KEY="a_strong_secret_key"
    ```
3.  **Streamlit Secrets:** Create a `.streamlit` folder and a `secrets.toml` file inside it to securely store the database credentials for the dashboard.
    ```toml
    # .streamlit/secrets.toml
    [postgres]
    dbname = "sales_dashboard_db"
    user = "postgres"
    password = "YourPostgresPassword"
    host = "localhost"
    port = "5432"
    ```

##### Running the Application

You will need to run three components in separate terminal windows.

1.  **Terminal 1: Run the Flask Webhook Server**
    ```bash
    flask --app webhook_server:app run --port 5000
    ```
2.  **Terminal 2: Start the ngrok Tunnel**
    ```bash
    ngrok http 5000
    ```
    Copy the `https://` forwarding URL that appears in this terminal.
3.  **Terminal 3: Run the Streamlit Dashboard**
    ```bash
    streamlit run app_v1.py
    ```

##### Triggering the Sync from Google Apps Script

1.  Open your Google Sheet and go to **Extensions > Apps Script**.
2.  Update the `WEBHOOK_URL` constant with the `https://` URL from your ngrok terminal, appending `/sync-sheets`. Ensure `SECRET_KEY` matches your `.env` file.
    ```javascript
    const WEBHOOK_URL = "[https://your-ngrok-url.ngrok-free.app/sync-sheets](https://your-ngrok-url.ngrok-free.app/sync-sheets)";
    const SECRET_KEY = "a_strong_secret_key";
    ```
3.  Save the script and set up an `onEdit` trigger if it doesn't already exist.
4.  Make an edit in the Google Sheet to test the full sync workflow.

#### Project Structure

sales-call-dashboard/
├── .env                          # Environment variables for Python scripts
├── .streamlit/
│   └── secrets.toml              # Secure credentials for Streamlit
├── app_v1.py                     # Streamlit dashboard application
├── dashboard-sales-team-26f7b3ff3eb2.json # Google service account key
├── google_sheets_to_postgres_sync.py  # Data sync pipeline script
├── requirements.txt              # Project dependencies
└── webhook_server.py             # Flask webhook listener

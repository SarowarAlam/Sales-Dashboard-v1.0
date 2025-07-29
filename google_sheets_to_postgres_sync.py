import psycopg2.extras
import gspread
import pandas as pd
import os
from datetime import datetime
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Google Sheets and PostgreSQL connection setup
SERVICE_ACCOUNT_FILE = 'dashboard-sales-team-26f7b3ff3eb2.json'

# Load service account info directly from the JSON file
try:
    with open(SERVICE_ACCOUNT_FILE, 'r') as f:
        GCP_SERVICE_ACCOUNT_INFO = json.load(f)
except FileNotFoundError:
    print(f"Error: Service account file '{SERVICE_ACCOUNT_FILE}' not found. Please ensure it's in the same directory.")
    exit()
except json.JSONDecodeError as e:
    print(f"Error decoding JSON from service account file: {e}")
    exit()

PG_DBNAME = os.getenv("PG_DBNAME", "sales_dashboard_db")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "Your Password")
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")

GOOGLE_SHEET_NAME = "Your Sheet"
WORKSHEET_NAME = "Your Worksheet"

# Define expected DB columns
expected_db_columns = [
    'name', 'email', 'number', 'country_name', 'remarks', 'agent', 'first_call_date',
    'status', 'notes_from_call', 'post_call_email', 'tags', 'interested_category', 'sales_status',
    'sales_amount', 'next_follow_up_time', 'next_follow_up_date', 'Calling_Stamp', 'Signup_Date'
]

def get_gspread_client():
    """Initializes and returns a gspread client."""
    try:
        gc = gspread.service_account_from_dict(GCP_SERVICE_ACCOUNT_INFO)
        return gc
    except Exception as e:
        print(f"Error initializing Google Sheets client: {e}")
        return None

def fetch_data_from_gsheets(gc, spreadsheet_name, worksheet_name):
    """Fetches data from Google Sheet and returns as a Pandas DataFrame."""
    try:
        sh = gc.open(spreadsheet_name)
        worksheet = sh.worksheet(worksheet_name)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)

        # Clean and process data
        df.columns = df.columns.str.lower().str.strip().str.replace(' ', '_').str.replace('&', 'and')

        if 'customer_name' in df.columns:
            df.rename(columns={'customer_name': 'name'}, inplace=True)

        if 'agent_name' in df.columns:
            df.rename(columns={'agent_name': 'agent'}, inplace=True)

        if 'sales_amount' in df.columns:
            df['sales_amount'] = df['sales_amount'].astype(str).str.replace(r'[$,]', '', regex=True)
            df['sales_amount'] = pd.to_numeric(df['sales_amount'], errors='coerce').fillna(0)

        date_cols_from_sheet = [
            'first_call_date', 'next_follow_up_date'
        ]
        for col in date_cols_from_sheet:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

        # Filter out rows with null or empty email BEFORE reordering
        initial_rows = len(df)
        df = df[df['email'].notnull() & (df['email'] != '')]
        filtered_rows = len(df)
        if initial_rows != filtered_rows:
            print(f"[{datetime.now()}] INFO: Filtered out {initial_rows - filtered_rows} rows due to null or empty emails.")

        # Reorder columns to match PostgreSQL schema
        df_reordered = pd.DataFrame(columns=expected_db_columns)
        for col in expected_db_columns:
            if col in df.columns:
                df_reordered[col] = df[col]
            else:
                if col == 'sales_amount':
                    df_reordered[col] = 0.0
                elif 'date' in col:
                    df_reordered[col] = pd.NaT
                else:
                    df_reordered[col] = ''
        print(f"[{datetime.now()}] INFO: Dataframe prepared with {len(df_reordered)} rows for insertion.")

        return df_reordered

    except gspread.exceptions.SpreadsheetNotFound:
        print(f"Error: Google Sheet '{spreadsheet_name}' not found.")
        return pd.DataFrame()
    except gspread.exceptions.WorksheetNotFound:
        print(f"Error: Worksheet '{worksheet_name}' not found.")
        return pd.DataFrame()
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame()

# REMOVE THIS FUNCTION: It's no longer needed if you truncate and then insert all unique records.
# def get_existing_emails_from_db(conn, table_name):
#     """Fetch existing emails from PostgreSQL to check for duplicates."""
#     try:
#         cur = conn.cursor()
#         cur.execute(f"SELECT email FROM {table_name};")
#         existing_emails = [row[0] for row in cur.fetchall()]
#         return existing_emails
#     except Exception as e:
#         print(f"Error fetching existing emails: {e}")
#         return []

def insert_data_to_postgres(df, table_name, db_host, db_name, db_user, db_password, db_port, expected_cols):
    """Inserts or updates data into PostgreSQL."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password,
            port=db_port
        )
        cur = conn.cursor()

        # --- STEP 1: Ensure table exists first (ALWAYS RUN THIS FIRST) ---
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id SERIAL PRIMARY KEY, -- New auto-incrementing primary key
            name VARCHAR(255),
            email VARCHAR(255), -- Now allows duplicates and NULLs
            number VARCHAR(50),
            country_name VARCHAR(100),
            remarks TEXT,
            agent VARCHAR(100),
            first_call_date DATE,
            status VARCHAR(50),
            notes_from_call TEXT,
            post_call_email TEXT,
            tags TEXT,
            interested_category VARCHAR(100),
            sales_status VARCHAR(50),
            sales_amount NUMERIC(10, 2),
            next_follow_up_time VARCHAR(50),
            next_follow_up_date DATE,
            "Calling_Stamp" DATE,
            "Signup_Date" DATE
        );
        """
        cur.execute(create_table_sql)
        conn.commit()
        print(f"[{datetime.now()}] Table '{table_name}' ensured to exist with correct schema.")
        # --- END CREATE TABLE ---

        # --- STEP 2: Now that table exists, TRUNCATE it to clear old data ---
        cur.execute(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE;")
        conn.commit()
        print(f"[{datetime.now()}] Table '{table_name}' truncated.")
        # --- END TRUNCATE ---

        # Prepare SQL for insertion (simple INSERT, as table is empty after truncate)
        cols = ", ".join([f'"{col}"' for col in expected_cols])
        placeholders = ", ".join(["%s"] * len(expected_cols))
        # Remove update_set and ON CONFLICT DO UPDATE part for a simple INSERT as per earlier discussion
        # If you still want ON CONFLICT DO UPDATE functionality (e.g., if ID is the PK for updates, but emails can be duplicates)
        # you would re-add that logic, but if aiming for simple truncate/load, plain INSERT is sufficient.
        # For this scenario, assuming you want all rows inserted regardless of email uniqueness after ID became PK:
        insert_sql = f"""
        INSERT INTO {table_name} ({cols})
        VALUES ({placeholders});
        """
        # If you keep the "ON CONFLICT (email) DO UPDATE" and email is no longer PK, it will error.
        # If you want to insert ALL rows including duplicates (since email is no longer PK), use simple INSERT.
        # If you want to use the new 'id' as PK for UPSERT, the logic becomes different.
        # For "all the data", a simple INSERT (after TRUNCATE) is best.

        data_to_insert = []
        for index, row in df.iterrows():
            processed_row = []
            for col in expected_cols:
                value = row[col]
                if pd.isna(value):
                    processed_row.append(None)
                elif isinstance(value, str) and value.strip() == '':
                    processed_row.append(None)
                else:
                    processed_row.append(value)
            data_to_insert.append(tuple(processed_row))

        # Insert records
        psycopg2.extras.execute_batch(cur, insert_sql, data_to_insert) # Use insert_sql here
        conn.commit()
        print(f"[{datetime.now()}] Successfully inserted {len(data_to_insert)} records after truncation.")

    except psycopg2.Error as e:
        print(f"Error with PostgreSQL: {e}")
        if conn:
            conn.rollback()
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == "__main__":
    print(f"[{datetime.now()}] Starting sync process...")

    gc_client = get_gspread_client()
    if gc_client is None:
        print("Google Sheets client initialization failed.")
        exit()

    # Fetch data from Google Sheets
    df_sheets = fetch_data_from_gsheets(gc_client, GOOGLE_SHEET_NAME, WORKSHEET_NAME)
    if df_sheets.empty:
        print("No data fetched. Exiting sync process.")
        exit()

    print(f"[{datetime.now()}] Attempting to insert {len(df_sheets)} records into PostgreSQL.") # Added for clarity
    # Insert data into PostgreSQL
    insert_data_to_postgres(df_sheets, "sales_data", PG_HOST, PG_DBNAME, PG_USER, PG_PASSWORD, PG_PORT, expected_db_columns)
    print(f"[{datetime.now()}] Sync finished.")

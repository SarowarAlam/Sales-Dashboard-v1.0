import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import seaborn as sns
import re
from datetime import datetime, date
import io
import json
import time

# NEW IMPORTS FOR POSTGRESQL
import psycopg2
import psycopg2.extras

st.set_page_config(page_title="Sales Call Dashboard", layout="wide")
sns.set(style="whitegrid")

# --- Configuration for PostgreSQL ---
try:
    db_credentials = st.secrets["postgres"]
    DB_NAME = db_credentials["dbname"]
    DB_USER = db_credentials["user"]
    DB_PASSWORD = db_credentials["password"]
    DB_HOST = db_credentials["host"]
    DB_PORT = db_credentials["port"]
except KeyError:
    st.error("PostgreSQL credentials not found in Streamlit secrets. Please configure them in .streamlit/secrets.toml.")
    DB_NAME = "sales_dashboard_db"
    DB_USER = "postgres"
    DB_PASSWORD = "Your Password"  # Replace with your actual PostgreSQL password if not using secrets
    DB_HOST = "localhost"
    DB_PORT = "5432"

# --- Define Expected DB Columns Manually ---
expected_db_columns = [
    'name', 'email', 'number', 'country_name', 'remarks', 'agent', 'first_call_date',
    'status', 'notes_from_call', 'post_call_email', 'tags', 'interested_category', 'sales_status',
    'sales_amount', 'next_follow_up_time', 'next_follow_up_date', 'Calling_Stamp', 'Signup_Date'
]

# --- Data Loading (Cached - Modified for PostgreSQL) ---
@st.cache_data(ttl=60)
def load_full_sales_data_from_db(db_host, db_name, db_user, db_password, db_port, expected_cols):
    conn = None
    df = pd.DataFrame()
    try:
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password,
            port=db_port
        )
        cursor = conn.cursor()

        # Quote all column names for the SQL query
        quoted_cols = [f'"{col}"' for col in expected_cols]
        
        # Fetch all data from the sales_data table
        cursor.execute(f"SELECT {', '.join(quoted_cols)} FROM sales_data;")
        data = cursor.fetchall()
        
        # Get column names directly from cursor description
        col_names = [desc[0] for desc in cursor.description]

        df_raw = pd.DataFrame(data, columns=col_names)
        
        # Ensure column names are consistent (lowercase, underscores)
        df_raw.columns = df_raw.columns.str.lower().str.strip().str.replace(' ', '_').str.replace('&', 'and')

        # --- Post-processing (similar to your original GSheet loading, but adjusted for DB column names) ---
        
        # Process first_call_date to identify initial calls
        if 'first_call_date' in df_raw.columns:
            df_raw['date_called'] = pd.to_datetime(df_raw['first_call_date'], errors='coerce')
            df_raw['is_initial_call'] = df_raw['date_called'].notna().astype(int)
        else:
            df_raw['date_called'] = pd.NaT
            df_raw['is_initial_call'] = 0
            st.warning("Column 'first_call_date' not found in database. Initial call metrics may be inaccurate.")

        # Convert sales_amount to numeric
        if 'sales_amount' in df_raw.columns:
            df_raw['sales_amount'] = pd.to_numeric(df_raw['sales_amount'], errors='coerce').fillna(0)
        else:
            df_raw['sales_amount'] = 0
            st.warning("Column 'sales_amount' not found. Sales metrics will be zero.")

        follow_up_date_db_columns_for_parsing = [
            'next_follow_up_date'
        ]
        for col_name in follow_up_date_db_columns_for_parsing:
            if col_name in df_raw.columns:
                df_raw[col_name] = pd.to_datetime(df_raw[col_name], errors='coerce').dt.date
            else:
                # Ensure the column exists even if empty, with NaT
                df_raw[col_name] = pd.NaT

        # Standardize status column
        # For 'status'
        if 'status' in df_raw.columns:
            # Convert to string, ensuring actual NaN/None become string 'nan'/'None'
            df_raw['status'] = df_raw['status'].astype(str).str.strip().str.lower()
            status_mapping = {
                'answered call': 'Answered',
                'answered': 'Answered',
                'not answered': 'Not answered',
                'invalid number': 'Invalid number',
                'silent call/voicemail': 'Voicemail',
                'voicemail': 'Voicemail',
            }
            df_raw['status'] = df_raw['status'].map(status_mapping)
            # Now, any unmapped values or original NaNs are handled.
            # Apply title case for display, but be careful with "Not answered"
            df_raw['status'] = df_raw['status'].apply(lambda x: x.title() if pd.notna(x) else x)
            df_raw['status'].replace({"Invalid Number": "Invalid number", "Not Answered": "Not answered"}, inplace=True)
        else:
            df_raw['status'] = 'nan' # Fallback for missing column


        # For 'sales_status'
        if 'sales_status' in df_raw.columns:
            df_raw['sales_status'] = df_raw['sales_status'].astype(str).str.strip().str.lower()
            sales_status_mapping = {
                'sold': 'Converted',
                'deal won': 'Converted',
                'deal complete': 'Converted',
                'converted': 'Converted',
                'lost': 'Not interested',
                'no interest': 'Not interested',
                'not interested (n)': 'Not interested',
                'not interested': 'Not interested',
                'follow up': 'Follow up',
                'f': 'Follow up',
            }
            df_raw['sales_status'] = df_raw['sales_status'].map(sales_status_mapping)
            df_raw['sales_status'] = df_raw['sales_status'].apply(lambda x: x.title() if pd.notna(x) else x)
        else:
            df_raw['sales_status'] = 'nan'

        # Extract Issues from Tags
        if 'tags' in df_raw.columns:
            df_raw['issues'] = df_raw['tags'].str.extract(
                r'(Language Barriers|KYC Issues & Complaints|Bonus or Promotions|Network, Inaudible Conversation|Interested|Spread, Leverage & Platform Concerns|Future Deposit|Withdrawal complaint|Wrong number claim|Busy|Geographical permission needed|VOIP restricted country|Payment method issue|Platform Issue|Answered by Another Person|Explorer|Partners Program)',
                flags=re.IGNORECASE
            )
            df_raw['issues'].fillna('N/A', inplace=True)
        else:
            df_raw['issues'] = 'N/A'
            st.warning("Column 'tags' not found. Issue analysis will be unavailable.")

        # Extract call_outcome from status
        df_raw['call_outcome'] = df_raw['status'].str.extract(
            r'(Answered|Not answered|Invalid number|Voicemail)',
            flags=re.IGNORECASE
        )
        
        # Handle country_name consistency
        if 'country_name' not in df_raw.columns:
            df_raw['country_name'] = 'Unknown'
            st.warning("Column 'country_name' not found. Country analysis will be limited.")
        
        # Assign country_group
        df_raw['country_group'] = np.where(
            df_raw['country_name'].isin(['India', 'Pakistan', 'Bangladesh']), 'South Asia',
            np.where(df_raw['country_name'].isin(['Brazil', 'Argentina', 'Colombia']), 'Latin America',
            np.where(df_raw['country_name'].isin(['Iraq', 'Saudi Arabia', 'United Arab Emirates']), 'Middle East', 'Other'))
        )

        # --- FOLLOW-UP CALL COUNTING LOGIC ---
        df_raw['total_follow_up_calls'] = 0
        follow_up_date_db_columns = [
            'next_follow_up_date'
        ]

        today = datetime.now().date()

        for col_name in follow_up_date_db_columns:
            if col_name in df_raw.columns:
                df_raw[col_name] = pd.to_datetime(df_raw[col_name], errors='coerce').dt.date
                df_raw['total_follow_up_calls'] += (
                    (df_raw[col_name].notna()) &
                    (df_raw[col_name] <= today) # Only count calls made till today
                ).astype(int)
            else:
                st.warning(f"Follow-up column '{col_name}' not found in database. Follow-up counts may be incomplete.")

        # Process next_follow_up_date and time
        if 'next_follow_up_date' in df_raw.columns:
            df_raw['next_follow_up_date'] = pd.to_datetime(df_raw['next_follow_up_date'], errors='coerce').dt.date
        else:
            df_raw['next_follow_up_date'] = pd.NaT

        if 'next_follow_up_time' in df_raw.columns:
            df_raw['next_follow_up_time'] = df_raw['next_follow_up_time'].astype(str).replace('NaT', '').replace('nan', '').str.strip()
        else:
            df_raw['next_follow_up_time'] = ''
            
        # Ensure 'email' column exists
        if 'email' not in df_raw.columns:
            df_raw['email'] = ''
        if 'agent' not in df_raw.columns:
            df_raw['agent'] = 'Unknown'

        return df_raw

    except psycopg2.Error as e:
        st.error(f"Error connecting to or querying PostgreSQL database: {e}")
        st.warning("Displaying a **sample dataset** for visual reference due to database loading issues.")
        return pd.DataFrame()  
    finally:
        if conn:
            conn.close()

# Dashboard always runs now
refresh_interval = st.sidebar.number_input("Auto-refresh interval (seconds)", min_value=0, value=0, key="refresh_interval_input")


df = pd.DataFrame()
data_load_success = False

if 'DB_NAME' in locals() and DB_NAME:
    df = load_full_sales_data_from_db(DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT, expected_db_columns)
    if not df.empty:
        data_load_success = True
        if "data_loaded_message_shown" not in st.session_state:
            success_message = st.empty()
            success_message.success(f"Loaded {len(df)} records from PostgreSQL!", icon="✅")
            time.sleep(1)
            success_message.empty()
            st.session_state.data_loaded_message_shown = True
    else:
        st.error("Failed to load data from PostgreSQL. Check console for errors or Streamlit secrets.")
        # Sample Data for fallback
        df = pd.DataFrame({
            'name': ['Test User 1', 'Test User 2', 'Test User 3', 'Test User 4', 'Test User 5', 'Test User 6', 'Test User 7', 'Test User 8', 'Test User 9', 'Test User 10', 'Test User 11'],
            'email': ['test1@example.com', 'test2@example.com', 'test3@example.com', 'test4@example.com', 'test5@example.com', 'test6@example.com', 'test7@example.com', 'test8@example.com', 'test9@example.com', 'test10@example.com', 'test11@example.com'],
            'number': ['111', '222', '333', '434', '555', '666', '777', '888', '999', '000', '123'],
            'sales_status': ['Follow up', 'Not interested', 'Converted', 'Follow up', 'Converted', 'Converted', 'Follow up', 'Converted', 'Not interested', 'Follow up', 'Converted'],
            'sales_amount': [1000.0, 0.0, 500.0, 0.0, 750.0, 2000.0, 300.0, 1200.0, 0.0, 0.0, 800.0],
            'next_follow_up_time': ['10:00 AM', '', '', '02:30 PM', '', '11:00 AM', '', '', '', '', '09:00 AM'],
            'next_follow_up_date': ['2023-07-26', '2023-08-01', None, '2023-07-16', None, '2023-07-26', '2023-08-01', None, None, None, '2023-08-15'],
            'remarks': ['Good', 'Fair', 'Excellent', 'Poor', 'Good', 'Excellent', 'Good', 'Fair', 'Poor', 'Good', 'Excellent'],
            'agent': ['Agent A', 'Agent B', 'Agent A', 'Agent C', 'Agent B', 'Agent D', 'Agent A', 'Agent E', 'Agent F', 'Agent E', 'Agent G'],
            'first_call_date': ['2023-01-01', '2023-01-05', '2023-01-10', '2023-01-15', '2023-01-20', '2023-01-25', '2023-01-28', '2023-02-01', '2023-02-05', '2023-02-10', '2023-02-12'],
            'status': ['Answered', 'Not answered', 'Answered', 'Voicemail', 'Answered', 'Answered', 'Answered', 'Answered', 'Answered', 'Not answered', 'Answered'],
            'notes_from_call': ['Detailed discussion', 'No answer', 'Converted lead', 'Left voicemail', 'Platform discussed', 'High interest', 'Follow up needed', 'Interested in future', 'Complaint received', 'Busy, call back', 'Converted lead'],
            'tags': ['Language Barriers', 'N/A', 'Interested', 'N/A', 'Platform Issue', 'N/A', 'Interested', 'N/A', 'KYC Issues & Complaints', 'N/A', 'N/A'],
            'interested_category': ['Hot Lead (1-3 Days)', 'Warm Lead (1-2 weeks)', 'Warm Lead (1 month)', 'Cold Lead (1-3 Months)', 'Hot Lead (1-3 Days)', 'Warm Lead (1-2 weeks)', 'Hot Lead (1-3 Days)', 'Warm Lead (1-2 weeks)', 'Cold Lead (1-3 Months)', 'Cold Lead (1-3 Months)', 'Warm Lead (1-2 weeks)'],
            'country_name': ['India', 'Brazil', 'Pakistan', 'Iraq', 'India', 'Cyprus', 'India', 'Brazil', 'Pakistan', 'Iraq', 'Cyprus'],
            'Calling_Stamp': ['2023-01-01 10:00:00', '2023-01-05 11:00:00', '2023-01-10 12:00:00', '2023-01-15 13:00:00', '2023-01-20 14:00:00', '2023-01-25 15:00:00', '2023-01-28 16:00:00', '2023-02-01 17:00:00', '2023-02-05 18:00:00', '2023-02-10 19:00:00', '2023-02-12 20:00:00'],
            'Signup_Date': ['2023-01-01', '2023-01-05', '2023-01-10', '2023-01-15', '2023-01-20', '2023-01-25', '2023-01-28', '2023-02-01', '2023-02-05', '2023-02-10', '2023-02-12']
        })
        df.columns = df.columns.str.strip().str.replace(' ', '_').str.replace('&', 'and').str.lower()
        if 'first_call_date' in df.columns:
            df['date_called'] = pd.to_datetime(df['first_call_date'], errors='coerce')
            df['is_initial_call'] = df['date_called'].notna().astype(int)
        else:
            df['date_called'] = pd.NaT
            df['is_initial_call'] = 0
            
        if 'sales_amount' in df.columns:
            df['sales_amount'] = pd.to_numeric(df['sales_amount'], errors='coerce').fillna(0)
            
        if 'status' in df.columns:
            df['status'] = df['status'].astype(str).str.strip().str.capitalize()
            df['status'].replace({
                'Answered call': 'Answered',
                'Silent call/voicemail': 'Voicemail',
                'Not answered': 'Not answered',
                'Invalid number': 'Invalid number'
            }, inplace=True)    

        if 'sales_status' in df.columns:
            df['sales_status'] = df['sales_status'].astype(str).str.strip().str.capitalize()
            sales_status_mapping = {
                'Sold': 'Converted',
                'Deal won': 'Converted',
                'Deal complete': 'Converted',
                'Lost': 'Not interested',    
                'No interest': 'Not interested'
            }
            df['sales_status'].replace(sales_status_mapping, inplace=True)

        if 'tags' in df.columns:
            df['issues'] = df['tags'].str.extract(
                r'(Language Barriers|KYC Issues & Complaints|Bonus or Promotions|Network, Inaudible Conversation|Interested|Spread, Leverage & Platform Concerns|Future Deposit|Withdrawal complaint|Wrong number claim|Busy|Geographical permission needed|VOIP restricted country|Payment method issue|Platform Issue|Answered by Another Person|Explorer|Partners Program)',
                flags=re.IGNORECASE
            )
            df['issues'].fillna('N/A', inplace=True)

        df['call_outcome'] = df['status'].str.extract(
            r'(Answered|Not answered|Invalid number|Voicemail)',
            flags=re.IGNORECASE
        )

        df['country_group'] = np.where(
            df['country_name'].isin(['India', 'Pakistan', 'Bangladesh']), 'South Asia',
            np.where(df['country_name'].isin(['Brazil', 'Argentina', 'Colombia']), 'Latin America',
            np.where(df['country_name'].isin(['Iraq', 'Saudi Arabia', 'United Arab Emirates']), 'Middle East', 'Other'))
        )

        df['total_follow_up_calls'] = 0
        follow_up_date_db_columns = [
            'next_follow_up_date'
        ]
        for col_name in follow_up_date_db_columns:
            if col_name in df.columns:
                df[col_name] = pd.to_datetime(df[col_name], errors='coerce').dt.date
                df['total_follow_up_calls'] += df[col_name].notna().astype(int)

        if 'next_follow_up_date' in df.columns:
            df['next_follow_up_date'] = pd.to_datetime(df['next_follow_up_date'], errors='coerce').dt.date
            
        if 'next_follow_up_time' in df.columns:
            df['next_follow_up_time'] = df['next_follow_up_time'].astype(str).replace('NaT', '').replace('nan', '').str.strip()
            
        if 'email' not in df.columns:
            df['email'] = ''
        if 'agent' not in df.columns:
            df['agent'] = 'Unknown'

        df['Calling_Stamp'] = pd.to_datetime(df['Calling_Stamp'], errors='coerce')
        df['Signup_Date'] = pd.to_datetime(df['Signup_Date'], errors='coerce')


if not df.empty:
    
    total_unique_agents_in_dataset = df['agent'].loc[df['agent'].astype(bool)].nunique()

    current_user_df = df.copy()
    
    if not current_user_df.empty and 'date_called' in current_user_df.columns and not current_user_df['date_called'].isnull().all():
        data_min_date = current_user_df['date_called'].min().date()
        data_max_date = current_user_df['date_called'].max().date()
    else:
        data_min_date = date(2023, 1, 1)
        data_max_date = date.today()

    if "agent_filter" not in st.session_state:
        st.session_state["agent_filter"] = "All"
    if "country_filter" not in st.session_state:
        st.session_state["country_filter"] = "All"
    if "status_filter" not in st.session_state:
        st.session_state["status_filter"] = "All"
    
    # Initialize start_date and end_date in session state, with "Today" option
    if "start_date" not in st.session_state:
        st.session_state["start_date"] = data_min_date
    if "end_date" not in st.session_state:
        st.session_state["end_date"] = data_max_date
    
    def update_start_date():
        st.session_state["start_date"] = st.session_state["start_date_input"]
        if st.session_state["start_date"] > st.session_state["end_date"]:
            st.session_state["end_date"] = st.session_state["start_date"]

    def update_end_date():
        st.session_state["end_date"] = st.session_state["end_date_input"]
        if st.session_state["end_date"] < st.session_state["start_date"]:
            st.session_state["start_date"] = st.session_state["end_date"]


    agent_list = ['All'] + sorted(current_user_df['agent'].dropna().unique().tolist())
    agent_index = agent_list.index(st.session_state['agent_filter']) if st.session_state['agent_filter'] in agent_list else 0
    agent_filter = st.sidebar.selectbox("Select Agent", agent_list, index=agent_index, key="agent_filter")

    country_list = ['All'] + sorted(current_user_df['country_name'].dropna().unique().tolist())
    status_list = ['All'] + sorted(current_user_df['status'].dropna().unique().tolist())

    country_index = country_list.index(st.session_state['country_filter']) if st.session_state['country_filter'] in country_list else 0
    status_index = status_list.index(st.session_state['status_filter']) if st.session_state['status_filter'] in status_list else 0

    country_filter = st.sidebar.selectbox("Select Country", country_list, index=country_index, key="country_filter")
    status_filter = st.sidebar.selectbox("Select Call Status", status_list, index=status_index, key="status_filter")

    # Add "Today" option for Start Date
    start_date_options = [data_min_date, date.today()] if data_min_date <= date.today() else [data_min_date]
    start_date_value = st.session_state["start_date"]
    
    if start_date_value == date.today():
        start_date_index = start_date_options.index(date.today()) if date.today() in start_date_options else 0
    else:
        start_date_index = start_date_options.index(start_date_value) if start_date_value in start_date_options else 0

    st.sidebar.date_input(
        "Start Date",
        value=start_date_value,
        min_value=data_min_date,
        max_value=data_max_date,
        key="start_date_input",
        on_change=update_start_date
    )
    
    # Add "Today" option for End Date
    end_date_options = [data_max_date, date.today()] if data_max_date >= date.today() else [data_max_date]
    end_date_value = st.session_state["end_date"]

    if end_date_value == date.today():
        end_date_index = end_date_options.index(date.today()) if date.today() in end_date_options else 0
    else:
        end_date_index = end_date_options.index(end_date_value) if end_date_value in end_date_options else 0

    st.sidebar.date_input(
        "End Date",
        value=end_date_value,
        min_value=data_min_date,
        max_value=data_max_date,
        key="end_date_input",
        on_change=update_end_date
    )

    start_date = st.session_state["start_date"]
    end_date = st.session_state["end_date"]


    filtered_df = current_user_df.copy()
    if agent_filter != 'All':
        filtered_df = filtered_df[filtered_df['agent'] == agent_filter]
    if country_filter != 'All':
        filtered_df = filtered_df[filtered_df['country_name'] == country_filter]
    if status_filter != 'All':
        filtered_df = filtered_df[filtered_df['status'] == status_filter]
    
    if 'date_called' in filtered_df.columns:
        filtered_df['date_called_dt'] = pd.to_datetime(filtered_df['date_called'], errors='coerce')
        filtered_df = filtered_df[
            (filtered_df['date_called_dt'].dt.date >= start_date) &
            (filtered_df['date_called_dt'].dt.date <= end_date)
        ]
        filtered_df = filtered_df.drop(columns=['date_called_dt'])
    else:
        st.warning("date_called column not found or is empty after filtering. Time-based filters may not work as expected.")
        filtered_df = pd.DataFrame(columns=df.columns)

    st.markdown("""
    <style>
    /* Main tab container - for the overall box */
    /* This is the div that Streamlit wraps the radio group in */
    .stRadio > div {
        background-color: transparent; /* Remove any default background */
        border-radius: 15px;
        box-shadow: none; /* Remove default outer shadow */
        margin-bottom: 1.8rem;
        /* A subtle border around the entire group of tabs */
        border: 1px solid rgba(255, 255, 255, 0.1);  
        padding: 1px; /* Remove internal padding */
        overflow: hidden; /* Ensures rounded corners clip correctly */
    }
    
    /* Tab group layout - targets the internal div with role="radiogroup" */
    .stRadio [role="radiogroup"] {
        display: flex; /* Enable flexbox for horizontal layout */
        flex-wrap: nowrap; /* <<-- KEY CHANGE: Prevents wrapping to next line */
        gap: 2px; /* Remove space between individual tabs */
        padding: 10px; /* Remove padding from the actual radio group */
        background-color: #262730; /* Dark background for the tab row itself */
        border-radius: 15px; /* Apply rounded corners to the tab row */
        overflow: hidden; /* Ensure content is clipped by border-radius */
    }
    
    /* Individual tabs - targets the internal div with role="radio" */
    .stRadio [role="radio"] {
        background-color: #262730; /* Default tab background (dark) */
        border: none; /* Remove individual borders */
        border-radius: 0px; /* Make them square to connect */
        padding: 10px 18px; /* Standard padding for each tab */
        margin: 0; /* No margin between tabs */
        transition: all 0.2s ease-in-out; /* Smooth transitions for hover/active states */
        box-shadow: none; /* Remove individual shadow */
        flex-grow: 1; /* Allows tabs to grow and fill available space evenly */
        text-align: center; /* Center text within each tab */
        color: #ccc; /* Lighter text for non-active tabs */
        font-weight: 400;
        cursor: pointer; /* Indicate clickable */
        user-select: none; /* Prevent text selection */
        display: flex; /* Use flexbox for content alignment */
        align-items: center;
        justify-content: center;
        min-width: 0; /* Allow tabs to shrink as much as needed */
    }
    
    /* Add subtle border between tabs */
    .stRadio [role="radio"]:not(:last-child) {
        border-right: 1px solid rgba(255, 255, 255, 0.05); /* Very subtle vertical separator */
    }
    
    /* Hover effect for individual tabs */
    .stRadio [role="radio"]:hover {
        background-color: #363740; /* Slightly lighter dark on hover */
        color: #fff; /* White text on hover */
    }
    
    /* Active tab styling */
    .stRadio [role="radio"][aria-checked="true"] {
        background-color: #1a73e8; /* Blue for active tab */
        color: white;
        font-weight: 500;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2); /* More prominent shadow for active tab */
        position: relative; /* Needed for z-index to bring active tab forward */
        z-index: 1; /* Bring active tab slightly forward */
    }
    
    /* Hide default radio button circle */
    .stRadio [role="radio"] > div:first-child {
        display: none;
    }

    /* Adjust icon size and margin within tabs */
    .stRadio [role="radio"] svg {
        font-size: 1.2em; /* Make icons slightly larger */
        margin-right: 5px; /* Space between icon and text */
        flex-shrink: 0; /* Prevent icons from shrinking */
    }

    /* Target the span containing the text label, allowing it to truncate */
    .stRadio [role="radio"] span {
        white-space: nowrap; /* Keep text on one line */
        overflow: hidden; /* Hide overflowing text */
        text-overflow: ellipsis; /* Add ellipsis (...) if text overflows */
    }
    
    /* Metric cards - keep previous styling as they are good */
    .metric-card {
        background: white;
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        height: 100%;
        border: 1px solid #f0f0f0;
    }
    .metric-title {
        font-size: 0.9rem;
        color: #5f6368;
        margin-bottom: 8px;
        font-weight: 500;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #202124;
    }
    
    /* Dark mode support */
    @media (prefers-color-scheme: dark) {
        .stRadio > div {
            border: 1px solid #444; /* Darker border for tab group */
        }
        .stRadio [role="radio"] {
            background-color: #2b2b2b; /* Darker background for tabs */
            color: #aaa;
        }
        .stRadio [role="radio"]:not(:last-child) {
            border-right: 1px solid #444; /* Darker separator */
        }
        .stRadio [role="radio"]:hover {
            background-color: #3b3b3b; /* Darker hover */
            color: #fff;
        }
        .stRadio [role="radio"][aria-checked="true"] {
            background-color: #0d47a1; /* Darker blue for active tab */
            color: white;
            border-color: #0d47a1;
        }
        .metric-card {
            background: #2b2b2b;
            border-color: #444;
        }
        .metric-title {
            color: #aaa;
        }
        .metric-value {
            color: #fff;
        }
    }
    /* CSS for st.selectbox elements */
    .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"] {
        background-color: #5C6BC0;
        color: white;
        border-radius: 12px;
        font-weight: bold;
        padding: 5px 10px;
        margin-top: 20px;
        border: 1px solid #5C6BC0;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"] div[data-testid="stMuiSelect"] {
        color: white;
        font-weight: bold;
    }
    .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"] svg {
        fill: white;
    }
    .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"]:hover {
        background-color: #7986CB;
        border-color: #7986CB;
    }
    .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"]:focus-within {
        border-color: #9FA8DA;
        box-shadow: 0 0 0 0.2rem rgba(159, 168, 218, 0.25);
    }
    /* Dark mode for selectbox */
    @media (prefers-color-scheme: dark) {
        .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"] {
            background-color: #424242;
            border-color: #616161;
        }
        .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"]:hover {
            background-color: #555;
            border-color: #757575;
        }
        .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stMuiSelect"] {
            color: #fff;
        }
        .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"] svg {
            fill: #fff;
        }
        .stSelectbox [data-testid="stSelectboxContainer"] div[data-testid="stSelectbox"]:focus-within {
            border-color: #9E9E9E;
            box-shadow: 0 0 0 0.2rem rgba(158, 158, 158, 0.25);
        }
    }
    </style>
    """, unsafe_allow_html=True)


    # Dashboard Metrics Layout
    st.subheader("📊 Dashboard")
    
    if not filtered_df.empty:

        # Calculate total initial calls based on is_initial_call flag
        total_initial_calls = filtered_df['is_initial_call'].sum()  # Initial Calls

        # Calculate total follow-up calls based on filtered_df and relevant dates
        total_follow_up_calls_overall = 0
        follow_up_date_cols_for_counting = [
            'next_follow_up_date'
        ]
        
         # Use the end_date from the filter for counting follow-up calls
        filter_end_date = end_date # This is the user-selected end date

        for col_name in follow_up_date_cols_for_counting:
            if col_name in filtered_df.columns:
                total_follow_up_calls_overall += (
                    (filtered_df[col_name].notna()) &
                    (filtered_df[col_name] <= filter_end_date)
                ).astype(int).sum() # Count follow-up calls made till the end date

        

        total_calls = total_initial_calls + total_follow_up_calls_overall  # Total Calls Made
        

        # Total Calls (including answered and non-answered)
        total_calls_display = f"{total_calls}" if not filtered_df.empty else "0"

        # Calculate total answered calls
        total_answered_calls = (filtered_df['status'] == 'Answered').sum()

        col1, col2, col3, col4, col5 = st.columns(5)

        def centered_metric(title, value):
            return f"""
                <div style='text-align: center; line-height: 1.2;'>
                    <div style='font-size: 0.9rem; font-weight: 500; margin-bottom: 0.2rem;'>{title}</div>
                    <div style='font-size: 1.8rem; font-weight: bold;'>{value}</div>
                </div>
            """
            
        total_entries = len(filtered_df)
        answered_rate = f"{(filtered_df['status'] == 'Answered').mean() * 100:.1f}%" if not filtered_df.empty else "0.0%"
        
        answered_calls_display = f"{total_answered_calls} ({answered_rate})" if not filtered_df.empty else "0 (0.0%)"
        
        total_sales = filtered_df['sales_amount'].sum()
        total_sales_value = f"${total_sales:,.2f}" if not np.isnan(total_sales) else "N/A"

        col1.markdown(centered_metric("Total Calls Made", total_calls_display), unsafe_allow_html=True)
        col2.markdown(centered_metric("Total Initial Calls", total_initial_calls), unsafe_allow_html=True)
        col3.markdown(centered_metric("Total Follow-Up Calls Made", total_follow_up_calls_overall), unsafe_allow_html=True)
        col4.markdown(centered_metric("Answered Calls", answered_calls_display), unsafe_allow_html=True)
        col5.markdown(centered_metric("Total Sales Generated", total_sales_value), unsafe_allow_html=True)  

    else:
        st.info("No data available to display dashboard metrics based on current filters.")


    tabs = {
        "home": "📈 Home",
        "data": "📟 Data Overview",
        "agent": "👨‍💼 Agent Performance",
        "country": "🌍 Country Analysis",
        "call": "📞 Call Outcome",
        "pipeline": "📈 Sales Pipeline",
        "followup": "🗓️ Follow Up Calling",
        "report": "📅 Report Generator"
    }
    
    query_params = st.query_params
    default_tab_raw = query_params.get("tab", ["home"])[0]
    
    if default_tab_raw not in tabs:
        default_tab = "home"
    else:
        default_tab = default_tab_raw

    st.query_params.update(tab=default_tab)

    tab_key = st.radio(
        "",
        list(tabs.keys()),
        format_func=lambda k: tabs[k],
        index=list(tabs.keys()).index(default_tab),
        horizontal=True,
        key="main_tabs_radio"
    )

    st.query_params.update(tab=tab_key)


    if tab_key == "home":
        with st.container():
            st.subheader(tabs["home"])

        with st.expander("Sales Distribution by Country"):
            st.subheader("Sales by Country")

            if not filtered_df.empty:
                country_sales = filtered_df.groupby('country_name')['sales_amount'].sum().reset_index()

                fig = px.bar(
                    country_sales,
                    x='country_name',
                    y='sales_amount',
                    color='sales_amount',
                    labels={'country_name': 'Country', 'sales_amount': 'Sales Amount'},
                    title="Total Sales by Country",
                    color_continuous_scale='Blues'
                )

                fig.update_traces(
                    hovertemplate='<b>Country:</b> %{x}<br><b>Total Sales:</b> $%{y:,.2f}',
                    marker_line_width=1.2,
                    marker_line_color='darkgrey'
                )

                fig.update_layout(
                    hoverlabel=dict(
                        bgcolor="black",
                        font_size=13,
                        font_family="Arial",
                        font_color="white",
                        bordercolor="lightgray"
                    ),
                    xaxis_title="Country",
                    yaxis_title="Total Sales ($)"
                )

                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No sales data available for charts with current filters.")


        custom_colors = ["#6D6D6D", "#019C0E", "#27288B", "#C52E00"]

        with st.expander("Call Outcome"):
            st.subheader("Call Outcomes Pie Chart")
            if not filtered_df.empty:
                outcome_counts = filtered_df['call_outcome'].value_counts().reset_index()
                outcome_counts.columns = ['call_outcome', 'Count']

                fig2 = px.pie(outcome_counts, names='call_outcome', values='Count', hole=0.5, color_discrete_sequence=custom_colors)
                
                fig2.update_traces(
                    textinfo='percent+label',
                    textfont_size=12,
                    textfont_color='white',
                    hovertemplate='<b>%{label}</b><br>Count: %{value}<br>Percentage: %{percent}',
                    textposition='inside'
                )
                
                st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info("No call outcome data available for charts with current filters.")


        if 'selected_issue' not in st.session_state:
            st.session_state.selected_issue = None

        with st.expander("Issues"):
            st.subheader("Issue Frequency")
            if not filtered_df.empty:
                outcome_counts = filtered_df['issues'].value_counts().reset_index()
                outcome_counts.columns = ['issues', 'Count']

                total_issues = outcome_counts['Count'].sum()
                display_df = outcome_counts[outcome_counts['issues'] != 'N/A']
                
                if not display_df.empty:
                    display_df['Percentage'] = (display_df['Count'] / total_issues * 100).round(2)


                    fig3 = px.bar(
                        display_df,
                        x='Count',
                        y='issues',
                        color='issues',
                        orientation='h',
                        labels={'Count': 'Number of Occurrences', 'issues': 'Issue Type'},
                    )

                    fig3.update_traces(
                        hovertemplate="<b>%{y}</b><br>🔢 Count: %{x}<br>📊 Percentage: %{customdata}%<extra></extra>",
                        customdata=display_df['Percentage']
                    )

                    fig3.update_layout(
                        hoverlabel=dict(
                            bgcolor="black",
                            font_size=12,
                            font_family="Arial",
                            font_color="white"
                        ),
                        yaxis={'categoryorder': 'total ascending'},
                        height=600,
                        margin=dict(t=30, b=20, l=150, r=20),
                        xaxis_title='Count',
                        yaxis_title='Issue Type'
                    )

                    st.plotly_chart(fig3, use_container_width=False)
                else:
                    st.info("No issues (excluding 'N/A') found with current filters.")
            else:
                st.info("No data available for issue frequency analysis with current filters.")

            
        st.subheader("Country by Issue Frequency Search")
                                                                                                
        st.markdown("""
                                                                                                 <style>
                                                                                                    .stSelectbox [data-testid="stSelectbox"] {
                                                                                                        background-color: #4CAF50;
                                                                                                        color: white;
                                                                                                        border-radius: 12px;
                                                                                                        font-weight: bold;
                                                                                                        width: 200px;
                                                                                                        padding: 5px 10px;
                                                                                                        margin-top: 20px;
                                                                                                    }

                                                                                                    .stSelectbox [data-testid="stSelectbox"]:hover {
                                                                                                        background-color: #45a049;
                                                                                                    }

                                                                                                    .stSelectbox div[role="listbox"] {
                                                                                                        background-color: #f1f1f1;
                                                                                                        color: black;
                                                                                                        border-radius: 12px;
                                                                                                    }

                                                                                                    .stSelectbox li:hover {
                                                                                                        background-color: #ddd;
                                                                                                        color: black;
                                                                                                    }

                                                                                                    .stSelectbox select:focus {
                                                                                                        outline: none;
                                                                                                    }
                                                                                                 </style>
                                                                                                 """, unsafe_allow_html=True)

        if not filtered_df.empty and 'issues' in filtered_df.columns:
            available_issues = display_df['issues'].tolist() if not display_df.empty else []
            if available_issues:
                selected_issue = st.selectbox(
                                                "Select an Issue to see",
                                                available_issues,
                                                index=0 if st.session_state.selected_issue not in available_issues else available_issues.index(st.session_state.selected_issue),
                                                key="issue_selectbox"
                                            )

                st.session_state.selected_issue = selected_issue

                issue_data = filtered_df[filtered_df['issues'] == selected_issue]

                if not issue_data.empty:
                    country_issue_counts = issue_data['country_name'].value_counts().reset_index()
                    country_issue_counts.columns = ['Country', 'Count']
                                                                    
                    st.subheader(f"Countries with '{selected_issue}' issue")
                    st.dataframe(country_issue_counts)
                else:
                    st.warning(f"No data available for '{selected_issue}' issue.")    
            else:
                st.info("No issues found in the filtered data to select.")
        else:
            st.info("No data available for issue frequency search with current filters.")


    elif tab_key == "data":
        with st.container():
            st.subheader(tabs["data"])

        with st.expander("Filtered Raw Data"):
            st.subheader("Filtered Raw Data")
            if not filtered_df.empty:
                st.dataframe(filtered_df)
            else:
                st.info("No data found matching the selected filters.")
        

    elif tab_key == "agent":
        with st.container():
            st.subheader(tabs["agent"])
            
            if not filtered_df.empty:
                agent_stats = filtered_df.groupby('agent').agg(
                    Total_Initial_Calls=('is_initial_call', 'sum'),
                    Total_Follow_Up_Calls=('total_follow_up_calls', 'sum'),
                    Total_Answered_Calls=('status', lambda x: (x == 'Answered').sum()),
                    Answered_Rate=('status', lambda x: (x == 'Answered').mean() * 100),
                    Sales_Follow_Up_Rate=('sales_status', lambda x: (x == 'Follow up').mean() * 100),
                    Total_Sales=('sales_amount', 'sum'),
                    
                )
                agent_stats.columns = ['Total_Initial_Calls', 'Total_Follow_Up_Calls', 'Total_Answered_Calls', 'Answered_Rate', 'Sales_Follow_Up_Rate', 'Total_Sales']
                agent_stats = agent_stats.sort_values('Total_Initial_Calls', ascending=False)

                agent_stats = agent_stats.reset_index()
                agent_stats = agent_stats[agent_stats['agent'].str.strip().astype(bool)]
                valid_agents = sorted(agent_stats['agent'].tolist())
                
                st.markdown("""
                    <style>
                        .stSelectbox [data-testid="stSelectbox"] {
                            background-color: #4CAF50;
                            color: white;
                            border-radius: 12px;
                            font-weight: bold;
                            width: 200px;
                            padding: 5px 10px;
                            margin-top: 20px;
                        }

                        .stSelectbox [data-testid="stSelectbox"]:hover {
                            background-color: #45a049;
                        }

                        .stSelectbox div[role="listbox"] {
                            background-color: #f1f1f1;
                            color: black;
                            border-radius: 12px;
                        }

                        .stSelectbox li:hover {
                            background-color: #ddd;
                            color: black;
                        }

                        .stSelectbox select:focus {
                            outline: none;
                        }
                    </style>
                    """, unsafe_allow_html=True)

                st.subheader("Agent Performance Visualizations")

                agent_view_option = st.radio(
                    "Select Agent View:",
                    ["Top 5 Agents", "All Agents", "Individual Agent"],  
                    horizontal=True,
                    key="agent_view_radio"
                )

                if agent_view_option == "Individual Agent":
                    if valid_agents:
                        selected_agent = st.selectbox(
                            "Select an Agent",
                            valid_agents,
                            index=0,
                            key="selected_agent_selectbox"
                        )

                        agent_df = filtered_df[filtered_df['agent'] == selected_agent]
                        total_initial_calls_agent = agent_df['is_initial_call'].sum()  
                        total_follow_up_calls_agent = agent_df['total_follow_up_calls'].sum()
                        total_answered_calls_agent = (agent_df['status'] == 'Answered').sum()

                        if agent_df.empty:
                            st.warning(f"No data available for {selected_agent}.")
                        else:
                            st.subheader(f"📊 {selected_agent}'s Performance")
                            
                            col1, col2, col3, col4, col5 = st.columns(5)
                            
                            call_outcomes = agent_df['status'].value_counts().reindex(
                                ['Answered', 'Not answered', 'Voicemail', 'Invalid number'],
                                fill_value=0
                            )

                            with col1:
                                st.metric("📞 Initial Calls", f"{total_initial_calls_agent}")  
                            with col2:  
                                st.metric("🔄 Follow-Up Calls", f"{total_follow_up_calls_agent}")
                            with col3:
                                st.metric("✅ Answered Calls", f"{total_answered_calls_agent} ({((agent_df['status'] == 'Answered').mean() * 100):.1f}%)")
                            with col4:
                                st.metric("📈 Sales Follow Up Rate",
                                            f"{(agent_df['sales_status'] == 'Follow up').mean() * 100:.1f}%" if total_initial_calls_agent > 0 else "0.0%")  
                            with col5:
                                st.metric("💰 Total Sales",    
                                            f"${agent_df['sales_amount'].sum():,.2f}")  
                            
                            st.markdown(f"**Detailed Breakdown for {selected_agent}:**")

                            call_outcome_colors = {
                                'Answered': 'green',
                                'Not answered': 'gray',
                                'Invalid number': 'red',
                                'Voicemail': 'blue'
                            }

                            fig_call = px.pie(
                                call_outcomes,
                                names=call_outcomes.index,
                                values=call_outcomes.values,
                                title=f"{selected_agent} - Call Outcome Distribution",
                                hole=0.4,
                                color=call_outcomes.index,
                                color_discrete_map=call_outcome_colors
                            )
                            fig_call.update_traces(
                                hovertemplate="<b>%{label}</b><br>Calls: %{value}<br>Share: %{percent:.2f}%",
                                textinfo='percent+label',
                                texttemplate='%{percent:.0%}' if '%{percent}' != '0%' else '',
                                textposition='inside'
                            )
                            st.plotly_chart(fig_call, use_container_width=True)

                            sales_status_colors = {
                                'Follow up': 'purple',
                                'Not interested': 'red',
                                'Converted': 'green'
                            }
                            sales_dist = agent_df['sales_status'].value_counts().reindex(['Follow up', 'Not interested', 'Converted'], fill_value=0)
                            fig_sales = px.pie(
                                sales_dist,
                                names=sales_dist.index,
                                values=sales_dist.values,
                                title=f"{selected_agent} - Sales Status Distribution",
                                hole=0.4,
                                color=sales_dist.index,
                                color_discrete_map=sales_status_colors
                            )
                            fig_sales.update_traces(
                                hovertemplate="<b>%{label}</b><br>Calls: %{value}<br>Share: %{percent:.2f}%",
                                textinfo='percent+label',
                                texttemplate='%{percent:.0%}' if '%{percent}' != '0%' else '',
                                textposition='inside'
                            )
                            st.plotly_chart(fig_sales, use_container_width=True)
                    else:
                        st.info("No agents found in the filtered data to display individual performance.")

                else:
                    if agent_view_option == "Top 5 Agents":
                        selected_agents = agent_stats.head(5)
                        view_title_prefix = "Top 5 "
                    else:
                        selected_agents = agent_stats
                        view_title_prefix = "All "

                    fig_initial_calls = px.bar(
                        selected_agents,
                        x='agent',
                        y='Total_Initial_Calls',
                        title=f'{view_title_prefix}Agents by Total Initial Call Volume',
                        labels={'Total_Initial_Calls': 'Total Initial Calls', 'agent': 'Agent'},
                        color='agent',
                        color_discrete_sequence=px.colors.qualitative.Plotly
                    )
                    fig_initial_calls.update_traces(
                        hovertemplate="<b>Agent:</b> %{x}<br><b>Total Initial Calls:</b> %{y}<extra></extra>"
                    )
                    fig_initial_calls.update_layout(showlegend=False)
                    st.plotly_chart(fig_initial_calls, use_container_width=True)
                    
                    fig_follow_up = px.bar(
                        selected_agents,
                        x='agent',
                        y='Total_Follow_Up_Calls',
                        title=f'{view_title_prefix}Agents by Total Follow-Up Call Volume',
                        labels={'Total_Follow_Up_Calls': 'Total Follow-Up Calls', 'agent': 'Agent'},
                        color='agent',
                        color_discrete_sequence=px.colors.qualitative.Safe
                    )
                    fig_follow_up.update_traces(
                        hovertemplate="<b>Agent:</b> %{x}<br><b>Total Follow-Up Calls:</b> %{y}<extra></extra>"
                    )
                    fig_follow_up.update_layout(showlegend=False)
                    st.plotly_chart(fig_follow_up, use_container_width=True)

                    fig_answered_calls = px.bar(
                        selected_agents,
                        x='agent',
                        y='Total_Answered_Calls',
                        title=f'{view_title_prefix}Agents by Total Answered Calls',
                        labels={'Total_Answered_Calls': 'Total Answered Calls', 'agent': 'Agent'},
                        color='agent',
                        color_discrete_sequence=px.colors.qualitative.Plotly
                    )
                    fig_answered_calls.update_traces(
                        hovertemplate="<b>Agent:</b> %{x}<br><b>Total Answered Calls:</b> %{y}<extra></extra>"
                    )
                    fig_answered_calls.update_layout(showlegend=False)
                    st.plotly_chart(fig_answered_calls, use_container_width=True)


                    fig2 = px.bar(
                        selected_agents,
                        x='agent',
                        y='Answered_Rate',
                        title=f'{view_title_prefix}Agents by Answered Rate',
                        labels={'Answered_Rate': 'Answered Rate (%)', 'agent': 'Agent'},
                        color='agent',
                        color_discrete_sequence=px.colors.qualitative.Plotly
                    )
                    fig2.update_traces(
                        hovertemplate="<b>Agent:</b> %{x}<br><b>Answered Rate:</b> %{y:.1f}%<extra></extra>"
                    )
                    fig2.update_layout(showlegend=False, yaxis_range=[0, 100])
                    st.plotly_chart(fig2, use_container_width=True)

                    fig3 = px.bar(
                        selected_agents,
                        x='agent',
                        y='Sales_Follow_Up_Rate',  
                        title=f'{view_title_prefix}Agents by Sales Follow Up Rate (from Initial Calls)',
                        labels={'Sales_Follow_Up_Rate': 'Sales Follow Up Rate (%)', 'agent': 'Agent'},  
                        color='agent',
                        color_discrete_sequence=px.colors.qualitative.Plotly
                    )
                    fig3.update_traces(
                        hovertemplate="<b>Agent:</b> %{x}<br><b>Sales Follow-Up Rate:</b> %{y:.1f}%<extra></extra>"
                    )
                    fig3.update_layout(showlegend=False, yaxis_range=[0, 100])
                    st.plotly_chart(fig3, use_container_width=True)

                    fig4 = px.bar(
                        selected_agents,
                        x='agent',
                        y='Total_Sales',
                        title=f'{view_title_prefix}Agents by Total Sales',
                        labels={'Total_Sales': 'Total Sales ($)', 'agent': 'Agent'},
                        color='agent',
                        color_discrete_sequence=px.colors.qualitative.Plotly
                    )
                    fig4.update_traces(
                        hovertemplate="<b>Agent:</b> %{x}<br><b>Total Sales:</b> $%{y:,.2f}<extra></extra>"
                    )
                    fig4.update_layout(showlegend=False)
                    st.plotly_chart(fig4, use_container_width=True)

                    st.markdown("### Performance Metrics Summary")
                    if not selected_agents.empty:
                        st.markdown(f"- **Highest Initial Call Volume:** {selected_agents.nlargest(1, 'Total_Initial_Calls').iloc[0]['agent']} ({selected_agents.nlargest(1, 'Total_Initial_Calls').iloc[0]['Total_Initial_Calls']} calls)")  
                        st.markdown(f"- **Highest Follow-Up Call Volume:** {selected_agents.nlargest(1, 'Total_Follow_Up_Calls').iloc[0]['agent']} ({selected_agents.nlargest(1, 'Total_Follow_Up_Calls').iloc[0]['Total_Follow_Up_Calls']} calls)")
                        st.markdown(f"- **Highest Answered Calls:** {selected_agents.nlargest(1, 'Total_Answered_Calls').iloc[0]['agent']} ({selected_agents.nlargest(1, 'Total_Answered_Calls').iloc[0]['Total_Answered_Calls']} calls)")
                        st.markdown(f"- **Best Answered Rate:** {selected_agents.nlargest(1, 'Answered_Rate').iloc[0]['agent']} ({selected_agents.nlargest(1, 'Answered_Rate').iloc[0]['Answered_Rate']:.1f}%)")
                        st.markdown(f"- **Top Sales Performer:** {selected_agents.nlargest(1, 'Total_Sales').iloc[0]['agent']} (${selected_agents.nlargest(1, 'Total_Sales').iloc[0]['Total_Sales']:,.2f})")
                    else:
                        st.info("No agent data to display summaries.")
            else:
                st.info("No data available for agent performance analysis with current filters.")
        
        
    elif tab_key == "country":
        with st.container():
            st.subheader(tabs["country"])

            analysis_type = st.radio("Select Analysis Type", ["Overall Analysis", "Individual Country Analysis"], horizontal=True, key="country_analysis_radio")

            country_stats = filtered_df.groupby('country_name').agg(
                Total_Initial_Calls=('is_initial_call', 'sum'),
                Total_Follow_Up_Calls=('total_follow_up_calls', 'sum'),
                Total_Answered_Calls=('status', lambda x: (x == 'Answered').sum()),
                Answered_Rate=('status', lambda x: (x == 'Answered').mean() * 100),
                Sales_Follow_Up_Rate=('sales_status', lambda x: (x == 'Follow up').mean() * 100),
                Total_Sales=('sales_amount', 'sum'),
                Avg_Sale=('sales_amount', 'mean')
            )
            country_stats.columns = ['Total_Initial_Calls', 'Total_Follow_Up_Calls', 'Total_Answered_Calls', 'Answered_Rate', 'Sales_Follow_Up_Rate', 'Total_Sales', 'Avg_Sale']
            country_stats = country_stats.sort_values('Total_Initial_Calls', ascending=False)


            if analysis_type == "Overall Analysis":
                st.markdown("### Overall Country Performance")
                
                # Chart for Total Initial Calls
                top_initial_calls_countries = country_stats.head(10).sort_values('Total_Initial_Calls')
                fig_initial_calls_country = px.bar(
                    top_initial_calls_countries,
                    x='Total_Initial_Calls',
                    y=top_initial_calls_countries.index,
                    orientation='h',
                    title="Top 10 Countries by Total Initial Call Volume",
                    color='Total_Initial_Calls',
                    color_continuous_scale='Blues'
                )
                fig_initial_calls_country.update_traces(hovertemplate='<b>%{y}</b><br>Total Initial Calls: %{x}')
                fig_initial_calls_country.update_layout(xaxis_title='Total Initial Calls', yaxis_title='Country')
                st.plotly_chart(fig_initial_calls_country, use_container_width=True)

                # Chart for Total Follow-Up Calls by Country
                top_follow_ups = country_stats.nlargest(10, 'Total_Follow_Up_Calls').sort_values('Total_Follow_Up_Calls')
                fig_country_follow_up = px.bar(
                    top_follow_ups,
                    x='Total_Follow_Up_Calls',
                    y=top_follow_ups.index,
                    orientation='h',
                    title="Top 10 Countries by Total Follow-Up Call Volume",
                    color='Total_Follow_Up_Calls',
                    color_continuous_scale='Purples'
                )
                fig_country_follow_up.update_traces(hovertemplate='<b>%{y}</b><br>Total Follow-Up Calls: %{x}')
                fig_country_follow_up.update_layout(xaxis_title='Total Follow-Up Calls', yaxis_title='Country')
                st.plotly_chart(fig_country_follow_up, use_container_width=True)

                # Chart for Total Answered Calls by Country
                top_answered_calls_countries = country_stats.nlargest(10, 'Total_Answered_Calls').sort_values('Total_Answered_Calls')
                fig_answered_calls_country = px.bar(
                    top_answered_calls_countries,
                    x='Total_Answered_Calls',
                    y=top_answered_calls_countries.index,
                    orientation='h',
                    title="Top 10 Countries by Total Answered Calls",
                    color='Total_Answered_Calls',
                    color_continuous_scale='Greens'
                )
                fig_answered_calls_country.update_traces(hovertemplate='<b>%{y}</b><br>Total Answered Calls: %{x}')
                fig_answered_calls_country.update_layout(xaxis_title='Total Answered Calls', yaxis_title='Country')
                st.plotly_chart(fig_answered_calls_country, use_container_width=True)


                answered_top = country_stats[country_stats['Total_Initial_Calls'] > 0].sort_values('Answered_Rate', ascending=False).head(10)
                fig_answered_top = px.bar(
                    answered_top.sort_values('Answered_Rate'),
                    x='Answered_Rate',
                    y=answered_top.sort_values('Answered_Rate').index,
                    orientation='h',
                    title="Top 10 Countries by Answered Rate",
                    color='Answered_Rate',
                    color_continuous_scale='Greens'
                )
                fig_answered_top.update_traces(hovertemplate='<b>%{y}</b><br>Answered Rate: %{x:.2f}%')
                fig_answered_top.update_layout(xaxis_title='Answered Rate', yaxis_title='Country')
                st.plotly_chart(fig_answered_top, use_container_width=True)

                answered_sorted = country_stats.sort_values('Answered_Rate', ascending=False)
                fig4 = px.bar(
                    answered_sorted,
                    x='Answered_Rate',
                    y=answered_sorted.index,
                    orientation='h',
                    title="All Countries by Answered Rate",
                    color='Answered_Rate',
                    color_continuous_scale='Greens'
                )
                fig4.update_traces(
                    hovertemplate='<b>%{y}</b><br>Answered Rate: %{x:.2f}%'
                )
                fig4.update_layout(xaxis_title='Answered Rate', yaxis_title='Country')
                st.plotly_chart(fig4, use_container_width=True)

                top_sales = country_stats.sort_values('Total_Sales', ascending=False).head(10)
                fig3 = px.bar(
                    top_sales.sort_values('Total_Sales'),
                    x='Total_Sales',
                    y=top_sales.sort_values('Total_Sales').index,
                    orientation='h',
                    title="Top 10 Countries by Total Sales",
                    color='Total_Sales',
                    color_continuous_scale='Reds'
                )
                fig3.update_traces(hovertemplate='<b>%{y}</b><br>Total Sales: $%{x:,.2f}')
                fig3.update_layout(xaxis_title='Total Sales', yaxis_title='Country')
                st.plotly_chart(fig3, use_container_width=True)

                sales_sorted = country_stats.sort_values('Total_Sales', ascending=False)
                fig6 = px.bar(
                    sales_sorted,
                    x='Total_Sales',
                    y=sales_sorted.index,
                    orientation='h',
                    title="All Countries by Total Sales",
                    color='Total_Sales',
                    color_continuous_scale='Reds'
                )
                fig6.update_traces(
                    hovertemplate='<b>%{y}</b><br>Total Sales: $%{x:,.2f}'
                )
                fig6.update_layout(xaxis_title='Total Sales', yaxis_title='Country')
                st.plotly_chart(fig6, use_container_width=True)


            elif analysis_type == "Individual Country Analysis":
                country_list = country_stats.index.tolist()
                if country_list:
                    selected_country = st.selectbox("Select a Country", country_list, key="selected_country_selectbox")

                    if selected_country in country_stats.index:
                        country_data = country_stats.loc[selected_country]
                        
                        st.markdown(f"### Performance for **{selected_country}**")
                        
                        total_initial_calls_country = int(country_data['Total_Initial_Calls'])
                        total_follow_up_calls_country = int(country_data['Total_Follow_Up_Calls'])
                        total_answered_calls_country = int(country_data['Total_Answered_Calls'])
                        answered_rate_country = float(country_data['Answered_Rate'])
                        sales_follow_up_rate_country = float(country_data['Sales_Follow_Up_Rate'])  
                        total_sales_country = float(country_data['Total_Sales'])
                        avg_sale_country = float(country_data['Avg_Sale'])
                                                                        
                        col1, col2, col3, col4, col5 = st.columns(5)
                        with col1:
                            st.metric("Total Initial Calls", total_initial_calls_country)
                        with col2:  
                            st.metric("Total Follow-Up Calls", total_follow_up_calls_country)
                        with col3:
                            st.metric("Answered Calls", f"{total_answered_calls_country} ({answered_rate_country:.1f}%)")
                        with col4:
                            st.metric("Sales Follow Up Rate", f"{sales_follow_up_rate_country:.1f}%")  
                        with col5:
                            st.metric("Total Sales", f"${total_sales_country:,.2f}")  
                        
                        country_call_dist = filtered_df[filtered_df['country_name'] == selected_country]['status'].value_counts().reindex(['Answered', 'Not answered', 'Voicemail', 'Invalid number'], fill_value=0)
                        country_call_dist = country_call_dist[country_call_dist > 0]
                        call_outcome_colors = {
                            'Answered': 'green',
                            'Not answered': 'gray',
                            'Invalid number': 'red',
                            'Voicemail': 'blue'
                        }

                        fig_call = px.pie(
                            country_call_dist,
                            names=country_call_dist.index,
                            values=country_call_dist.values,
                            title=f"{selected_country} - Call Outcome",
                            hole=0.4,
                            color=country_call_dist.index,
                            color_discrete_map=call_outcome_colors
                        )
                        fig_call.update_traces(
                            hovertemplate="<b>%{label}</b><br>Calls: %{value}<br>Share: %{percent:.2f}%",
                            textinfo='percent+label',
                            texttemplate='%{percent:.0%}' if '%{percent}' != '0%' else '',
                            textposition='inside'
                        )
                        st.plotly_chart(fig_call, use_container_width=True)

                        country_sales_df_filtered = filtered_df[filtered_df['country_name'] == selected_country].copy()
                        country_sales_df_filtered['sales_status'] = country_sales_df_filtered['sales_status'].astype(str).str.strip()
                        country_sales_df_filtered['sales_status'].replace('', np.nan, inplace=True)
                        country_sales_df_filtered.dropna(subset=['sales_status'], inplace=True)

                        valid_sales_statuses_country = country_sales_df_filtered['sales_status'].dropna().unique().tolist()
                        country_sales_dist = country_sales_df_filtered['sales_status'].value_counts().reindex(valid_sales_statuses_country, fill_value=0)
                        
                        sales_status_colors = {
                            'Follow up': 'purple',
                            'Not interested': 'red',
                            'Converted': 'green'
                        }
                        color_scale = px.colors.qualitative.Plotly
                        dynamic_sales_colors = {status: sales_status_colors.get(status, color_scale[i % len(color_scale)]) for i, status in enumerate(valid_sales_statuses_country)}


                        fig_sales = px.pie(
                            country_sales_dist,
                            names=country_sales_dist.index,
                            values=country_sales_dist.values,
                            title=f"{selected_country} - Sales Status",
                            hole=0.4,
                            color=country_sales_dist.index,
                            color_discrete_map=dynamic_sales_colors
                        )
                        fig_sales.update_traces(
                            hovertemplate="<b>%{label}</b><br>Leads: %{value}<br>Share: %{percent:.2f}%",
                            textinfo='percent+label',
                            texttemplate='%{percent:.0%}' if '%{percent}' != '0%' else '',
                            textposition='inside'
                        )
                        st.plotly_chart(fig_sales, use_container_width=True)

                    else:
                        st.warning(f"No data available for {selected_country}.")
                else:
                    st.info("No countries found in the filtered data to analyze.")
            else:
                st.info("No data available for country analysis with current filters.")
        
    elif tab_key == "call":
        with st.container():
            st.subheader(tabs["call"])

            if not filtered_df.empty:
                valid_outcomes = ['Answered', 'Not answered', 'Voicemail', 'Invalid number']
                color_map = {
                    'Answered': 'green',
                    'Not answered': 'gray',
                    'Voicemail': 'blue',
                    'Invalid number': 'red'
                }

                call_outcome_df_temp = filtered_df.copy()
                call_outcome_df_temp['call_outcome'] = call_outcome_df_temp['status'].str.extract(
                    r'(Answered|Not answered|Voicemail|Invalid number)', flags=re.IGNORECASE
                )
                call_outcome_df_temp['call_outcome'].fillna('Other', inplace=True)
                call_outcome_df_temp = call_outcome_df_temp[call_outcome_df_temp['call_outcome'].isin(valid_outcomes)]
                
                if not call_outcome_df_temp.empty:
                    outcome_dist = call_outcome_df_temp['call_outcome'].value_counts(normalize=True) * 100
                    outcome_dist = outcome_dist.sort_values(ascending=False).round(1)

                    outcome_df = outcome_dist.reset_index()
                    outcome_df.columns = ['call_outcome', 'Percentage']

                    fig = px.bar(
                        outcome_df,
                        x='call_outcome',
                        y='Percentage',
                        title="Call Outcome Distribution by Status",
                        color='call_outcome',
                        color_discrete_map=color_map,
                        text=outcome_df['Percentage'].astype(str) + '%'
                    )
                    fig.update_traces(
                        textposition='outside',
                        hovertemplate='<b>Outcome:</b> %{x}<br><b>Percentage:</b> %{y:.1f}%<extra></extra>'
                    )
                    fig.update_layout(
                        yaxis_title="Percentage (%)",
                        xaxis_title="Call Outcome",
                        hoverlabel=dict(
                            bgcolor="black",
                            font_size=12,
                            font_family="Arial",
                            font_color="white",
                            bordercolor="lightgray"
                        )
                    )

                    st.plotly_chart(fig, use_container_width=True)

                    fig2 = px.pie(
                        outcome_df,
                        names='call_outcome',
                        values='Percentage',
                        title="Call Outcome Distribution by Status (Donut Chart)",
                        hole=0.4,
                        color='call_outcome',
                        color_discrete_map=color_map
                    )
                    fig2.update_traces(
                        hovertemplate='<b>%{label}</b><br><b>Percentage:</b> %{percent}',
                        textinfo='percent+label',
                        text=outcome_dist.round(1).astype(str) + '%'
                    )

                    fig2.update_layout(
                        hoverlabel=dict(
                            bgcolor="black",
                            font_size=12,
                            font_family="Arial",
                            font_color="white",
                            bordercolor="lightgray"
                        )
                    )

                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info("No valid call outcome data found with current filters.")


    elif tab_key == "pipeline":
        with st.container():
            st.subheader(tabs["pipeline"])
            
            answered_df = filtered_df[filtered_df['status'].str.strip() == 'Answered'].copy()

            answered_df['sales_status'] = answered_df['sales_status'].astype(str).str.strip()
            # Exclude 'nan' or empty string sales_status values
            answered_df = answered_df[answered_df['sales_status'].notna() & (answered_df['sales_status'] != '') & (answered_df['sales_status'].str.lower() != 'nan')]

            answered_df['sales_amount'] = pd.to_numeric(answered_df['sales_amount'], errors='coerce').fillna(0)

            valid_sales_statuses_pipeline = answered_df['sales_status'].dropna().unique().tolist()

            preferred_order = ['Not interested', 'Follow up', 'Converted']

            final_valid_statuses_pipeline = [s for s in preferred_order if s in valid_sales_statuses_pipeline] + \
                                            [s for s in valid_sales_statuses_pipeline if s not in preferred_order]

            answered_df = answered_df[answered_df['sales_status'].isin(final_valid_statuses_pipeline)]

            if not answered_df.empty:
                pipeline_dist = answered_df['sales_status'].value_counts()
                pipeline_dist.fillna(0, inplace=True)  
                
                color_scale_pipeline = px.colors.qualitative.Plotly
                color_map_pipeline = {status: color_scale_pipeline[i % len(color_scale_pipeline)] for i, status in enumerate(final_valid_statuses_pipeline)}
                color_map_pipeline.update({
                    'Not interested': 'red',
                    'Converted': 'green',
                    'Follow up': 'purple'
                })

                # Filter out 'nan' values from display_df_pipeline to ensure they don't appear in charts
                display_df_pipeline = answered_df[answered_df['sales_status'].notna() & (answered_df['sales_status'] != '')]

                fig1 = px.pie(
                    pipeline_dist,
                    names=pipeline_dist.index,
                    values=pipeline_dist.values,
                    title="Sales Pipeline Distribution (Overall)",
                    hole=0.4,
                    color=pipeline_dist.index,
                    color_discrete_map=color_map_pipeline
                )
                fig1.update_traces(
                    hovertemplate='<b>%{label}</b><br>Count: %{value}<br>Percentage: %{percent}',
                    textinfo='label+percent'
                )
                st.plotly_chart(fig1, use_container_width=True)

                st.markdown("### Key Sales Pipeline Rates:")
                
                st.markdown("---")

                view_option = st.radio("Select Country View", ["Top 5 Countries", "All Countries"], horizontal=True, key="pipeline_country_view_radio")  

                country_df_pipeline = answered_df.copy()

                if not country_df_pipeline.empty:
                    country_df_pipeline.dropna(subset=['country_name'], inplace=True)
                    
                    cross = pd.crosstab(
                        country_df_pipeline['country_name'],
                        country_df_pipeline['sales_status'],
                        normalize='index'
                    ).mul(100).round(1)

                    for status in final_valid_statuses_pipeline:
                        if status not in cross.columns:
                            cross[status] = 0

                    cross = cross[final_valid_statuses_pipeline]
                    
                    cross.fillna(0, inplace=True)

                    if view_option == "Top 5 Countries":
                        cross_display = cross.nlargest(5, 'Converted')
                        view_title_suffix = " (Top 5 Converting Countries)"
                    else:
                        cross_display = cross
                        view_title_suffix = ""

                    fig2 = px.bar(
                        cross_display.reset_index(),
                        x='country_name',
                        y=final_valid_statuses_pipeline,
                        title=f"Sales Status Distribution by Country{view_title_suffix}",
                        labels={'country_name': 'Country', 'value': 'Percentage (%)', 'variable': 'Sales Status'},
                        color_discrete_map=color_map_pipeline,
                        barmode='stack'
                    )

                    fig2.update_traces(
                        hovertemplate='<b>%{x}</b><br>' +
                                        '%{fullData.name}: <b>%{y:.1f}%</b><extra></extra>'
                    )
                    
                    fig2.update_layout(
                        xaxis=dict(title='Country'),
                        legend_title='Sales Status',
                        hovermode='closest',
                        hoverlabel=dict(
                            bgcolor="black",
                            font_size=13,
                            font_family="Arial",
                            font_color="white",
                            bordercolor="lightgray"
                        )
                    )
                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info("No 'Answered' calls with valid sales statuses found for the selected filters to display the pipeline analysis.")
            else:
                st.info("No 'Answered' calls found for the selected filters to display the pipeline analysis.")


    
    elif tab_key == "followup":  
        with st.container():
            st.subheader(tabs["followup"])

            st.markdown("---")
            st.markdown("### 📊 Follow-Up Call Counts by Agent")

            if not filtered_df.empty:
                # Ensure 'agent' and 'total_follow_up_calls' columns are used consistently
                # 'total_follow_up_calls' is calculated in load_full_sales_data_from_db to include 2nd-10th calls
                agent_follow_up_counts = filtered_df.groupby('agent')['total_follow_up_calls'].sum().reset_index()
                
                # Filter out agents with empty names or 0 follow-up calls if desired
                agent_follow_up_counts = agent_follow_up_counts[
                    (agent_follow_up_counts['agent'].str.strip().astype(bool)) &
                    (agent_follow_up_counts['total_follow_up_calls'] > 0) # Only show agents who actually have follow-up calls
                ]
                agent_follow_up_counts = agent_follow_up_counts.sort_values(by='total_follow_up_calls', ascending=False)

                if not agent_follow_up_counts.empty:
                    fig_agent_follow_ups = px.bar(
                        agent_follow_up_counts,
                        x='agent',
                        y='total_follow_up_calls',
                        title='Total Follow-Up Calls by Agent',
                        labels={'total_follow_up_calls': 'Number of Follow-Up Calls', 'agent': 'Agent'},
                        color='total_follow_up_calls',
                        color_continuous_scale='Purples'
                    )
                    fig_agent_follow_ups.update_traces(hovertemplate="<b>Agent:</b> %{x}<br><b>Follow-Up Calls:</b> %{y}<extra></extra>")
                    fig_agent_follow_ups.update_layout(xaxis_title='Agent', yaxis_title='Total Follow-Up_Calls')
                    st.plotly_chart(fig_agent_follow_ups, use_container_width=True)
                else:
                    st.info("No agents found with recorded follow-up calls in the filtered data.")
            else:
                st.info("No data available for follow-up call counting.")
            
            st.markdown("---")
            
            analysis_reference_date = end_date

            # --- Section 1: Upcoming Follow-Up Calls (based on next_follow_up_date) ---
            st.markdown("### 🗓️ Upcoming Follow-Up Calls")
            
            # Filter for rows that have a future 'next_follow_up_date'
            upcoming_scheduled_df = filtered_df[
                (filtered_df['next_follow_up_date'].notna()) &
                (filtered_df['next_follow_up_date'] > analysis_reference_date)
            ].copy()

            if not upcoming_scheduled_df.empty:
                # Columns to include, ensuring 'name' is in the initial list
                display_cols_base = ['agent', 'name', 'country_name', 'email', 'number', 'next_follow_up_date', 'next_follow_up_time']
                
                
                # Combine base and call date columns, and filter to only include existing columns in the DataFrame
                display_cols_upcoming_final = [col for col in (display_cols_base) if col in upcoming_scheduled_df.columns]
                
                upcoming_scheduled_df_display = upcoming_scheduled_df[display_cols_upcoming_final].sort_values(by='next_follow_up_date', ascending=True)
                
                # Define a common rename map for consistency across sections
                common_rename_map = {
                    'agent': 'Agent', 
                    'name': 'Name', 
                    'country_name': 'Country', 
                    'email': 'Email', 
                    'number': 'Number', 
                    'next_follow_up_date': 'Next Follow Up Date', 
                    'next_follow_up_time': 'Next Follow Up Time'
                }
                
                upcoming_scheduled_df_display = upcoming_scheduled_df_display.rename(columns=common_rename_map)

                st.write(f"### Upcoming Follow-Up Calls ({len(upcoming_scheduled_df_display)} entries):")
                st.dataframe(upcoming_scheduled_df_display.reset_index(drop=True), use_container_width=True)
            else:
                st.info("No upcoming follow-up calls scheduled with current filters.")



            st.markdown("---")
            # --- Section 3: Follow Up Meetings Completed So Far (All actual past call dates) ---
            st.markdown("### ✅ Follow Up Calls Completed So Far")

            # Prepare a list to store all distinct completed call events
            completed_events = []
            
            # Columns representing actual past calls
            # --- MODIFICATION START ---
            # Re-include 'next_follow_up_date' here
            historical_call_date_cols_map = {
                #'first_call_date': 'Initial Call',
                'next_follow_up_date': 'Next Follow-Up Call Completed', # <-- UNCOMMENTED/ADDED THIS LINE
            }
            # --- MODIFICATION END ---

            for index, row in filtered_df.iterrows():
                common_info = {
                    'Agent': row['agent'],
                    'Name': row['name'],
                    'Email': row['email'],
                    'Number': row['number'],
                    'Country': row['country_name'],
                    'Sales Status': row['sales_status']
                }
                
                for col_name, call_stage_name in historical_call_date_cols_map.items():
                    if col_name in row.index and pd.notna(row[col_name]):
                        call_date = row[col_name]
                        # Ensure the call date is within the selected filter range (inclusive of end_date)
                        if call_date <= end_date:
                            completed_events.append({
                                **common_info,
                                'Follow Up Date': call_date,
                                'Call Stage': call_stage_name
                            })
            
            completed_events_df = pd.DataFrame(completed_events)

            if not completed_events_df.empty:
                completed_events_df['Follow Up Date'] = pd.to_datetime(completed_events_df['Follow Up Date']).dt.date
                
                # --- MODIFICATION START ---
                # Re-evaluate drop_duplicates:
                # If you want the count of "completed_events_df" to EXACTLY match "total_follow_up_calls_overall",
                # and "total_follow_up_calls_overall" performs no further deduplication than what happens when
                # summing the individual boolean `notna() & <= filter_end_date` flags,
                # then this `drop_duplicates` line must be removed or commented out for a direct count match.
                # The assumption is that each valid date entry (first_call_date or next_follow_up_date) represents
                # a unique call event to be counted.
                # completed_events_df = completed_events_df.drop_duplicates(
                #     subset=['Name', 'Follow Up Date', 'Call Stage'], keep='first'
                # )
                # --- MODIFICATION END ---
                
                completed_events_df = completed_events_df.sort_values(by='Follow Up Date', ascending=False)

                st.info(f"You have completed {len(completed_events_df)} follow-up meetings so far!")
                display_cols_completed = ['Agent', 'Name', 'Email', 'Number', 'Country', 'Sales Status', 'Follow Up Date', 'Call Stage']
                st.dataframe(completed_events_df[display_cols_completed].reset_index(drop=True), use_container_width=True)
            else:
                st.info("No past follow-up meetings found.")

    elif tab_key == "report":
        with st.container():
            st.subheader(tabs["report"])
            st.subheader("📥 Generate and Download Report")

            if not filtered_df.empty:
                report_date = datetime.now().strftime('%Y-%m-%d %H:%M')
                date_range = f"{filtered_df['date_called'].min().date()} to {filtered_df['date_called'].max().date()}"
                
                total_initial_calls_report = filtered_df['is_initial_call'].sum()  
                total_follow_up_calls_report = filtered_df['total_follow_up_calls'].sum()
                total_answered_calls_report = (filtered_df['status'] == 'Answered').sum()

                total_entries = len(filtered_df)
                
                answered_rate_report = (filtered_df['status'] == 'Answered').mean() * 100
                total_sales_report = filtered_df['sales_amount'].sum()
                
                top_agents_report = filtered_df['agent'].value_counts().head(3).index.tolist()
                top_countries_report = filtered_df['country_name'].value_counts().head(3).index.tolist()
                if 'issues' in filtered_df.columns:
                    outcome_summary_report = (
                        filtered_df[filtered_df['issues'] != 'N/A']['issues']
                        .value_counts()
                        .head(3)
                        .index
                        .tolist()
                    )
                else:
                    outcome_summary_report = []

                report_df = pd.DataFrame({
                    'Metric': [
                        'Report Date', 'Date Range', 'Total Dataset Entries', 'Total Initial Calls', 'Total Follow-Up Calls',
                        'Total Answered Calls', 'Answered Rate', 'Total Sales',
                        'Top 3 Agents', 'Top 3 Countries', 'Common Issues'
                    ],
                    'Value': [
                        report_date, date_range, total_entries, total_initial_calls_report, total_follow_up_calls_report,
                        total_answered_calls_report,
                        f"{answered_rate_report:.1f}%", f"${total_sales_report:,.2f}" if not pd.isna(total_sales_report) else "N/A",
                        ", ".join(top_agents_report), ", ".join(top_countries_report), ", ".join(outcome_summary_report)
                    ]
                })

                st.dataframe(report_df)

                csv_buffer = io.StringIO()
                report_df.to_csv(csv_buffer, index=False)

                st.download_button(
                    label="📄 Download CSV Report",
                    data=csv_buffer.getvalue().encode('utf-8'),
                    file_name=f"sales_report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime='text/csv',
                    key="download_report_button"
                )
            else:
                st.info("No data available to generate a report with current filters.")
else:
    st.info("No sales data available to display the dashboard. Please check your data source or filters.")

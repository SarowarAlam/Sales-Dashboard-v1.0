from flask import Flask, request, jsonify
import subprocess
import os
from datetime import datetime

app = Flask(__name__)

# IMPORTANT: Set this to a strong, secret key for basic security
# Google Apps Script will send this key in the header
WEBHOOK_SECRET_KEY = os.getenv("WEBHOOK_SECRET_KEY", "a_secret_key")

@app.route('/sync-sheets', methods=['POST'])
def sync_sheets():
    # Basic security check: Validate the secret key
    if request.headers.get('X-Secret-Key') != WEBHOOK_SECRET_KEY:
        print(f"[{datetime.now()}] Unauthorized access attempt.")
        return jsonify({"message": "Unauthorized"}), 401

    # Log the request
    print(f"[{datetime.now()}] Webhook received, initiating sync...")

    # Define the path to your main synchronization script
    # This assumes 'google_sheets_to_postgres_sync.py' is in the SAME directory as 'webhook_server.py'
    sync_script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'google_sheets_to_postgres_sync.py')

    if not os.path.exists(sync_script_path):
        print(f"[{datetime.now()}] Error: Sync script not found at {sync_script_path}")
        return jsonify({"message": "Server error: Sync script not found"}), 500

    try:
        # Execute your main sync script and capture its output for debugging.
        # subprocess.run is blocking, meaning the Flask app waits for the sync script to finish.
        # This is good for debugging to see all output immediately.
        result = subprocess.run(['python3', sync_script_path],
                                capture_output=True, # Captures stdout and stderr
                                text=True,           # Decodes output as text
                                check=False)         # Do not raise CalledProcessError for non-zero exit codes

        # Log the sync script's output and errors to the Flask server's console
        if result.stdout:
            print(f"[{datetime.now()}] Sync Script STDOUT:\n{result.stdout}")
        if result.stderr:
            print(f"[{datetime.now()}] Sync Script STDERR:\n{result.stderr}")

        if result.returncode == 0:
            print(f"[{datetime.now()}] Sync process for '{sync_script_path}' completed successfully.")
            # You might want to return the sync script's output in the JSON response for more detailed Apps Script logging
            return jsonify({"message": "Sync process completed successfully!", "sync_output": result.stdout, "sync_error": result.stderr}), 200
        else:
            print(f"[{datetime.now()}] Sync process for '{sync_script_path}' failed with exit code {result.returncode}.")
            return jsonify({"message": f"Sync process failed with exit code {result.returncode}", "sync_output": result.stdout, "sync_error": result.stderr}), 500

    except Exception as e:
        print(f"[{datetime.now()}] Failed to launch or monitor sync script: {e}")
        return jsonify({"message": f"Failed to initiate sync: {str(e)}"}), 500

if __name__ == '__main__':
    print(f"[{datetime.now()}] Starting Flask webhook server on port 5000...")
    app.run(host='0.0.0.0', port=5000)

from flask import Flask, request, jsonify
from datetime import datetime
import time
import os

# --- Configuration ---
# Bind to 0.0.0.0 for public access (required by deployment platforms like Render)
HOST = "0.0.0.0"
# Use the PORT environment variable provided by the platform (default to 5000 for local dev)
PORT = int(os.environ.get("PORT", 5000))

# Create the Flask application instance
app = Flask(__name__)

# --- Wemos Device Simulation ---
def send_to_wemos(command: str):
    """
    Simulates sending data to the dedicated IoT device (Wemos).
    In a real system, this function would handle the low-level communication (e.g., MQTT, TCP/UDP).
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] -> Wemos: Processing command '{command}'...")
    
    # Simulate different processing times based on command type
    if command in ["HARD_ON", "HARD_OFF"]:
        time.sleep(0.05) # Fast response for critical controls
    elif command == "AUTO_OFF":
        time.sleep(0.5) # Slower response for mode changes
    
    # Generate simulated Wemos response
    if command == "HARD_ON":
        return f"Wemos: Successfully set GPIO high."
    elif command == "HARD_OFF":
        return f"Wemos: Successfully set GPIO low."
    elif command == "AUTO_OFF":
        return f"Wemos: Switched internal state to automatic management."
    else:
        return f"Wemos: Unknown command '{command}'."


@app.route('/command', methods=['POST'])
def receive_command():
    """
    Receives command requests from Server A (Command Forwarder).
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 400

    data = request.get_json()
    command = data.get('command')
    source = data.get('source', 'ServerA') # Default to ServerA, as it is the expected source
    
    if not command:
        return jsonify({"status": "error", "message": "Missing 'command' field in JSON payload"}), 400

    print(f"[{datetime.now().strftime('%H:%M:%S')}] **Received Command from {source}: '{command}'**")
    
    # Execute the command simulation
    wemos_response = send_to_wemos(command)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Wemos Simulation Result: {wemos_response}")

    # Send success response back to Server A
    return jsonify({
        "status": "success",
        "command_received": command,
        "wemos_status": wemos_response,
        "timestamp": datetime.now().isoformat()
    }), 200

# --- Local Run Block and Deployment Reference ---
if __name__ == '__main__':
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Server B (IoT Gateway) for local development on http://{HOST}:{PORT}")
    print("---")
    print("Render Deployment Start Command (for web service configuration):")
    print("gunicorn --bind 0.0.0.0:$PORT app:app")
    print("---")
    
    # Run the Flask app directly for local development compatibility
    try:
        app.run(host=HOST, port=PORT, debug=False) 
    except KeyboardInterrupt:
        print("\nServer B stopped by user.")
    except Exception as e:
        print(f"An error occurred during server startup: {e}")
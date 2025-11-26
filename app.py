import os
import json
import requests
import asyncio
from datetime import datetime
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse

# --- Configuration ---
app = FastAPI()
SERVER_B_URL = os.environ.get("SERVER_B_URL", "https://iot-gateway-89zp.onrender.com/command")
PHP_BACKEND_PERIOD_URL = "https://tristechhub.org.rw/projects/ATS/backend/main.php?action=is_current_time_in_period"
PHP_BACKEND_DEVICE_URL = "https://tristechhub.org.rw/projects/ATS/backend/main.php?action=get_user_device"

# --- State ---
user_to_device: Dict[str, str] = {}          # email → deviceName
user_clients: Dict[str, Set[WebSocket]] = {} # email → set of browser sockets

@app.on_event("startup")
async def start_php_polling():
    asyncio.create_task(check_php_backend())

@app.get("/")
async def root():
    return {"status": "ok", "message": "Server A running"}

# --- Browser WebSocket registration ---
@app.websocket("/ws/browser")
async def ws_browser(websocket: WebSocket, email: str = Query(...)):
    await websocket.accept()
    if email not in user_clients:
        user_clients[email] = set()
    user_clients[email].add(websocket)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Browser registered for {email}")

    # Lookup device for this user
    device_name = get_device_for_user(email)
    if device_name:
        await websocket.send_text(json.dumps({
            "type": "device_binding",
            "deviceName": device_name
        }))
        # Ask Server B if device is connected
        status = await check_device_status(device_name)
        await websocket.send_text(json.dumps({
            "type": "device_status",
            "deviceName": device_name,
            "status": status
        }))
    else:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "No device bound to this account"
        }))

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            if data.get("type") == "command":
                target_device = user_to_device.get(email)
                if target_device:
                    resp = await forward_command(data.get("payload", {}).get("action", ""), target_device)
                    await websocket.send_text(json.dumps({
                        "type": "ack",
                        "deviceId": target_device,
                        "response": resp
                    }))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "No device bound to your account"
                    }))
    except WebSocketDisconnect:
        pass
    finally:
        user_clients[email].discard(websocket)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Browser for {email} disconnected")

# --- Helpers ---
def get_device_for_user(email: str) -> str | None:
    if not email:
        return None
    if email in user_to_device:
        return user_to_device[email]
    try:
        resp = requests.post(PHP_BACKEND_DEVICE_URL,
                             data={"action": "get_user_device", "email": email},
                             timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("success") and data.get("device_name"):
            device_name = data["device_name"]
            user_to_device[email] = device_name
            return device_name
        return None
    except Exception as e:
        print(f"get_device_for_user error: {e}")
        return None

async def forward_command(command: str, target_device: str = None):
    """Forward command to Server B via HTTP POST."""
    full_url = SERVER_B_URL if SERVER_B_URL.endswith('/command') else f"{SERVER_B_URL}/command"
    payload = {"command": command, "source": "ServerA"}
    if target_device:
        payload["deviceId"] = target_device

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Forwarding '{command}' to Server B for {target_device}...")
    try:
        response = requests.post(full_url, json=payload, timeout=10)
        if response.status_code == 200:
            return f"✅ '{command}' forwarded. Server B replied: {response.text}"
        else:
            return f"⚠️ Error: Server B responded with status {response.status_code}."
    except requests.exceptions.RequestException as e:
        return f"❌ Failed to connect to Server B. {e.__class__.__name__}"

async def check_device_status(device_name: str) -> str:
    """Ask Server B if a device is connected (stub — could be an API call)."""
    # For now, just forward a status check command
    resp = await forward_command("STATUS_CHECK", device_name)
    if "replied" in resp:
        return "CONNECTED"
    return "DISCONNECTED"

async def check_php_backend():
    """Poll PHP backend every minute and forward AUTO_ON when active period is matched."""
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling PHP backend...")
        try:
            resp = requests.get(PHP_BACKEND_PERIOD_URL, timeout=10)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Failed to parse backend response: {resp.text}")
                    data = None

                if data and data.get("success") is True:
                    backend_msg = data.get("message", "No message")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ TIME MATCHED: {backend_msg} : {data.get('id')}")
                    # Forward AUTO_ON to Server B for each bound device
                    for email, device_name in user_to_device.items():
                        await forward_command("AUTO_ON", device_name)
                        # Notify all browser sessions for this user
                        for ws in list(user_clients.get(email, [])):
                            try:
                                await ws.send_text(json.dumps({
                                    "type": "auto_on_trigger",
                                    "deviceName": device_name,
                                    "message": backend_msg
                                }))
                            except Exception:
                                pass
                else:
                    backend_msg = data.get("message", "No message") if data else "No data"
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ TIME NOT MATCHED: {backend_msg}")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] PHP backend error: HTTP {resp.status_code}")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] check_php_backend error: {e}")

        await asyncio.sleep(60)

# --- Local run block ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)

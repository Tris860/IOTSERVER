import os
import json
import time
import asyncio
import requests
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI()

devices: Dict[str, WebSocket] = {}
last_ping: Dict[str, float] = {}

WEMOS_AUTH_URL = "https://tristechhub.org.rw/projects/ATS/backend/main.php?action=wemos_auth"
SERVER_A_CALLBACK_URL = os.environ.get("SERVER_A_CALLBACK_URL", "https://webserver-ft8c.onrender.com/notify/device-status")

PING_TIMEOUT = 180  # 3 minutes

@app.get("/")
def root():
    return JSONResponse({"status": "ok", "message": "Server B online"})


@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket, deviceId: str = Query(...)):
    username = websocket.headers.get("x-username")
    password = websocket.headers.get("x-password")
    print(f"Authenticating Wemos. Received headers → username={username}, password={password}")

    if not username or not password:
        await websocket.close(code=4000)
        reason = "Missing authentication headers"
        print(f"Authentication failed: {reason}")
        notify_server_a(deviceId, "REJECTED", {"reason": reason, "username": username, "password": password})
        return

    try:
        resp = requests.post(
            WEMOS_AUTH_URL,
            data={"action": "wemos_auth", "username": username, "password": password},
            timeout=10,
        )
        print(f"Backend replied HTTP {resp.status_code}: {resp.text}")  # show raw backend reply

        if resp.status_code != 200:
            reason = f"Backend error HTTP {resp.status_code}"
            await websocket.close(code=4001)
            print(f"Authentication failed: {reason}")
            notify_server_a(deviceId, "REJECTED", {"reason": reason, "username": username})
            return

        data = resp.json()
        print(f"Parsed backend JSON: {data}")  # show parsed JSON

        if not data.get("success"):
            reason = data.get("message", "Unknown rejection")
            await websocket.close(code=4002)
            print(f"Authentication failed: {reason}")
            notify_server_a(deviceId, "REJECTED", {"reason": reason, "username": username})
            return

        # Device authenticated
        deviceName = data.get("data", {}).get("device_name", username)
        initialCommand = "HARD_ON" if data.get("data", {}).get("hard_switch_enabled") else "HARD_OFF"

        await websocket.accept()
        devices[deviceName] = websocket
        last_ping[deviceName] = time.time()
        print(f"Wemos '{deviceName}' authenticated and connected with username={username}")

        # Send initial command
        await websocket.send_text(json.dumps({"type": "command", "payload": {"action": initialCommand}}))

        # Notify Server A
        notify_server_a(deviceName, "CONNECTED")

        # … rest of your loop …
    except Exception as e:
        reason = f"Internal error: {e}"
        print(f"Authentication error: {reason}")
        await websocket.close(code=1011)
        notify_server_a(deviceId, "REJECTED", {"reason": reason, "username": username})

@app.post("/command")
async def receive_command(request: Request):
    """Receive command from Server A and forward to devices"""
    data = await request.json()
    command = data.get("command")
    target = data.get("deviceId")

    print(f"[{time.strftime('%H:%M:%S')}] Received command: {command} for {target}")

    if not target:
        return {"status": "error", "message": "Missing deviceId"}

    if isinstance(target, list):
        results = {}
        for dev in target:
            results[dev] = await send_command_to_device(dev, command)
        return {"status": "multi", "results": results}

    return await send_command_to_device(target, command)

async def send_command_to_device(deviceName: str, command: str):
    ws = devices.get(deviceName)
    if ws:
        try:
            await ws.send_text(json.dumps({"type": "command", "payload": {"action": command}}))
            return {"status": "ok", "message": f"Command '{command}' sent to {deviceName}."}
        except Exception as e:
            # Clean up dead socket
            devices.pop(deviceName, None)
            last_ping.pop(deviceName, None)
            print(f"Socket for {deviceName} was closed. Removing from devices.")
            return {"status": "error", "message": f"Device {deviceName} not connected (socket closed)."}
    else:
        return {"status": "error", "message": f"Device {deviceName} not connected."}

def notify_server_a(deviceName: str, status: str, payload: dict = None):
    """Push device events back to Server A"""
    event = {"deviceName": deviceName, "status": status}
    if payload:
        event["payload"] = payload
    try:
        requests.post(SERVER_A_CALLBACK_URL, json=event, timeout=5)
    except Exception as e:
        print(f"Failed to notify Server A: {e}")

# --- Background task to check ping timeouts ---
@app.on_event("startup")
async def monitor_pings():
    async def check_loop():
        while True:
            now = time.time()
            for deviceName in list(last_ping.keys()):
                if now - last_ping[deviceName] > PING_TIMEOUT:
                    print(f"Device {deviceName} timed out (no ping).")
                    ws = devices.pop(deviceName, None)
                    last_ping.pop(deviceName, None)
                    if ws:
                        try:
                            await ws.close()
                        except:
                            pass
                    notify_server_a(deviceName, "DISCONNECTED")
            await asyncio.sleep(30)
    asyncio.create_task(check_loop())

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)

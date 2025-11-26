import os
import json
import time
import requests
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI()

devices: Dict[str, WebSocket] = {}
browsers: Set[WebSocket] = set()
last_ping: Dict[str, float] = {}

WEMOS_AUTH_URL = "https://tristechhub.org.rw/projects/ATS/backend/main.php?action=wemos_auth"

@app.get("/")
def root():
    return JSONResponse({"status": "ok", "message": "WebSocket gateway online"})

@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket, deviceId: str = Query(...)):
    """
    WebSocket endpoint for Wemos/IoT devices with authentication.
    Devices must send x-username and x-password headers.
    """
    # Extract headers
    username = websocket.headers.get("x-username")
    password = websocket.headers.get("x-password")
    print(f"Authenticating Wemos. username={username}")

    if not username or not password:
        await websocket.close(code=4000)
        print("Authentication failed: missing headers")
        return

    # Call PHP backend for authentication
    try:
        resp = requests.post(
            WEMOS_AUTH_URL,
            data={
                "action": "wemos_auth",
                "username": username,
                "password": password,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            await websocket.close(code=4001)
            print("Authentication failed: backend error")
            return

        data = resp.json()
        if not data.get("success"):
            await websocket.close(code=4002)
            print(f"Authentication failed: {data.get('message')}")
            return

        # Device authenticated
        deviceName = data.get("data", {}).get("device_name", username)
        initialCommand = "HARD_ON" if data.get("data", {}).get("hard_switch_enabled") else "HARD_OFF"

        await websocket.accept()
        devices[deviceName] = websocket
        last_ping[deviceName] = time.time()
        print(f"Wemos '{deviceName}' authenticated and connected.")

        # Send initial command if any
        await websocket.send_text(json.dumps({"type": "command", "payload": {"action": initialCommand}}))

        await notify_browsers({"type": "device_connected", "deviceId": deviceName})

        try:
            while True:
                msg = await websocket.receive_text()
                data = json.loads(msg)

                if data.get("type") == "ping":
                    last_ping[deviceName] = time.time()
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    continue

                await notify_browsers({
                    "type": "device_status",
                    "deviceId": deviceName,
                    "payload": data
                })
        except WebSocketDisconnect:
            pass
        finally:
            devices.pop(deviceName, None)
            last_ping.pop(deviceName, None)
            await notify_browsers({"type": "device_disconnected", "deviceId": deviceName})

    except Exception as e:
        print(f"Authentication error: {e}")
        await websocket.close(code=1011)  # Internal error
        return

@app.websocket("/ws/browser")
async def ws_browser(websocket: WebSocket):
    await websocket.accept()
    browsers.add(websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            if data.get("type") == "command":
                target = data.get("deviceId")
                payload = data.get("payload", {})
                if target in devices:
                    await devices[target].send_text(json.dumps({
                        "type": "command",
                        "payload": payload
                    }))
                    await websocket.send_text(json.dumps({
                        "type": "ack",
                        "deviceId": target
                    }))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "device offline"
                    }))
    except WebSocketDisconnect:
        pass
    finally:
        browsers.discard(websocket)

@app.post("/command")
async def receive_command(request: Request):
    data = await request.json()
    command = data.get("command")
    source = data.get("source", "unknown")
    target = data.get("deviceId")

    print(f"[{time.strftime('%H:%M:%S')}] Received command from {source}: {command}")

    if target:
        ws = devices.get(target)
        if ws:
            try:
                await ws.send_text(json.dumps({
                    "type": "command",
                    "payload": {"action": command}
                }))
                await notify_browsers({
                    "type": "server_command",
                    "source": source,
                    "command": command,
                    "target": target
                })
                return {"status": "ok", "message": f"Command '{command}' sent to {target}."}
            except Exception as e:
                return {"status": "error", "message": f"Failed to send to {target}: {e}"}
        else:
            return {"status": "error", "message": f"Device {target} not connected."}
    else:
        for deviceId, ws in devices.items():
            try:
                await ws.send_text(json.dumps({
                    "type": "command",
                    "payload": {"action": command}
                }))
            except Exception as e:
                print(f"Failed to send to {deviceId}: {e}")

        await notify_browsers({
            "type": "server_command",
            "source": source,
            "command": command,
            "target": "all"
        })
        return {"status": "ok", "message": f"Command '{command}' broadcasted to all devices."}

async def notify_browsers(event: dict):
    dead = []
    message = json.dumps(event)
    for ws in list(browsers):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        browsers.discard(ws)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)

import os
import json
import time
import asyncio
import requests
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import JSONResponse

# Create FastAPI app
app = FastAPI()

# Track connected devices and browsers
devices: Dict[str, WebSocket] = {}
browsers: Set[WebSocket] = set()
last_ping: Dict[str, float] = {}

PHP_BACKEND_URL = "https://tristechhub.org.rw/projects/ATS/backend/main.php?action=is_current_time_in_period"
@app.on_event("startup")
async def start_php_polling():
    asyncio.create_task(check_php_backend())


@app.get("/")
def root():
    return JSONResponse({"status": "ok", "message": "WebSocket gateway online"})

@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket, deviceId: str = Query(...)):
    await websocket.accept()
    devices[deviceId] = websocket
    last_ping[deviceId] = time.time()
    await notify_browsers({"type": "device_connected", "deviceId": deviceId})

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)

            if data.get("type") == "ping":
                last_ping[deviceId] = time.time()
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            await notify_browsers({
                "type": "device_status",
                "deviceId": deviceId,
                "payload": data
            })
    except WebSocketDisconnect:
        pass
    finally:
        devices.pop(deviceId, None)
        last_ping.pop(deviceId, None)
        await notify_browsers({"type": "device_disconnected", "deviceId": deviceId})

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
                    "payload": { "action": command }
                }))
                await notify_browsers({
                    "type": "server_command",
                    "source": source,
                    "command": command,
                    "target": target
                })
                return { "status": "ok", "message": f"Command '{command}' sent to {target}." }
            except Exception as e:
                return { "status": "error", "message": f"Failed to send to {target}: {e}" }
        else:
            return { "status": "error", "message": f"Device {target} not connected." }
    else:
        for deviceId, ws in devices.items():
            try:
                await ws.send_text(json.dumps({
                    "type": "command",
                    "payload": { "action": command }
                }))
            except Exception as e:
                print(f"Failed to send to {deviceId}: {e}")

        await notify_browsers({
            "type": "server_command",
            "source": source,
            "command": command,
            "target": "all"
        })

        return { "status": "ok", "message": f"Command '{command}' broadcasted to all devices." }

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

# --- PHP backend polling task ---
async def check_php_backend():
    while True:
        try:
            resp = requests.get(PHP_BACKEND_URL, timeout=10)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    print(f"[{time.strftime('%H:%M:%S')}] Failed to parse backend response: {resp.text}")
                    data = None

                if data:
                    if data.get("success") is True:
                        print(f"[{time.strftime('%H:%M:%S')}] PHP backend success: {data.get('message')}")
                    else:
                        print(f"[{time.strftime('%H:%M:%S')}] PHP backend failure: {data.get('message')}")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] PHP backend error: HTTP {resp.status_code}")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] check_php_backend error: {e}")

        await asyncio.sleep(60)  # poll every minute

# --- Local run block ---
if __name__ == "__main__":
    import uvicorn

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(check_php_backend())

    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)


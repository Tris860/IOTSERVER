import os
import json
import time
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import JSONResponse

# Create FastAPI app
app = FastAPI()

# Track connected devices and browsers
devices: Dict[str, WebSocket] = {}
browsers: Set[WebSocket] = set()
last_ping: Dict[str, float] = {}

@app.get("/")
def root():
    return JSONResponse({"status": "ok", "message": "WebSocket gateway online"})

@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket, deviceId: str = Query(...)):
    """WebSocket endpoint for Wemos/IoT devices"""
    await websocket.accept()
    devices[deviceId] = websocket
    last_ping[deviceId] = time.time()
    await notify_browsers({"type": "device_connected", "deviceId": deviceId})

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)

            # Heartbeat
            if data.get("type") == "ping":
                last_ping[deviceId] = time.time()
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            # Status update from device -> broadcast to browsers
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
    """WebSocket endpoint for browser clients"""
    await websocket.accept()
    browsers.add(websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)

            # Browser sends command to device
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
    """Receive command from Server A and forward to devices"""
    data = await request.json()
    command = data.get("command")
    source = data.get("source", "unknown")
    target = data.get("deviceId")  # optional

    print(f"[{time.strftime('%H:%M:%S')}] Received command from {source}: {command}")

    if target:
        # Send only to the specified device
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
        # Broadcast to all devices
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
    """Broadcast events to all connected browsers"""
    dead = []
    message = json.dumps(event)
    for ws in list(browsers):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        browsers.discard(ws)

# --- Local run block ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)

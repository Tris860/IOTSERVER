import os
import json
import time
import asyncio
import requests
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# Pools
unauthenticated: Dict[str, WebSocket] = {}   # deviceId -> socket
devices: Dict[str, WebSocket] = {}           # deviceName -> socket
last_ping: Dict[str, float] = {}             # deviceName -> last ping time

# Config
WEMOS_AUTH_URL = "https://tristechhub.org.rw/projects/ATS/backend/main.php?action=wemos_auth"
SERVER_A_CALLBACK_URL = os.environ.get("SERVER_A_CALLBACK_URL", "https://webserver-ft8c.onrender.com/notify/device-status")
PING_TIMEOUT = 180  # 3 minutes
AUTH_GRACE_PERIOD = 60  # 1 minute

@app.get("/")
def root():
    return JSONResponse({"status": "ok", "message": "Server B online"})

@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket, deviceId: str = Query(...)):
    await websocket.accept()
    unauthenticated[deviceId] = websocket
    print(f"Device {deviceId} connected, awaiting auth...")

    auth_deadline = time.time() + AUTH_GRACE_PERIOD
    deviceName = None

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)

            # --- Handle auth handshake ---
            if data.get("type") == "auth":
                username = data.get("username")
                password = data.get("password")
                print(f"Auth attempt from {deviceId}: {username}/{password}")

                try:
                    resp = requests.post(
                        WEMOS_AUTH_URL,
                        data={"action": "wemos_auth", "username": username, "password": password},
                        timeout=10,
                    )
                    print(f"Backend replied HTTP {resp.status_code}: {resp.text}")

                    if resp.status_code == 200:
                        backend = resp.json()
                        print(f"Parsed backend JSON: {backend}")

                        if backend.get("success"):
                            deviceName = backend["data"]["device_name"]
                            unauthenticated.pop(deviceId, None)
                            devices[deviceName] = websocket
                            last_ping[deviceName] = time.time()
                            print(f"Device {deviceName} authenticated")

                            # Send initial status
                            initialCommand = "HARD_ON" if backend["data"].get("hard_switch_enabled") else "HARD_OFF"
                            await websocket.send_text(json.dumps({"type": "command", "payload": {"action": initialCommand}}))
                            notify_server_a(deviceName, "CONNECTED")
                        else:
                            reason = backend.get("message", "Auth failed")
                            await websocket.send_text(json.dumps({"type": "auth_failed", "reason": reason}))
                            await websocket.close(code=4002)
                            notify_server_a(deviceId, "REJECTED", {"reason": reason})
                            return
                    else:
                        reason = f"Backend error HTTP {resp.status_code}"
                        await websocket.send_text(json.dumps({"type": "auth_failed", "reason": reason}))
                        await websocket.close(code=4001)
                        notify_server_a(deviceId, "REJECTED", {"reason": reason})
                        return
                except Exception as e:
                    reason = f"Internal error: {e}"
                    await websocket.send_text(json.dumps({"type": "auth_failed", "reason": reason}))
                    await websocket.close(code=1011)
                    notify_server_a(deviceId, "REJECTED", {"reason": reason})
                    return

            # --- Handle ping/pong ---
            elif data.get("type") == "ping" and deviceName in devices:
                last_ping[deviceName] = time.time()
                await websocket.send_text(json.dumps({"type": "pong"}))

            # --- Forward status if authenticated ---
            elif deviceName in devices and data.get("type") == "status":
                notify_server_a(deviceName, "STATUS", payload=data)

            # --- Grace period timeout ---
            if deviceId in unauthenticated and time.time() > auth_deadline:
                await websocket.send_text(json.dumps({"type": "auth_failed", "reason": "Timeout: no auth within 60s"}))
                await websocket.close(code=4003)
                unauthenticated.pop(deviceId, None)
                notify_server_a(deviceId, "REJECTED", {"reason": "Timeout"})
                return

    except WebSocketDisconnect:
        print(f"Device {deviceId} disconnected")
        unauthenticated.pop(deviceId, None)
        if deviceName and deviceName in devices:
            devices.pop(deviceName, None)
            last_ping.pop(deviceName, None)
            notify_server_a(deviceName, "DISCONNECTED")

@app.websocket("/ws/device-plain")
async def ws_device_plain(websocket: WebSocket, deviceId: str = Query(...)):
    await websocket.accept()
    unauthenticated[deviceId] = websocket
    print(f"[WS-PLAIN] Device {deviceId} connected (non-TLS), awaiting auth...")

    auth_deadline = time.time() + AUTH_GRACE_PERIOD
    deviceName = None

    try:
        while True:
            msg = await websocket.receive_text()
            print(f"[WS-PLAIN] Raw message from {deviceId}: {msg}")
            data = json.loads(msg)

            if data.get("type") == "auth":
                username = data.get("username")
                password = data.get("password")
                print(f"[AUTH-PLAIN] Attempt from {deviceId}: {username}/{password}")

                try:
                    resp = requests.post(
                        WEMOS_AUTH_URL,
                        data={"action": "wemos_auth", "username": username, "password": password},
                        timeout=10,
                    )
                    print(f"[AUTH-PLAIN] Backend replied HTTP {resp.status_code}: {resp.text}")

                    if resp.status_code == 200:
                        backend = resp.json()
                        print(f"[AUTH-PLAIN] Parsed backend JSON: {backend}")

                        if backend.get("success"):
                            deviceName = backend["data"]["device_name"]
                            unauthenticated.pop(deviceId, None)
                            devices[deviceName] = websocket
                            last_ping[deviceName] = time.time()
                            print(f"[AUTH-PLAIN] Device {deviceName} authenticated")

                            initialCommand = "HARD_ON" if backend["data"].get("hard_switch_enabled") else "HARD_OFF"
                            await websocket.send_text(json.dumps({"type": "command", "payload": {"action": initialCommand}}))
                            print(f"[AUTH-PLAIN] Sent initial command {initialCommand} to {deviceName}")
                            notify_server_a(deviceName, "CONNECTED")
                        else:
                            reason = backend.get("message", "Auth failed")
                            print(f"[AUTH-PLAIN] Authentication failed for {deviceId}: {reason}")
                            await websocket.send_text(json.dumps({"type": "auth_failed", "reason": reason}))
                            await websocket.close(code=4002)
                            notify_server_a(deviceId, "REJECTED", {"reason": reason})
                            return
                    else:
                        reason = f"Backend error HTTP {resp.status_code}"
                        print(f"[AUTH-PLAIN] Backend error for {deviceId}: {reason}")
                        await websocket.send_text(json.dumps({"type": "auth_failed", "reason": reason}))
                        await websocket.close(code=4001)
                        notify_server_a(deviceId, "REJECTED", {"reason": reason})
                        return
                except Exception as e:
                    reason = f"Internal error: {e}"
                    print(f"[AUTH-PLAIN] Exception during auth for {deviceId}: {reason}")
                    await websocket.send_text(json.dumps({"type": "auth_failed", "reason": reason}))
                    await websocket.close(code=1011)
                    notify_server_a(deviceId, "REJECTED", {"reason": reason})
                    return

            elif data.get("type") == "ping" and deviceName in devices:
                last_ping[deviceName] = time.time()
                print(f"[PING-PLAIN] Received ping from {deviceName}, sending pong")
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif deviceName in devices and data.get("type") == "status":
                print(f"[STATUS-PLAIN] Forwarding status from {deviceName}: {data}")
                notify_server_a(deviceName, "STATUS", payload=data)

            if deviceId in unauthenticated and time.time() > auth_deadline:
                print(f"[AUTH-PLAIN] Timeout: {deviceId} did not authenticate within {AUTH_GRACE_PERIOD}s")
                await websocket.send_text(json.dumps({"type": "auth_failed", "reason": "Timeout: no auth within 60s"}))
                await websocket.close(code=4003)
                unauthenticated.pop(deviceId, None)
                notify_server_a(deviceId, "REJECTED", {"reason": "Timeout"})
                return

    except WebSocketDisconnect:
        print(f"[WS-PLAIN] Device {deviceId} disconnected")
        unauthenticated.pop(deviceId, None)
        if deviceName and deviceName in devices:
            devices.pop(deviceName, None)
            last_ping.pop(deviceName, None)
            print(f"[WS-PLAIN] Device {deviceName} removed from authenticated pool")
            notify_server_a(deviceName, "DISCONNECTED")


@app.post("/command")
async def receive_command(request: Request):
    """Receive command from Server A and forward to devices"""
    data = await request.json()
    command = data.get("command")
    target =  target = data.get("deviceName") or data.get("deviceId")  # accept both

    print(f"[{time.strftime('%H:%M:%S')}] Received command: {command} for {target}")

    if not target:
        return {"status": "error", "message": "Missing deviceName"}

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

import asyncio
import base64
import datetime
import json
import os
import pyautogui

import websockets
from websockets import ConnectionClosed, ConnectionClosedError

CONFIG = {'host_id': ''}
IP = "ws://localhost:8765"


async def on_ready(websocket: websockets.ClientConnection):
    print("Loading configuration")
    global CONFIG
    if not os.path.exists("config.json"):
        with open("config.json", "w") as f:
            json.dump(CONFIG, f)
    else:
        with open("config.json", "r") as f:
            CONFIG = json.load(f)

    # Setting up client id if connecting for the first time
    if not CONFIG['host_id']:
        print("UUID not found. Requesting UUID")
        packet = dict()
        packet['type'] = "setup"
        await websocket.send(json.dumps(packet))
        async for message in websocket:
            CONFIG['host_id'] = message
            break
        with open("config.json", "w") as f:
            json.dump(CONFIG, f)


async def hello(websocket: websockets.ClientConnection):
    packet = dict()
    print(f"Authorizing with UUID: {CONFIG['host_id']}")
    packet['type'] = "hello"
    packet['host_id'] = CONFIG['host_id']
    await websocket.send(json.dumps(packet))
    async for message in websocket:
        print(message)
        break


async def heartbeat(websocket: websockets.ClientConnection):
    await asyncio.sleep(5)
    packet = dict()
    while True:
        packet['type'] = "heartbeat"
        await websocket.send(json.dumps(packet))
        await asyncio.sleep(5)


async def listen(websocket: websockets.ClientConnection):
    async def snip():
        print("Taking screenshot")
        pyautogui.screenshot("snip.png")
        packet = dict(data)
        with open("snip.png", mode="rb") as f:
            packet["data"] = base64.b64encode(f.read()).decode("ascii")
        await websocket.send(json.dumps(packet))

    func_map = {
        "snip": snip,
    }

    async for message in websocket:
        try:
            data = json.loads(message)
            await func_map[data['type']]()
        except json.JSONDecodeError:
            print("Invalid JSON data")
        except KeyError as e:
            print(f"Invalid key: {e}")


async def main():
    while True:
        try:
            print("Connecting to host server")
            async with websockets.connect(IP, max_size=100*1024*1024) as websocket:
                print("Connection established")
                await on_ready(websocket)
                await hello(websocket)

                heartbeat_task = asyncio.create_task(heartbeat(websocket))
                listen_task = asyncio.create_task(listen(websocket))

                await asyncio.gather(listen_task, heartbeat_task)
        except (ConnectionError,
                ConnectionClosed,
                ConnectionClosedError) as e:
            print(e)
            await asyncio.sleep(5)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

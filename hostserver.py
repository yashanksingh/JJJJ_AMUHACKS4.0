import asyncio
import base64
import datetime
import glob
import json
import os
from uuid import uuid4

import pymongo
import websockets
from dotenv import load_dotenv

load_dotenv()
db = pymongo.MongoClient(os.getenv("CONN_STRING"))["remote"]
connected = dict()
backend_server: websockets.ServerConnection


async def handler(websocket: websockets.ServerConnection):
    host = {
        "opentime": datetime.datetime.now(datetime.UTC),
        "auth": False
    }

    async def db_update():
        document = {
            "lastSeen": host["last"]
        }
        db["hosts"].find_one_and_update({"uuid": host["id"]}, {"$set": document})
        print(f"Updated last seen time for host {host['id']}")

    async def setup():
        res = db["hosts"].find({}, {"_id": 0, "uuid": 1})
        uuids = [_["uuid"] for _ in list(res)]
        res.close()
        host_id = str(uuid4())
        while host_id in uuids:
            host_id = str(uuid4())

        now = datetime.datetime.now(datetime.UTC)
        document = {
            "uuid": host_id,
            "name": "...",
            "lastSeen": now,
            "timeCreated": now
        }
        db["hosts"].insert_one(document)
        await websocket.send(host_id)

    async def hello():
        if data["host_id"] == "backend":
            global backend_server
            backend_server = websocket
            host["auth"] = True
            host["id"] = data["host_id"]
            print("Connection Established with backend")
            await websocket.send("Host Server is now connected with Backend")
            return

        res = db["hosts"].find({}, {"_id": 0, "uuid": 1})
        uuids = [_["uuid"] for _ in list(res)]
        res.close()
        if data["host_id"] not in uuids:
            print("Invalid UUID. Closing connection")
            await websocket.close()
            return

        host["auth"] = True
        host['id'] = data['host_id']
        host['last'] = datetime.datetime.now(datetime.UTC)
        print(f"Connection Established with host: {data['host_id']}")
        connected[host['id']] = websocket
        await db_update()
        await websocket.send("Hello Acknowledgment")

    async def heartbeat():
        now = datetime.datetime.now(datetime.UTC)
        beat = now - host["last"]
        print(f"Last heartbeat was {beat.total_seconds():.2f}s ago")
        host['last'] = now
        packet = dict()
        packet["type"] = "snip"
        await websocket.send(json.dumps(packet))

    async def echo():
        print(f"Echo: {data['message']}")
        await websocket.send(data['message'])

    async def msg():
        print(f"Message: {data['message']}")

    async def hosts():
        packet = dict()
        packet["hosts"] = list(connected.keys())
        await websocket.send(json.dumps(packet))

    async def cmd():
        try:
            packet = dict(data)
            packet["type"] = data["cmd"]
            del packet["cmd"]
            del packet["uuid"]
            await connected[data["uuid"]].send(json.dumps(packet))
        except KeyError:
            print("Host is not online or invalid uuid")

    async def snip():
        datafolder = f"data/{host['id']}/snip"
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}.png"
        filepath = os.path.join(datafolder, filename)

        os.makedirs(datafolder, exist_ok=True)
        files = glob.glob("*.png", root_dir=datafolder)
        older_files = sorted(files)[:-10]
        for i in older_files:
            os.remove(os.path.join(datafolder, i))

        with open(filepath, "wb") as f:
            f.write(base64.b64decode(data["data"]))

    async def upload():
        packet = dict(data)
        with open(data["filename"], mode="rb") as f:
            packet["data"] = base64.b64encode(f.read()).decode("ascii")
        await websocket.send(json.dumps(packet))

    async def download():
        datafolder = f"data/{host['id']}/files"
        filename = data["filename"]
        filepath = os.path.join(datafolder, filename)

        os.makedirs(datafolder, exist_ok=True)

        with open(filepath, "wb") as f:
            f.write(base64.b64decode(data["data"]))

    async def command():
        packet = dict()
        packet["out"], packet["err"] = data["out"], data["err"]
        await backend_server.send(json.dumps(packet))

    async def run():
        packet = dict()
        packet["ack"] = "run"
        await backend_server.send(json.dumps(packet))

    async def move():
        packet = dict()
        packet["ack"] = "move"
        await backend_server.send(json.dumps(packet))

    async def click():
        packet = dict()
        packet["ack"] = "click"
        await backend_server.send(json.dumps(packet))

    async def write():
        packet = dict()
        packet["ack"] = "write"
        await backend_server.send(json.dumps(packet))

    async def hotkey():
        packet = dict()
        packet["ack"] = "hotkey"
        await backend_server.send(json.dumps(packet))

    func_map = {
        'setup': setup,
        'hello': hello,
        'heartbeat': heartbeat,
        'echo': echo,
        'msg': msg,
        'hosts': hosts,
        'cmd': cmd,
        'snip': snip,
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
            if not host["auth"]:
                if (datetime.datetime.now(datetime.UTC) - host["opentime"]).total_seconds() < 30:
                    if data['type'] == "setup" or data['type'] == "hello":
                        await func_map[data['type']]()
                    else:
                        print("Invalid method")
                else:
                    print("Closing unauthorized connection")
                    await websocket.close()
            else:
                await func_map[data['type']]()
        except json.JSONDecodeError:
            print("Invalid JSON data")
        except KeyError as e:
            print(f"Invalid key: {e}")

    if host["auth"]:
        if host["id"] == "backend":
            print("Connection Lost with backend")
        else:
            del connected[host['id']]
            await db_update()
            print("Goodbye")


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765, max_size=100*1024*1024):
        print(f"Listening to connection requests")
        await asyncio.Future()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

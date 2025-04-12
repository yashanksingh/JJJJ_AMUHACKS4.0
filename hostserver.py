import asyncio
import datetime
import json
import os
from uuid import uuid4

import pymongo
import websockets
from dotenv import load_dotenv

load_dotenv()
db = pymongo.MongoClient(os.getenv("CONN_STRING"))["remote"]
connected = dict()


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

    func_map = {
        'setup': setup,
        'hello': hello,
        'heartbeat': heartbeat,
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
        del connected[host['id']]
        await db_update()
        print("Goodbye")


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print(f"Listening to connection requests")
        await asyncio.Future()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
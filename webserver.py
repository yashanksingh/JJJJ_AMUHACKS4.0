import asyncio
import glob

import timeago
import datetime
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional, Dict
from uuid import uuid4

import pymongo
import websockets
from PIL import Image
from dotenv import load_dotenv
from jose import jwt, JWTError

import uvicorn
from fastapi import FastAPI, Request, Depends, HTTPException, status, UploadFile, File
from fastapi.security import OAuth2PasswordRequestForm, OAuth2
from fastapi.responses import HTMLResponse, FileResponse, Response, RedirectResponse
from fastapi.security.utils import get_authorization_scheme_param
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.models import OAuthFlows as OAuthFlowsModel

load_dotenv()
IP = "ws://localhost:8765"
db = pymongo.MongoClient(os.getenv("CONN_STRING"))["remote"]
SECRET_KEY = os.getenv("SECRET_KEY")
websocket: websockets.ClientConnection
request_mapping = {}


def generate_request_id():
    request_id = str(uuid4())
    while request_id in request_mapping.keys():
        request_id = str(uuid4())
    return request_id


async def request_message(request_id):
    while not request_mapping.get(request_id, 0):
        await asyncio.sleep(0.1)
    response = request_mapping[request_id]
    del request_mapping[request_id]
    return response


async def listen():
    async for message in websocket:
        try:
            data = json.loads(message)
            print(data)
        except Exception as e:
            print(e)


async def auth():
    packet = dict()
    print(f"Connecting with Host Server")
    packet['type'] = "hello"
    packet['host_id'] = "backend"
    await websocket.send(json.dumps(packet))
    async for message in websocket:
        print(message)
        break


async def initiate_websocket():
    global websocket
    while True:
        try:
            async with websockets.connect(IP) as websocket:
                await auth()
                listen_task = asyncio.create_task(listen())
                await asyncio.gather(listen_task)
        except Exception as e:
            print(e)
            websocket = None
            print("Reconnecting in 10s")
            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global websocket
    websocket_thread = threading.Thread(target=asyncio.run, args=(initiate_websocket(),))
    websocket_thread.start()

    yield

    await websocket.close()
    websocket_thread.join()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")


#  ------------------------------ LOGIN ------------------------------


@app.get("/login", response_class=HTMLResponse)
async def show_login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


class LoginForm:
    def __init__(self, request: Request):
        self.request: Request = request
        self.username: Optional[str] = None
        self.password: Optional[str] = None

    async def load_data(self):
        form = await self.request.form()
        self.username = form.get("username")
        self.password = form.get("password")
        print(f"{self.username} {self.password}")

    async def is_valid(self):
        if self.username and self.password:
            return True
        return False


@app.post("/login")
async def login_user(request: Request):
    form = LoginForm(request)
    await form.load_data()
    if await form.is_valid():
        try:
            response = RedirectResponse(f"/hosts", status_code=status.HTTP_303_SEE_OTHER)
            # noinspection PyTypeChecker
            login_for_access_token(response=response, form_data=form)
            return response
        except HTTPException:
            return templates.TemplateResponse(request=request, name="login.html")
    return templates.TemplateResponse(request=request, name="login.html")


sessions = {}


class RequiresLogin(Exception):
    pass


@app.exception_handler(RequiresLogin)
async def requires_login(request: Request, _: Exception):
    return RedirectResponse(f"/login")


def get_user(username):
    user = db['users'].find_one({'username': username}, {'_id': 0})
    return user


def authenticate_user(username, password):
    user = get_user(username)
    if user is None or user["password"] != password:
        return False
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.now(datetime.UTC) + expires_delta
    else:
        expire = datetime.datetime.now(datetime.UTC) + timedelta(minutes=30)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm='HS256')
    return encoded_jwt


@app.post("/token")
def login_for_access_token(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    access_token_expires = timedelta(hours=24)
    access_token = create_access_token(
        data={"sub": user["username"]}, expires_delta=access_token_expires
    )
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=False)
    return {"access_token": access_token, "token_type": "bearer"}


class OAuth2PasswordBearerWithCookie(OAuth2):
    def __init__(
            self,
            tokenUrl: str,
            scheme_name: Optional[str] = None,
            scopes: Optional[Dict[str, str]] = None,
            auto_error: bool = True,
    ):
        if not scopes:
            scopes = {}
        flows = OAuthFlowsModel(password={"tokenUrl": tokenUrl, "scopes": scopes})
        super().__init__(flows=flows, scheme_name=scheme_name, auto_error=auto_error)

    async def __call__(self, request: Request) -> str | None | tuple[str, bool]:
        authorization: str = request.cookies.get("access_token")
        if authorization is None:
            authorization = request.headers.get("authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            if self.auto_error:
                raise RequiresLogin
            else:
                return None
        return param


oauth2_scheme = OAuth2PasswordBearerWithCookie(tokenUrl="/login/token")


def get_current_user_from_token(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms='HS256'
        )
        username: str = payload.get("sub")
        if username is None:
            raise RequiresLogin
    except JWTError:
        raise RequiresLogin
    user = get_user(username=username)
    if user is None:
        raise RequiresLogin
    return user


#  ------------------------------ ENDPOINTS ------------------------------


@app.get("/hosts", response_class=HTMLResponse)
async def show_all_hosts(request: Request, user: dict = Depends(get_current_user_from_token)):
    return templates.TemplateResponse(request=request, name="hosts.html")


@app.get("/host/{uuid}/control", response_class=HTMLResponse)
async def show_host(request: Request, uuid: str, user: dict = Depends(get_current_user_from_token)):
    return templates.TemplateResponse(request=request, name="host.html", context={"uuid": uuid})


@app.post("/host/{uuid}/submit")
async def host_form_submit(request: Request, uuid: str, file: Optional[UploadFile] = File(None),
                           user: dict = Depends(get_current_user_from_token)):
    form = await request.form()
    print(uuid)
    print(dict(form))
    print(user)

    packet = dict(form)
    if file:
        contents = await file.read()
        with open(file.filename, "wb") as f:
            f.write(contents)
            # f.write(file.file)
        del packet["file"]
        packet["filename"] = file.filename

    request_id = generate_request_id()
    packet["type"] = "cmd"
    packet["uuid"] = uuid
    packet["request_id"] = request_id
    await websocket.send(json.dumps(packet))

    async def download():
        return FileResponse(f"data/{uuid}/files/{data['filename']}", filename=data["filename"],
                            media_type='application/octet-stream')

    func_map = {
        "download": download,
    }

    data = await request_message(request_id)
    print(data)
    if data.get("type", 0):
        resp = await func_map[data['type']]()
        return resp

    return data


@app.get("/groups", response_class=HTMLResponse)
async def show_all_groups(request: Request, user: dict = Depends(get_current_user_from_token)):
    return templates.TemplateResponse(request=request, name="groups.html")


@app.get("/group/{uuid}/info", response_class=HTMLResponse)
async def show_group_info(request: Request, uuid: str, user: dict = Depends(get_current_user_from_token)):
    return templates.TemplateResponse(request=request, name="groupinfo.html", context={"uuid": uuid})


@app.get("/group/{uuid}/control", response_class=HTMLResponse)
async def show_group(request: Request, uuid: str, user: dict = Depends(get_current_user_from_token)):
    return templates.TemplateResponse(request=request, name="group.html", context={"uuid": uuid})


@app.post("/group/{uuid}/submit")
async def group_form_submit(request: Request, uuid: str, file: Optional[UploadFile] = File(None),
                      user: dict = Depends(get_current_user_from_token)):
    form = await request.form()
    uuids = db["groups"].find_one({"uuid": uuid}, {"_id": 0, "hosts": 1})["hosts"]
    if not uuids:
        return []

    packet = dict(form)
    if file:
        contents = await file.read()
        with open(file.filename, "wb") as f:
            f.write(contents)
        del packet["file"]
        packet["filename"] = file.filename

    requests = dict()
    for i in uuids:
        request_id = generate_request_id()
        packet["type"] = "cmd"
        packet["uuid"] = i
        packet["request_id"] = request_id
        requests[request_id] = i
        await websocket.send(json.dumps(packet))

    # async def download():
    #     return FileResponse(f"data/{uuid}/files/{data['filename']}", filename=data["filename"],
    #                         media_type='application/octet-stream')
    #
    # func_map = {
    #     "download": download,
    # }

    data = dict()
    for request_id in requests.keys():
        temp = await request_message(request_id)
        data[requests[request_id]] = temp
    print(data)

    # if data.get("type", 0):
    #     resp = await func_map[data['type']]()
    #     return resp

    return data


#  ------------------------------ APIS ------------------------------


@app.get("/api/all_hosts")
async def get_all_hosts(user: dict = Depends(get_current_user_from_token)):
    request_id = generate_request_id()
    packet = dict()
    packet["type"] = "hosts"
    packet["request_id"] = request_id
    await websocket.send(json.dumps(packet))
    connected = await request_message(request_id)

    res = db["hosts"].find({}, {"_id": 0})

    all_hosts = []
    for i in list(res):
        host = dict()
        host["uuid"] = i["uuid"]
        host["name"] = i.get("name", "...")
        host["status"] = True if i["uuid"] in connected["hosts"] else False
        host["lastSeen"] = timeago.format(i["lastSeen"], datetime.datetime.now(datetime.UTC))
        host["timeCreated"] = i["timeCreated"]
        all_hosts.append(host)
    res.close()

    return all_hosts


@app.get("/api/active_hosts")
async def get_active_hosts(user: dict = Depends(get_current_user_from_token)):
    request_id = generate_request_id()
    packet = dict()
    packet["type"] = "hosts"
    packet["request_id"] = request_id
    await websocket.send(json.dumps(packet))
    connected = await request_message(request_id)

    return connected


@app.get("/api/host_info/{uuid}")
async def get_host_info(uuid: str, user: dict = Depends(get_current_user_from_token)):
    request_id = generate_request_id()
    packet = dict()
    packet["type"] = "hosts"
    packet["request_id"] = request_id
    await websocket.send(json.dumps(packet))
    connected = await request_message(request_id)

    res = db["hosts"].find_one({"uuid": uuid}, {"_id": 0})

    host = dict()
    host["name"] = res.get("name", "...")
    host["uuid"] = res["uuid"]
    host["status"] = True if uuid in connected["hosts"] else False
    host["lastSeen"] = timeago.format(res["lastSeen"], datetime.datetime.now(datetime.UTC))
    host["timeCreated"] = res["timeCreated"]

    return host


@app.post("/api/update_hostname")
async def update_hostname(data: dict, user: dict = Depends(get_current_user_from_token)):
    db["hosts"].update_one({"uuid": data["uuid"]}, {"$set": {"name": data["hostname"]}})


@app.get("/api/latest_snip/{uuid}/{scale}")
async def get_latest_snip(uuid: str, scale: str | None = None, user: dict = Depends(get_current_user_from_token)):
    datafolder = f"data/{uuid}/snip"
    files = glob.glob("*.png", root_dir=datafolder)
    filename = max(files) if files else None
    if not filename:
        return FileResponse("static/blank.png", filename="blank.png", media_type="image/png")
    filepath = os.path.join(datafolder, filename)
    if scale == 'm':
        Image.open(filepath).save(f"{filepath[:-4]}.jpg")
        return FileResponse(os.path.join(datafolder, f"{filename[:-4]}.jpg"), filename=f"{filename[:-4]}.jpg",
                            media_type="image/jpg")
    elif scale == 's':
        img = Image.open(filepath)
        x, y = img.size
        img.resize((x // 2, y // 2)).save(f"{filepath[:-4]}.jpg")
        return FileResponse(os.path.join(datafolder, f"{filename[:-4]}.jpg"), filename=f"{filename[:-4]}.jpg",
                            media_type="image/jpg")
    return FileResponse(os.path.join(datafolder, filename), filename=filename, media_type="image/png")


@app.get("/api/snip/{uuid}/{filename}")
async def get_snip(uuid: str, filename, user: dict = Depends(get_current_user_from_token)):
    datafolder = f"data/{uuid}/snip"
    if not os.path.exists(os.path.join(datafolder, filename)):
        return FileResponse("static/blank.png", media_type="image/png")
    return FileResponse(os.path.join(datafolder, filename), filename=filename, media_type=f"image/{filename[-3:]}")


@app.get("/api/all_groups")
async def get_all_groups(user: dict = Depends(get_current_user_from_token)):
    request_id = generate_request_id()
    packet = dict()
    packet["type"] = "hosts"
    packet["request_id"] = request_id
    await websocket.send(json.dumps(packet))
    connected = await request_message(request_id)

    res = db["groups"].find({}, {"_id": 0})

    all_groups = []
    for i in list(res):
        group = dict()
        group["uuid"] = i["uuid"]
        group["user"] = i["user"]
        group["name"] = i["name"]
        group["hosts"] = i["hosts"]
        on = 0
        for uuid in i["hosts"]:
            if uuid in connected["hosts"]:
                on += 1
        group["status"] = {"on": on, "off": len(i["hosts"])-on}
        group["timeCreated"] = i["timeCreated"]
        all_groups.append(group)
    res.close()

    return all_groups


@app.post("/api/new_group")
async def create_new_group(user: dict = Depends(get_current_user_from_token)):
    res = db["groups"].find({}, {"_id": 0, "uuid": 1})
    uuids = [_["uuid"] for _ in list(res)]
    res.close()
    group_id = str(uuid4())
    while group_id in uuids:
        group_id = str(uuid4())

    now = datetime.utcnow()
    document = {
        "user": user["username"],
        "uuid": group_id,
        "name": "...",
        "hosts": [],
        "timeCreated": now
    }
    db["groups"].insert_one(document)


@app.post("/api/update_groupname")
async def update_groupname(data: dict, user: dict = Depends(get_current_user_from_token)):
    db["groups"].update_one({"uuid": data["uuid"]}, {"$set": {"name": data["groupname"]}})


@app.post("/api/edit_group")
async def edit_group(data: dict, user: dict = Depends(get_current_user_from_token)):
    db["groups"].update_one({"uuid": data["uuid"]}, {"$set": {"hosts": data["hosts"]}})


@app.post("/api/delete_group")
async def delete_group(data: dict, user: dict = Depends(get_current_user_from_token)):
    db["groups"].delete_one({"uuid": data["uuid"]})


@app.get("/api/group_info/{uuid}")
async def get_group_info(uuid: str, user: dict = Depends(get_current_user_from_token)):
    request_id = generate_request_id()
    packet = dict()
    packet["type"] = "hosts"
    packet["request_id"] = request_id
    await websocket.send(json.dumps(packet))
    connected = await request_message(request_id)

    res = db["groups"].find_one({"uuid": uuid}, {"_id": 0})

    group = dict()
    group["uuid"] = res["uuid"]
    group["user"] = res["user"]
    group["name"] = res["name"]
    group["hosts"] = res["hosts"]
    on = 0
    for uuid in res["hosts"]:
        if uuid in connected["hosts"]:
            on += 1
    group["status"] = {"on": on, "off": len(res["hosts"])-on}
    group["timeCreated"] = res["timeCreated"]

    return group


@app.get("/api/group_host_info/{uuid}")
async def get_group_host_info(uuid: str, user: dict = Depends(get_current_user_from_token)):
    request_id = generate_request_id()
    packet = dict()
    packet["type"] = "hosts"
    packet["request_id"] = request_id
    await websocket.send(json.dumps(packet))
    connected = await request_message(request_id)

    uuids = db["groups"].find_one({"uuid": uuid}, {"_id": 0, "hosts": 1})["hosts"]
    if not uuids:
        return []
    res = db["hosts"].find({"$or": [{"uuid": _} for _ in uuids]}, {"_id": 0})

    all_hosts = []
    for i in list(res):
        host = dict()
        host["uuid"] = i["uuid"]
        host["name"] = i.get("name", "...")
        host["status"] = True if i["uuid"] in connected["hosts"] else False
        host["lastSeen"] = timeago.format(i["lastSeen"], datetime.utcnow())
        host["timeCreated"] = i["timeCreated"]
        all_hosts.append(host)
    res.close()

    return all_hosts


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)

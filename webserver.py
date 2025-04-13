import asyncio
import datetime
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional, Dict

import pymongo
import websockets
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
    return 69

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)

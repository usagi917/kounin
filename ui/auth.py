"""ログイン。ログイン ID とパスワードで人事担当だけが入る（SPEC §3）。

役割はセッションから読み、画面の入力から読まない。全更新に CSRF 対策をし、Host を検査する（INV20）。
"""

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "kounin_session"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MAX_FAILURES, LOCK_SECONDS = 5, 300


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


DUMMY_HASH = hash_password("kounin", b"0" * 16)


def verify_password(password: str, stored: str | None) -> bool:
    """ID がない時も同じ計算をして、応答の速さで ID の有無が分からないようにする。"""
    salt = base64.b64decode((stored or DUMMY_HASH).split("$")[1])
    matched = hmac.compare_digest(hash_password(password, salt), stored or DUMMY_HASH)
    return matched and stored is not None


class Attempts:
    """失敗が続いた ID を一定時間締め出す。"""

    def __init__(self) -> None:
        self._items: dict[str, tuple[int, float]] = {}

    def locked(self, login_id: str, now: float | None = None) -> bool:
        count, until = self._items.get(login_id, (0, 0.0))
        return count >= MAX_FAILURES and (now or time.monotonic()) < until

    def fail(self, login_id: str, now: float | None = None) -> None:
        count, until = self._items.get(login_id, (0, 0.0))
        moment = now or time.monotonic()
        if count >= MAX_FAILURES and moment >= until:
            count = 0
        self._items[login_id] = (count + 1, moment + LOCK_SECONDS)

    def clear(self, login_id: str) -> None:
        self._items.pop(login_id, None)


@dataclass(frozen=True)
class Session:
    user_id: str
    csrf: str


class Sessions:
    def __init__(self) -> None:
        self._items: dict[str, Session] = {}

    def create(self, user_id: str) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        session = Session(user_id, secrets.token_urlsafe(32))
        self._items[token] = session
        return token, session

    def get(self, token: str | None) -> Session | None:
        return self._items.get(token) if token else None

    def drop(self, token: str | None) -> None:
        if token:
            self._items.pop(token, None)


def csrf_ok(session: Session, sent: str | None) -> bool:
    return bool(sent) and hmac.compare_digest(session.csrf, sent)


def host_name(value: str) -> str:
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class HostCheck:
    """Host が loopback でない要求を拒む。DNS の張り替えで外のページから呼ばれるのを防ぐ。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode("latin-1")
            if host_name(host) not in ALLOWED_HOSTS:
                await JSONResponse({"error": "この宛先からは開けません"}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)

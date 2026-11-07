"""画面の API。ログインは人事担当だけで、ほかの人は一時URLで答える。検査はここで行い、業務は agent/core へ渡す。"""

import json
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from agent.core import audit, changes, decisions, inbox, ingest, links, month as months, teach
from agent.core import pack as packs
from agent.core import workspace
from engine.hashing import stable_hash
from ui.auth import COOKIE, Attempts, HostCheck, Sessions, csrf_ok, verify_password

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 20 * 1024 * 1024
HR = "人事担当"
OK_CATEGORIES = ("通常ルール適用", "例外適用")
SCREENS = ["today", "inbox", "ask", "change", "pay", "history"]
BUSINESS_ERRORS = (months.MonthError, changes.ChangeError, decisions.PayError, packs.PackError, ValueError,
                   FileNotFoundError)


def error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(home: Path | None = None) -> Starlette:
    home = home or workspace.resolve_home()
    if not workspace.is_ready(home):
        if os.environ.get(workspace.HOME_ENV):
            raise workspace.WorkspaceError(f"{workspace.HOME_ENV} の場所に環境がありません。架空環境を勝手に作りません")
        workspace.init_sample(home)
    workspace.refresh_sample_users(home)
    sessions = Sessions()
    attempts = Attempts()

    def user_of(request: Request) -> dict | None:
        session = sessions.get(request.cookies.get(COOKIE))
        if session is None:
            return None
        return next((user for user in workspace.users(home) if user["id"] == session.user_id), None)

    def current_month() -> str:
        return workspace.config(home)["current_month"]

    def guarded(write: bool = False) -> Callable:
        """ログイン、人事担当であること、CSRF の検査を1か所にまとめる。拒否は履歴に残す。"""
        def wrap(handler: Callable) -> Callable:
            async def endpoint(request: Request) -> Response:
                user = user_of(request)
                if user is None:
                    return error("ログインしてください", 401)
                if write and not csrf_ok(sessions.get(request.cookies.get(COOKIE)),
                                         request.headers.get("x-csrf-token")):
                    audit.record(home, user, "更新の拒否", request.url.path, "CSRF の検査に失敗")
                    return error("画面を開き直してください", 403)
                if user["role"] != HR:
                    audit.record(home, user, "権限の拒否", request.url.path, "権限がない")
                    return error("この操作の権限がありません", 403)
                try:
                    return await handler(request, user)
                except BUSINESS_ERRORS as problem:
                    return error(str(problem))
            return endpoint
        return wrap

    async def body(request: Request) -> dict:
        try:
            value = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            raise ValueError("送られた内容が読めません") from None
        if not isinstance(value, dict):
            raise ValueError("送られた内容が読めません")
        return value

    async def index(_: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def session_info(request: Request) -> Response:
        user = user_of(request)
        session = sessions.get(request.cookies.get(COOKIE))
        settings = workspace.config(home)
        return JSONResponse({
            "environment": settings["environment"],
            "user": user and {"id": user["id"], "name": user["name"], "role": user["role"]},
            "csrf": session.csrf if session else None,
            "screens": SCREENS if user else [],
        })

    async def login(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return error("この宛先からは開けません", 403)
        data = await body(request)
        login_id, password = str(data.get("login_id", "")).strip(), str(data.get("password", ""))
        if attempts.locked(login_id):
            audit.record(home, None, "ログインの拒否", "画面", "失敗が続いたため締め出し中")
            return error("しばらくしてから、もう一度試してください", 429)
        user = next((item for item in workspace.users(home)
                     if item.get("login_id") == login_id and item["role"] == HR), None)
        if not verify_password(password, user.get("password_hash") if user else None):
            attempts.fail(login_id)
            audit.record(home, None, "ログインの拒否", "画面", "ID かパスワードが違う")
            return error("ログイン ID かパスワードが違います")
        attempts.clear(login_id)
        sessions.drop(request.cookies.get(COOKIE))
        token, _ = sessions.create(user["id"])
        audit.record(home, user, "ログイン", "画面", "成功")
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    @guarded(write=True)
    async def logout(request: Request, user: dict) -> Response:
        sessions.drop(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    def waiting(month: str) -> list[dict]:
        """確認が必要な理由ごとの人数。誰の手が要るかも添える。"""
        counts: dict[tuple[str, str], int] = {}
        people = {item["id"]: item for item in workspace.users(home)}
        for item in inbox.open_items(home, month):
            to = item["to"]
            if to["role"] == HR:
                who = "人事担当（あなた）"
            else:
                person = people.get(to.get("user_id")) or next(
                    (row for row in people.values() if row["role"] == to["role"]), None)
                who = f"{person['name']}さん（{to['role']}）" if person else to["role"]
            counts[(item["reason"], who)] = counts.get((item["reason"], who), 0) + 1
        return [{"reason": reason, "who": who, "count": count} for (reason, who), count in sorted(counts.items())]

    def run_summary(run: dict | None) -> dict | None:
        return run and {key: value for key, value in run.items() if key != "people"}

    def groups_of(run: dict | None) -> list[dict]:
        """職場ごとの人数と、確認が必要な人数。個人は返さない。"""
        rows: dict[str, dict] = {}
        for person in run["people"] if run else []:
            name = person["group"] or "職場が不明"
            row = rows.setdefault(name, {"name": name, "total": 0, "waiting": 0})
            row["total"] += 1
            row["waiting"] += person["category"] not in OK_CATEGORIES
        return sorted(rows.values(), key=lambda row: -row["total"])

    def headcount(month: str) -> int:
        return len(ingest.load_rows(ingest.input_dir(home, month), ingest.STAFF))

    def stage_of(month: str, run: dict | None, stale: bool) -> int:
        """今の工程。0 計算、1 確認、2 3案、3 決定、4 CSV、5 終わり。"""
        if run is None or stale:
            return 0
        if run["stopped"]:
            return 1
        data = decisions.current_plans(home, month)
        record = decisions.decision(home, month)
        if data is None:
            return 2
        if record is None or decisions.is_expired(home, month, record):
            return 3
        return 5 if decisions.exported(home, month, record) else 4

    @guarded()
    async def pulse(request: Request, user: dict) -> Response:
        """画面が今の状態を知るための短い問い合わせ。答えが届くと画面が動く。"""
        month = current_month()
        run, stale = months.latest(home, month), months.is_stale(home, month)
        stage = stage_of(month, run, stale)
        sign = [run and run["id"], stale, stage, links.path(home).stat().st_size if links.path(home).exists() else 0]
        return JSONResponse({"stage": stage, "signature": stable_hash(sign)[:16]})

    @guarded()
    async def today(request: Request, user: dict) -> Response:
        month = current_month()
        run = months.latest(home, month)
        return JSONResponse({"month": month, "run": run_summary(run), "stale": months.is_stale(home, month),
                             "waiting": waiting(month), "closed": inbox.closed_count(home, month),
                             "groups": groups_of(run), "headcount": headcount(month)})

    @guarded(write=True)
    async def run_now(request: Request, user: dict) -> Response:
        run, created = months.run(home, current_month(), user)
        return JSONResponse({"created": created, "run": run_summary(run)})

    @guarded()
    async def inbox_view(request: Request, user: dict) -> Response:
        month = current_month()
        run = months.latest(home, month)
        items = inbox.mine(home, month, user)
        pack = packs.for_date(home, months.base_date(month))
        return JSONResponse({"month": month, "bundles": inbox.bundles(items, run) if run else [],
                             "ranks": pack.ranks, "environment": workspace.config(home)["environment"],
                             **links.board(home, month, run)})

    def own_item(month: str, item_id: str, user: dict, kind: str) -> dict:
        item = inbox.find(home, month, item_id)
        if item is None:
            raise ValueError("その連絡はもう閉じています")
        if not inbox.addressed_to(item, user) or item["kind"] != kind:
            audit.record(home, user, "権限の拒否", "連絡", "宛先の人ではない")
            raise PermissionError
        return item

    @guarded(write=True)
    async def answer_rank(request: Request, user: dict) -> Response:
        data, month = await body(request), current_month()
        try:
            item = own_item(month, data.get("item_id", ""), user, inbox.RANK)
        except PermissionError:
            return error("あなた宛ての連絡ではありません", 403)
        pack = packs.for_date(home, months.base_date(month))
        if data.get("rank") not in pack.ranks:
            return error("ランクを選んでください")
        ingest.answer_rank(home, month, item["emp_id"], data["rank"], user)
        run, _ = months.run(home, month, None)
        return JSONResponse({"stopped": run["stopped"]})

    def check_upload(month: str, kind: str, raw: bytes) -> dict:
        pack = packs.for_date(home, months.base_date(month))
        directory = ingest.input_dir(home, month)
        known = {row["職員番号"] for row in ingest.load_rows(directory, ingest.STAFF)}
        result = ingest.check(kind, raw, known_ids=known, groups=set(pack.groups),
                              jobs=set(pack.coefficients.job_coefficients), ranks=set(pack.ranks))
        if result.errors:
            return {"ok": False, "errors": result.errors}
        return {"ok": True, "kind": kind, "token": ingest.stage(home, month, kind, raw),
                "diff": ingest.differences(kind, ingest.load_rows(directory, kind), result.rows)}

    @guarded(write=True)
    async def file_check(request: Request, user: dict) -> Response:
        raw = await request.body()
        if len(raw) > MAX_UPLOAD:
            return error("ファイルが大きすぎます")
        kind, month = request.query_params.get("kind", ""), current_month()
        result = check_upload(month, kind, raw)
        audit.record(home, user, "データの検査", f"{month} {kind}",
                     "通った" if result["ok"] else f"拒否：{len(result['errors'])}件")
        return JSONResponse(result)

    @guarded(write=True)
    async def file_confirm(request: Request, user: dict) -> Response:
        data, month = await body(request), current_month()
        version, grew = ingest.confirm(home, month, data.get("kind", ""), data.get("token", ""), user)
        run, _ = months.run(home, month, None)
        return JSONResponse({"version": version, "grew": grew, "stopped": run["stopped"]})

    @guarded(write=True)
    async def ask(request: Request, user: dict) -> Response:
        text = str((await body(request)).get("text", "")).strip()
        if not text:
            return error("聞きたいことを書いてください")
        reply = teach.answer(home, current_month(), text, True)
        audit.record(home, user, "聞く", "教える", "まだ教わっていない" if reply.get("stopped") else "答えた")
        return JSONResponse(reply)

    def next_effective(month: str) -> str:
        first = months.base_date(month).replace(day=1)
        taken = {ref.effective_from for ref in packs.versions(home)}
        while first in taken:
            first += timedelta(days=1)
        return first.isoformat()

    @guarded()
    async def change_view(request: Request, user: dict) -> Response:
        month = current_month()
        return JSONResponse({"month": month, "versions": changes.version_list(home), "changes": changes.listing(home),
                             "effective_from": next_effective(month)})

    @guarded(write=True)
    async def change_propose(request: Request, user: dict) -> Response:
        data = await body(request)
        text = str(data.get("text", "")).strip()
        if not text:
            return error("変更を書いてください")
        return JSONResponse(changes.propose(home, current_month(), text, str(data.get("effective_from", "")), user))

    @guarded(write=True)
    async def change_approve(request: Request, user: dict) -> Response:
        record = changes.approve(home, str((await body(request)).get("id", "")), user)
        run, _ = months.run(home, current_month(), None)
        return JSONResponse({"change": record, "run": run_summary(run)})

    @guarded(write=True)
    async def change_reject(request: Request, user: dict) -> Response:
        return JSONResponse(changes.reject(home, str((await body(request)).get("id", "")), user))

    @guarded()
    async def pay_view(request: Request, user: dict) -> Response:
        month = current_month()
        run = months.latest(home, month)
        record = decisions.decision(home, month)
        data = decisions.current_plans(home, month)
        stale = months.is_stale(home, month)
        return JSONResponse({
            "month": month, "run": run_summary(run), "stale": stale,
            "plans": decisions.summaries(data) if data and not stale else None,
            "chair": next((row for row in links.board(home, month, run)["contacts"] if row["role"] == "理事長"), None),
            "decision": record and {"plan_id": record["plan_id"], "plan_name": record["plan_name"], "by": record["by"],
                                    "at": record["at"], "expired": decisions.is_expired(home, month, record)}})

    @guarded(write=True)
    async def pay_plans(request: Request, user: dict) -> Response:
        decisions.build_plans(home, current_month(), user)
        return JSONResponse({"ok": True})

    @guarded(write=True)
    async def pay_export(request: Request, user: dict) -> Response:
        name, text = decisions.export(home, current_month(), user)
        return Response(text.encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @guarded()
    async def history(request: Request, user: dict) -> Response:
        return JSONResponse({"entries": audit.entries(home)[:200]})

    @guarded(write=True)
    async def link_issue(request: Request, user: dict) -> Response:
        data = await body(request)
        token, record = links.issue(home, current_month(), str(data.get("to", "")), data.get("questions", []), user)
        return JSONResponse({"token": token, "expires_at": record["expires_at"]})

    @guarded(write=True)
    async def link_revoke(request: Request, user: dict) -> Response:
        links.revoke(home, current_month(), str((await body(request)).get("id", "")), user)
        return JSONResponse({"ok": True})

    async def question_page(_: Request) -> Response:
        """トークンは URL の # の後ろにあり、サーバーへは届かない。ログと Referer に残さないためである。"""
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store",
                                                            "Referrer-Policy": "no-referrer"})

    def holder(handler: Callable) -> Callable:
        """一時URLの入口。セッションを見ず、トークンの見出しだけで動く。独自の見出しが CSRF の防ぎにもなる。"""
        async def endpoint(request: Request) -> Response:
            token = request.headers.get("x-link-token", "")
            try:
                response = await handler(request, token, current_month())
            except links.LinkDone as problem:
                response = JSONResponse({"error": str(problem), "done": True}, status_code=410)
            except links.LinkClosed as problem:
                if request.method == "POST":
                    audit.record(home, None, "一時URLの拒否", "一時URL", str(problem))
                response = error(str(problem), 410)
            except BUSINESS_ERRORS as problem:
                response = error(str(problem))
            response.headers["Cache-Control"] = "no-store"
            return response
        return endpoint

    @holder
    async def link_view(request: Request, token: str, month: str) -> Response:
        return JSONResponse(links.view(home, token, month))

    @holder
    async def link_rank(request: Request, token: str, month: str) -> Response:
        data = await body(request)
        run = links.answer_rank(home, token, month, data.get("item_id"), data.get("rank"))
        return JSONResponse({"stopped": run["stopped"]})

    @holder
    async def link_judge(request: Request, token: str, month: str) -> Response:
        data = await body(request)
        run = links.answer_judge(home, token, month, data.get("item_id"), data.get("group"))
        return JSONResponse({"stopped": run["stopped"]})

    @holder
    async def link_answer(request: Request, token: str, month: str) -> Response:
        data = await body(request)
        links.answer_text(home, token, month, data.get("index"), data.get("text"))
        return JSONResponse({"ok": True})

    @holder
    async def link_decide(request: Request, token: str, month: str) -> Response:
        record = links.decide(home, token, month, (await body(request)).get("plan_id"))
        return JSONResponse({"plan_id": record["plan_id"]})

    routes = [
        Route("/", index),
        Route("/q", question_page),
        Route("/api/session", session_info),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/pulse", pulse),
        Route("/api/today", today),
        Route("/api/run", run_now, methods=["POST"]),
        Route("/api/inbox", inbox_view),
        Route("/api/inbox/rank", answer_rank, methods=["POST"]),
        Route("/api/links", link_issue, methods=["POST"]),
        Route("/api/links/revoke", link_revoke, methods=["POST"]),
        Route("/api/link", link_view),
        Route("/api/link/rank", link_rank, methods=["POST"]),
        Route("/api/link/judge", link_judge, methods=["POST"]),
        Route("/api/link/answer", link_answer, methods=["POST"]),
        Route("/api/link/decide", link_decide, methods=["POST"]),
        Route("/api/files/check", file_check, methods=["POST"]),
        Route("/api/files/confirm", file_confirm, methods=["POST"]),
        Route("/api/ask", ask, methods=["POST"]),
        Route("/api/changes", change_view),
        Route("/api/changes/propose", change_propose, methods=["POST"]),
        Route("/api/changes/approve", change_approve, methods=["POST"]),
        Route("/api/changes/reject", change_reject, methods=["POST"]),
        Route("/api/pay", pay_view),
        Route("/api/pay/plans", pay_plans, methods=["POST"]),
        Route("/api/pay/export", pay_export, methods=["POST"]),
        Route("/api/history", history),
        Mount("/static", StaticFiles(directory=STATIC)),
    ]
    return Starlette(routes=routes, middleware=[Middleware(HostCheck)])


"""Local HTTP API and listening UI; every tool route comes from the registry."""

from __future__ import annotations

import asyncio
import copy
import json
import secrets
import socket
import sys
import webbrowser
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import ValidationError

from ..errors import MuestraError, ToolResult, issues_from_validation_error
from ..project.core import open_project
from ..schemas.api import WsEvent
from .core import REGISTRY, Runtime, failure, input_schema, project_logger, resolve_project
from .events import EventBus, ProjectWatcher
from .files import MIME, audio_path, waveform
from .jobs import JobQueueFull, Jobs
from .summary import project_summary


def _http_schema(spec):
    schema = input_schema(spec)
    schema.get("properties", {}).pop("project", None)
    schema["required"] = [key for key in schema.get("required", []) if key != "project"]
    definitions = schema.pop("$defs", {})

    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value and value["$ref"].startswith("#/$defs/"):
                value = {
                    **definitions[value["$ref"].split("/")[-1]],
                    **{k: v for k, v in value.items() if k != "$ref"},
                }
            return {k: expand(v) for k, v in value.items()}
        return [expand(v) for v in value] if isinstance(value, list) else value

    return expand(schema)


#: tools a client on another device may call when the server listens on the
#: LAN (WP13): reading + the two things the listening UI writes.
REMOTE_WRITE_TOOLS = frozenset({"feedback.append", "chops.choose"})
TOKEN_COOKIE = "muestra_token"
LOOPBACK_NAMES = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback(host: str | None) -> bool:
    return bool(host) and (host in LOOPBACK_NAMES or host.startswith("127."))


def remote_allowed(spec) -> bool:
    return spec.readonly or spec.name in REMOTE_WRITE_TOOLS


def _host_name(header: str | None) -> str:
    header = (header or "").strip().lower()
    if header.startswith("["):
        return header[1 : header.find("]")] if "]" in header else header
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def _supplied_token(request) -> str:
    return (
        request.query_params.get("token")
        or request.headers.get("x-muestra-token")
        or request.cookies.get(TOKEN_COOKIE)
        or ""
    )


_TOKEN_PAGE = """<!doctype html><html><meta charset="utf-8"><title>Muestra</title>
<style>body{font:18px system-ui;max-width:36rem;margin:12vh auto;padding:2rem;
background:#14131b;color:#efedf5}code{color:#c7f9d6}</style>
<h1>Muestra</h1><p>Para abrir el estudio desde otro dispositivo usá la dirección
completa que muestra la terminal de la compu al arrancar <code>muestra serve</code>:
termina en <code>?token=…</code>.</p></html>"""


def _recover_renders(root: Path) -> None:
    """Roll back versions left without audio by a killed render (WP13)."""
    from ..render import staging

    try:
        staging.recover(open_project(root))
    except Exception:  # noqa: BLE001 - never block the UI on cleanup
        project_logger(root).exception("Could not clean interrupted renders")


def create_app(
    project: str | Path,
    *,
    ui_dist: Path | None = None,
    watch_interval=0.1,
    debounce=0.2,
    job_capacity=32,
    token: str | None = None,
    allowed_hosts: frozenset[str] | None = None,
) -> FastAPI:
    """``token``: LAN mode (WP13) — clients that are not on this computer must
    present it (``?token=`` once, then a cookie, or ``X-Muestra-Token``) and may
    only call read-only tools plus feedback.append / chops.choose; loopback
    clients are unaffected. ``allowed_hosts``: Host header names accepted
    (a loopback bind passes the loopback names: blocks DNS rebinding)."""
    root = open_project(project).root
    runtime = Runtime(root, fixed=True)
    bus = EventBus()
    jobs = Jobs(runtime, bus, capacity=job_capacity)
    watcher = ProjectWatcher(root, bus, interval=watch_interval, debounce=debounce)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(_recover_renders, root)
        await asyncio.to_thread(watcher.initialize)
        watching = asyncio.create_task(watcher.run())
        worker = asyncio.create_task(jobs.worker())
        try:
            yield
        finally:
            watching.cancel()
            with suppress(asyncio.CancelledError):
                await watching
            await jobs.close()
            await worker

    app = FastAPI(
        title="Muestra",
        version="0.0.0",
        lifespan=lifespan,
        description="One local music project. See /docs for generated tools and jobs.",
    )
    app.state.runtime, app.state.jobs, app.state.bus = runtime, jobs, bus
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "HEAD", "POST", "DELETE"],
        allow_headers=["Content-Type", "Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "X-Muestra-Project"],
    )

    by_route = {spec.mcp_name: spec for spec in REGISTRY.values()}

    @app.middleware("http")
    async def guard(request, call_next):
        if allowed_hosts is not None and _host_name(request.headers.get("host")) not in (
            allowed_hosts
        ):
            return JSONResponse({"detail": "Host is not allowed."}, status_code=403)
        client = request.client.host if request.client else None
        if token is None or is_loopback(client):
            return await call_next(request)
        supplied = _supplied_token(request)
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            if request.method == "GET" and not request.url.path.startswith(
                ("/tools", "/files", "/waveform", "/project", "/jobs")
            ):
                return HTMLResponse(_TOKEN_PAGE, status_code=401)
            return JSONResponse(
                failure(
                    "http",
                    "unauthorized",
                    "This Muestra server needs its access token.",
                    "Open the URL with ?token=... that muestra serve printed.",
                ).model_dump(mode="json"),
                status_code=401,
            )
        path = request.url.path
        spec = by_route.get(path[len("/tools/") :]) if path.startswith("/tools/") else None
        if (spec is not None and not remote_allowed(spec)) or request.method == "DELETE":
            name = spec.name if spec else "http"
            return JSONResponse(
                failure(
                    name,
                    "local_only",
                    f"{name} is only available on the computer running Muestra.",
                    "Ask the agent on that computer; from another device you can listen, "
                    "comment, rate and choose chops.",
                ).model_dump(mode="json"),
                status_code=403,
            )
        response = await call_next(request)
        if request.query_params.get("token") and request.cookies.get(TOKEN_COOKIE) != token:
            response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict")
        return response

    @app.middleware("http")
    async def project_header(request, call_next):
        # Browser requests from arbitrary web pages must not mutate an unauthenticated server.
        origin = request.headers.get("origin")
        if origin and origin not in ("http://localhost:5173", "http://127.0.0.1:5173"):
            if origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "Origin is not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Muestra-Project"] = str(root)
        return response

    @app.exception_handler(Exception)
    async def crash(request, exc):
        logger = project_logger(root)
        logger.error("HTTP request failed", exc_info=(type(exc), exc, exc.__traceback__))
        for handler in logger.handlers:
            handler.close()
        return JSONResponse(
            failure(
                "http",
                "internal_error",
                "The request could not complete.",
                "Inspect cache/logs/muestra.log.",
            ).model_dump(mode="json"),
            status_code=500,
        )

    def tool_endpoint(spec):
        async def endpoint(request: Request):
            try:
                if request.method == "GET":
                    params = dict(request.query_params)
                    params.pop("token", None)  # LAN access token, not a tool param
                    properties = spec.params.model_json_schema().get("properties", {})
                    for key, value in list(params.items()):
                        prop = properties.get(key, {})
                        variants = prop.get("anyOf", [prop])
                        if not any(item.get("type") == "string" for item in variants):
                            try:
                                params[key] = json.loads(value)
                            except ValueError:
                                pass
                else:
                    raw = await request.body()
                    params = json.loads(raw) if raw else {}
                    if not isinstance(params, dict):
                        raise ValueError("Tool parameters must be a JSON object.")
                prepared = runtime.prepare(spec, params)
                # Validate before queuing so invalid requests don't consume worker slots.
                spec.params.model_validate(prepared)
            except ValidationError as exc:
                result = ToolResult(
                    tool=spec.name,
                    ok=False,
                    errors=issues_from_validation_error(exc),
                    hint="Fix the listed parameters and retry.",
                )
                return JSONResponse(result.model_dump(mode="json"), status_code=422)
            except MuestraError as exc:
                result = ToolResult(tool=spec.name, ok=False, errors=exc.issues, hint=exc.hint)
                return JSONResponse(result.model_dump(mode="json"), status_code=409)
            except (ValueError, UnicodeError) as exc:
                result = failure(
                    spec.name, "parse_error", str(exc), "Send a JSON object of parameters."
                )
                return JSONResponse(result.model_dump(mode="json"), status_code=422)
            if spec.long_running:
                try:
                    job = jobs.submit(spec, prepared)
                except JobQueueFull:
                    return JSONResponse(
                        failure(
                            spec.name,
                            "busy",
                            "The job queue is full.",
                            "Wait for a queued job to finish and retry.",
                        ).model_dump(mode="json"),
                        status_code=503,
                    )
                return JSONResponse({"job_id": job.id}, status_code=202)
            result = await asyncio.to_thread(runtime.call, spec, prepared)
            status = (
                200
                if result.ok
                else (
                    501
                    if any(i.code == "not_implemented" for i in result.errors)
                    else 500
                    if any(i.code == "internal_error" for i in result.errors)
                    else 422
                )
            )
            return JSONResponse(result.model_dump(mode="json"), status_code=status)

        return endpoint

    for spec in REGISTRY.values():
        schema = _http_schema(spec)
        endpoint = tool_endpoint(spec)
        responses = {}
        if spec.long_running:
            responses[202] = {
                "description": "Queued job",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["job_id"],
                            "properties": {"job_id": {"type": "string"}},
                        }
                    }
                },
            }
        app.add_api_route(
            f"/tools/{spec.mcp_name}",
            endpoint,
            methods=["POST"],
            name=spec.name,
            summary=spec.description,
            responses=responses,
            response_model=ToolResult,
            openapi_extra={
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": schema}},
                }
            },
        )
        if spec.readonly:
            app.add_api_route(
                f"/tools/{spec.mcp_name}",
                endpoint,
                methods=["GET"],
                name=f"{spec.name} (read)",
                summary=spec.description,
                openapi_extra={
                    "parameters": [
                        {
                            "name": name,
                            "in": "query",
                            "required": name in schema.get("required", []),
                            "schema": prop,
                        }
                        for name, prop in schema.get("properties", {}).items()
                    ]
                },
                responses=copy.deepcopy(responses),
                response_model=ToolResult,
            )

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        if job_id not in jobs.items:
            raise HTTPException(404, "Job not found (job history is process-local and bounded).")
        return jobs.items[job_id]

    @app.delete("/jobs/{job_id}")
    async def cancel_job(job_id: str):
        if job_id not in jobs.items:
            raise HTTPException(404, "Job not found.")
        job = jobs.items[job_id]
        if not jobs.cancel(job):
            raise HTTPException(
                409, "Only queued jobs can be cancelled; running audio work finishes safely."
            )
        return job

    @app.get("/project")
    @app.get("/project/summary", include_in_schema=False)
    def summary():
        return project_summary(runtime)

    @app.get("/files/{path:path}")
    @app.head("/files/{path:path}", include_in_schema=False)
    def files(path: str):
        target = audio_path(root, path)
        # ETag/Last-Modified come with FileResponse; no-cache makes the browser
        # revalidate, so re-rendering a version never plays stale audio.
        return FileResponse(
            target,
            media_type=MIME[target.suffix.lower()],
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/waveform")
    @app.get("/waveform/peaks", include_in_schema=False)
    def peaks(path: str, points: int = Query(default=1024, ge=1, le=8192)):
        target = audio_path(root, path)
        return {"path": path, **waveform(target, points)}

    @app.websocket("/events")
    async def events(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        client = websocket.client.host if websocket.client else None
        if allowed_hosts is not None and _host_name(host) not in allowed_hosts:
            await websocket.close(code=1008)
            return
        if (
            token is not None
            and not is_loopback(client)
            and not secrets.compare_digest(_supplied_token(websocket).encode(), token.encode())
        ):
            await websocket.close(code=1008)
            return
        if origin and origin not in (
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            f"http://{host}",
            f"https://{host}",
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = bus.subscribe()
        # A page opened mid-separation must see the running job right away.
        for job in list(jobs.items.values()):
            if job.status in ("queued", "running"):
                await websocket.send_json(
                    WsEvent(
                        type="job.updated", ts=datetime.now(UTC), data=job.model_dump(mode="json")
                    ).model_dump(mode="json")
                )

        async def send():
            while True:
                await websocket.send_json(await queue.get())

        async def receive():
            while True:
                await websocket.receive_text()

        sender, receiver = asyncio.create_task(send()), asyncio.create_task(receive())
        try:
            done, pending = await asyncio.wait(
                [sender, receiver], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                with suppress(WebSocketDisconnect, RuntimeError):
                    task.result()
        finally:
            for task in (sender, receiver):
                task.cancel()
                with suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await task
            bus.subscribers.discard(queue)

    dist = ui_dist or Path(__file__).resolve().parents[3] / "ui/dist"

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        # Keep unmatched API paths distinct from SPA navigation.
        if path.split("/", 1)[0] in ("tools", "jobs", "files", "events", "waveform", "project"):
            raise HTTPException(404, "Route not found.")
        if (dist / "index.html").is_file():
            target = (dist / path).resolve()
            if not target.is_relative_to(dist.resolve()):
                raise HTTPException(403, "Invalid asset path.")
            if target.is_file():
                return FileResponse(target)
            if Path(path).suffix:
                raise HTTPException(404, "Asset not found.")
            return FileResponse(dist / "index.html")
        if path:
            raise HTTPException(404, "UI has not been built; visit / for setup instructions.")
        return HTMLResponse("""<!doctype html><html><meta charset="utf-8"><title>Muestra</title>
        <style>body{font:18px system-ui;max-width:42rem;margin:12vh auto;padding:2rem;
        background:#14131b;color:#efedf5}a{color:#b9a3ff}code{color:#c7f9d6}</style>
        <h1>Muestra</h1><p>Your project API is ready.</p>
        <p>The listening UI has not been built yet. From the repository's <code>ui</code>
        folder run <code>npm install</code>, then <code>npm run build</code>, and refresh.</p>
        <p><a href="/docs">Explore the API</a> ·
        <a href="/project">Project summary</a></p></html>""")

    return app


def bind_socket(host: str, port: int) -> socket.socket:
    """Hold the selected port until uvicorn takes ownership, avoiding a pick/bind race."""
    if not 0 <= port <= 65535:
        raise ValueError("Port must be in 0..65535.")
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    selected = socket.socket(family)
    try:
        selected.bind((host, port))
    except OSError:
        selected.close()
        selected = socket.socket(family)
        selected.bind((host, 0))
    selected.listen(128)
    selected.setblocking(False)
    return selected


def _lan_ip() -> str | None:
    """This computer's address on the LAN (routing-table lookup: a UDP
    connect sends no packet)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere real
        address = probe.getsockname()[0]
        return None if address.startswith("127.") or address == "0.0.0.0" else address
    except OSError:
        return None
    finally:
        probe.close()


def serve(project=None, *, host="127.0.0.1", port=8765, open_browser=False):
    import uvicorn

    root = resolve_project(project)
    if root is None:
        raise ValueError("No project selected. Pass --project or run inside a Muestra project.")
    local = is_loopback(host)
    token = None if local else secrets.token_urlsafe(16)
    app = create_app(root, token=token, allowed_hosts=LOOPBACK_NAMES if local else None)
    with bind_socket(host, port) as selected:
        actual = selected.getsockname()[1]
        display_host = "127.0.0.1" if host == "0.0.0.0" else f"[{host}]" if ":" in host else host
        url = f"http://{display_host}:{actual}"
        if token:
            lan = _lan_ip() if host in ("0.0.0.0", "::") else host
            print(
                "Listening on the network. Other devices need the token in the URL below and "
                "can only listen, comment, rate and choose chops; this computer needs nothing.",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"On your phone (same Wi-Fi): http://{lan or '<this-computer-LAN-IP>'}:{actual}"
                f"/?token={token}",
                flush=True,
            )
        print(f"Muestra · {root}\n{url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=actual))
        server.run(sockets=[selected])

"""V2Pulse web service. Run: python -m uvicorn pulse.app:app --host 0.0.0.0 --port 8875"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, seed
from .audit import access_log
from .auth.routes import router as auth_router
from .modules import admin, analytics, assist, bi, control_tower, dumps, home, masters, mis, portals, reports, simulation, workspace
from .web import middleware, problem

STATIC = os.path.join(os.path.dirname(__file__), "static")
# Look-and-feel overrides (e.g. Microsoft-yellow folders). Outside static/ so a web-app rebuild keeps them.
THEME_CSS = os.path.join(os.path.dirname(__file__), "theme", "overrides.css")
THEME_LINK = '<link rel="stylesheet" href="/theme/overrides.css">'
THEME_JS = os.path.join(os.path.dirname(__file__), "theme", "overrides.js")
THEME_SCRIPT = '<script src="/theme/overrides.js" defer></script>'
ANALYTICS = os.path.join(os.path.dirname(__file__), "analytics")
# "Ask me a question" (Claude) panel, on the web app and on the server pages; answers from modules/assist.py.
ASSIST_JS = os.path.join(os.path.dirname(__file__), "theme", "assist.js")
ASSIST_CSS = os.path.join(os.path.dirname(__file__), "theme", "assist.css")
ASSIST_TAGS = '<link rel="stylesheet" href="/theme/assist.css"><script src="/theme/assist.js" defer></script>'


def with_assist(path: str) -> HTMLResponse:
    """A server page (Analytics, MIS, Simulation) with the Ask-me-a-question panel added."""
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    if ASSIST_TAGS not in html and os.path.isfile(ASSIST_JS):
        html = html.replace("</head>", ASSIST_TAGS + "\n</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    with db.tx() as cur:
        seed.run(cur)
    access_log.start()
    yield
    access_log.stop()
    db.close_pool()


app = FastAPI(title="V2Pulse", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")
app.middleware("http")(middleware)

for r in (auth_router, home.router, portals.router, reports.router, masters.router, bi.router, workspace.router, control_tower.router, admin.router,
          analytics.router, dumps.router, simulation.router, assist.router):
    app.include_router(r)
mis.include(app)   # MIS engine routes (copied from DFS) behind V2Pulse sign-in + mis.* permissions


@app.exception_handler(HTTPException)
async def http_problem(request: Request, exc: HTTPException):
    titles = {400: "Bad request", 401: "Not signed in", 403: "Not allowed", 404: "Not found", 409: "Conflict",
              422: "Check the input", 429: "Too many requests", 502: "Upstream failed", 503: "Not ready"}
    return problem(exc.status_code, titles.get(exc.status_code, "Error"), str(exc.detail), request)


@app.exception_handler(RequestValidationError)
async def validation_problem(request: Request, exc: RequestValidationError):
    errs = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return problem(422, "Check the input", "; ".join("%s: %s" % (e["field"], e["message"]) for e in errs[:5]), request,
                   errors=errs)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return problem(500, "Something went wrong", "The error was logged. Quote the trace id to the V2Pulse team.", request)


@app.get("/healthz")
def healthz():
    try:
        with db.tx() as cur:
            cur.execute("select 1")
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    return JSONResponse({"ok": ok, "db": ok, "version": __version__, "access_log_written": access_log.written},
                        status_code=200 if ok else 503)


if os.path.isdir(os.path.join(STATIC, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC, "assets")), name="assets")


@app.get("/theme/overrides.css", include_in_schema=False)
def theme_css():
    if not os.path.isfile(THEME_CSS):
        return HTMLResponse("", media_type="text/css")
    return FileResponse(THEME_CSS, media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/theme/overrides.js", include_in_schema=False)
def theme_js():
    return FileResponse(THEME_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/theme/assist.js", include_in_schema=False)
def assist_js():
    return FileResponse(ASSIST_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/theme/assist.css", include_in_schema=False)
def assist_css():
    return FileResponse(ASSIST_CSS, media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/analytics", include_in_schema=False)
def analytics_home():
    """Analytics landing page (the sidebar tab): dashboard menu and headline figures; data from /api/v1/analytics."""
    return with_assist(os.path.join(ANALYTICS, "index.html"))


@app.get("/analytics/app.js", include_in_schema=False)
def analytics_js():
    return FileResponse(os.path.join(ANALYTICS, "app.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


SIMULATION = os.path.join(os.path.dirname(__file__), "simulation")


@app.get("/simulation", include_in_schema=False)
def simulation_home():
    """Simulation page (sidebar tab under Libraries): what-if tools; data from /api/v1/simulation."""
    return with_assist(os.path.join(SIMULATION, "index.html"))


@app.get("/simulation/app.js", include_in_schema=False)
def simulation_js():
    return FileResponse(os.path.join(SIMULATION, "app.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# MIS workspace (all 19 DFS MIS tabs + the Dump Scheduler): ONE page (pulse/mis_ui/index.html) for every /mis/... address;
# its own router (mis_ui/core.js) shows the tab. Plain HTML / JS served outside the web app, like Analytics.
MIS_UI = os.path.join(os.path.dirname(__file__), "mis_ui")
UI_TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}


def _mis_file(name):
    f = os.path.realpath(os.path.join(MIS_UI, name))
    if not f.startswith(os.path.realpath(MIS_UI) + os.sep) or not os.path.isfile(f):
        return problem(404, "Not found", "No such file.")
    return FileResponse(f, media_type=UI_TYPES.get(os.path.splitext(f)[1], "application/octet-stream"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/mis", include_in_schema=False)
@app.get("/mis/{rest:path}", include_in_schema=False)
def mis_pages(rest: str = ""):
    """Every /mis/... address is the MIS workspace page; static files under /mis/static/."""
    if rest.startswith("static/"):
        return _mis_file(rest[len("static/"):])
    return with_assist(os.path.join(MIS_UI, "index.html"))


@app.get("/dumps", include_in_schema=False)
def dumps_page():
    """The Dump Scheduler lives in the MIS workspace now."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/mis/dumps", status_code=302)


MANAGE = os.path.join(os.path.dirname(__file__), "manage")


@app.get("/manage/trees", include_in_schema=False)
def manage_trees():
    """Library trees page: rename the Reports / Masters trees (served outside the web app, like the theme)."""
    return FileResponse(os.path.join(MANAGE, "trees.html"), media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/manage/trees.js", include_in_schema=False)
def manage_trees_js():
    return FileResponse(os.path.join(MANAGE, "trees.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Every non-API route returns the single-page app; it routes on the client."""
    if path.startswith(("api/", "auth/")):
        return problem(404, "Not found", "No such endpoint.")
    f = os.path.join(STATIC, path)
    if path and os.path.isfile(f) and os.path.realpath(f).startswith(os.path.realpath(STATIC)):
        return FileResponse(f)
    index = os.path.join(STATIC, "index.html")
    if os.path.isfile(index):
        with open(index, encoding="utf-8") as fh:
            html = fh.read()
        if THEME_LINK not in html:   # after the app's own stylesheet, so the overrides win
            html = html.replace("</head>", "  " + THEME_LINK + "\n  </head>", 1)
        if THEME_SCRIPT not in html and os.path.isfile(THEME_JS):   # sidebar labels, links to server pages
            html = html.replace("</head>", "  " + THEME_SCRIPT + "\n  </head>", 1)
        if ASSIST_TAGS not in html and os.path.isfile(ASSIST_JS):   # Ask-me-a-question panel
            html = html.replace("</head>", "  " + ASSIST_TAGS + "\n  </head>", 1)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>V2Pulse API is running</h1><p>The web app has not been built yet (npm run build in web/).</p>")

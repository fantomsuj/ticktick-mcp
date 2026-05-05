"""Vercel serverless entrypoint for the TickTick triage dashboard.

Vercel runs this as a serverless function, so the lifecycle is different
from local `ticktick-dashboard`:

- No persistent disk: the SQLite event log defaults to in-memory and
  resets on every cold start. Set TICKTICK_DASHBOARD_DB=/tmp/foo.db to
  keep it within a single warm function instance.
- Env vars come from Vercel project settings, not a `.env` file.
- If TICKTICK_ACCESS_TOKEN isn't set, we boot in mock mode so the
  preview deploy still demos.
- Set DASHBOARD_PASSWORD to gate the dashboard with HTTP Basic Auth —
  otherwise anyone with the URL can mutate your tasks.
"""

import os
import sys
from pathlib import Path

# Ensure the project root is importable when Vercel runs from /var/task/api
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ticktick_mcp.dashboard import create_app  # noqa: E402
from ticktick_mcp.event_log import EventLog  # noqa: E402

TZ = os.environ.setdefault("TICKTICK_TIMEZONE", "America/Los_Angeles")


def _build_client():
    if os.getenv("TICKTICK_DASHBOARD_MODE", "").lower() == "mock":
        from ticktick_mcp.mock_data import MockClient
        return MockClient(), True
    if not os.getenv("TICKTICK_ACCESS_TOKEN"):
        from ticktick_mcp.mock_data import MockClient
        return MockClient(), True
    from ticktick_mcp.src.ticktick_client import TickTickClient
    return TickTickClient(), False


def _build_event_log() -> EventLog:
    db_path = os.getenv("TICKTICK_DASHBOARD_DB") or ":memory:"
    return EventLog(db_path, TZ)


def _install_basic_auth(app) -> None:
    """Gate the dashboard with HTTP Basic Auth if DASHBOARD_PASSWORD is set."""
    password = os.getenv("DASHBOARD_PASSWORD")
    if not password:
        return
    user = os.getenv("DASHBOARD_USER", "admin")

    from functools import wraps
    from flask import request, Response

    def _check(auth) -> bool:
        return bool(auth) and auth.username == user and auth.password == password

    @app.before_request
    def _require_auth():  # noqa: ANN202
        # Always allow the health endpoint — useful for uptime pings.
        if request.path == "/health":
            return None
        auth = request.authorization
        if not _check(auth):
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="TickTick dashboard"'},
            )
        return None


client, is_mock = _build_client()
event_log = _build_event_log()
if is_mock:
    from ticktick_mcp.mock_data import seed_mock_events
    seed_mock_events(event_log)

app = create_app(client, event_log=event_log)
_install_basic_auth(app)

"""Local web dashboard for triaging TickTick tasks.

Run with `ticktick-dashboard` (uses the same .env tokens as the MCP server)
or `ticktick-dashboard --mock` to demo the UI without API credentials.

The dashboard reuses `TickTickClient` for all writes, so OAuth refresh and
.env handling are unchanged. A 30-second read-through cache keeps UI swaps
fast without hammering the API.
"""

import argparse
import json
import logging
import os
import sys
import threading
import webbrowser
from datetime import datetime, date as date_cls, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, url_for

from .event_log import DEFAULT_DB_PATH, EventLog
from .prompts import panel_prompt

logger = logging.getLogger(__name__)

# Project IDs from CLAUDE.md
INBOX_PROJECT_ID = "699a5943b1bed115b35b1e10"
SOMEDAY_PROJECT_ID = "69b6e5088f085ebce14b22d6"
WAITING_HOME_PROJECT_ID = "699c8a338f088b3b190a1a5d"  # BR Commercial & BD

PRIORITY_NAMES = {0: "None", 1: "Low", 3: "Medium", 5: "High"}
PRIORITY_ORDER = [5, 3, 1, 0]

HIGHLIGHT_PREFIX = "⭐ "
WAITING_PREFIX = "WAITING:"
CLAUDE_PREFIX = "🚩 "

DEFAULT_TZ = os.getenv("TICKTICK_TIMEZONE", "America/Los_Angeles")


# ---------------------------------------------------------------------------
# Cache layer over TickTickClient
# ---------------------------------------------------------------------------

class TickTickStore:
    """Read-through cache for projects + active tasks.

    The TickTick public API has no bulk-tasks endpoint, so we walk every
    non-closed project once and cache for 30s. Mutations call
    `invalidate()` so the next read pulls fresh data.
    """

    def __init__(self, client, ttl_seconds: int = 30):
        self.client = client
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = threading.Lock()
        self._projects: Optional[List[Dict]] = None
        self._tasks_by_project: Dict[str, List[Dict]] = {}
        self._fetched_at: Optional[datetime] = None

    def invalidate(self) -> None:
        with self._lock:
            self._projects = None
            self._tasks_by_project = {}
            self._fetched_at = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _ensure_fresh(self) -> None:
        with self._lock:
            if self._fetched_at and (self._now() - self._fetched_at) < self._ttl:
                return
            projects = self.client.get_projects()
            if isinstance(projects, dict) and projects.get("error"):
                raise RuntimeError(f"TickTick get_projects failed: {projects['error']}")
            self._projects = projects or []
            self._tasks_by_project = {}
            for project in self._projects:
                pid = project.get("id")
                if not pid or project.get("closed"):
                    continue
                data = self.client.get_project_with_data(pid)
                if isinstance(data, dict) and data.get("error"):
                    logger.warning("project %s: %s", pid, data["error"])
                    self._tasks_by_project[pid] = []
                    continue
                self._tasks_by_project[pid] = data.get("tasks") or []
            self._fetched_at = self._now()

    def projects(self, include_closed: bool = False) -> List[Dict]:
        self._ensure_fresh()
        return [p for p in (self._projects or []) if include_closed or not p.get("closed")]

    def project(self, project_id: str) -> Optional[Dict]:
        for p in self.projects(include_closed=True):
            if p.get("id") == project_id:
                return p
        return None

    def all_active_tasks(self) -> List[Dict]:
        self._ensure_fresh()
        out: List[Dict] = []
        for tasks in self._tasks_by_project.values():
            for t in tasks:
                if t.get("status") != 2:
                    out.append(t)
        return out

    def project_tasks(self, project_id: str) -> List[Dict]:
        self._ensure_fresh()
        return [t for t in self._tasks_by_project.get(project_id, []) if t.get("status") != 2]


# ---------------------------------------------------------------------------
# Date / task helpers
# ---------------------------------------------------------------------------

def user_tz() -> ZoneInfo:
    return ZoneInfo(DEFAULT_TZ)


def parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if "." in s:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


def task_due(task: Dict) -> Optional[datetime]:
    return parse_iso_dt(task.get("dueDate"))


def today_local() -> date_cls:
    return datetime.now(user_tz()).date()


def is_due_today(task: Dict) -> bool:
    due = task_due(task)
    return bool(due and due.astimezone(user_tz()).date() == today_local())


def is_due_tomorrow(task: Dict) -> bool:
    due = task_due(task)
    return bool(due and due.astimezone(user_tz()).date() == today_local() + timedelta(days=1))


def is_overdue(task: Dict) -> bool:
    due = task_due(task)
    if not due:
        return False
    local_due = due.astimezone(user_tz())
    if local_due.date() < today_local():
        return True
    # Same-day past-due tasks count as "overdue" only after the day rolls.
    return False


def days_overdue(task: Dict) -> int:
    due = task_due(task)
    if not due:
        return 0
    delta = today_local() - due.astimezone(user_tz()).date()
    return max(delta.days, 0)


def fmt_due(task: Dict) -> str:
    due = task_due(task)
    if not due:
        return ""
    local = due.astimezone(user_tz())
    today = today_local()
    delta_days = (local.date() - today).days
    time_part = "" if task.get("isAllDay") else local.strftime(" %-I:%M %p")
    if delta_days == 0:
        return f"today{time_part}"
    if delta_days == 1:
        return f"tomorrow{time_part}"
    if delta_days == -1:
        return f"yesterday{time_part}"
    if delta_days < 0:
        return f"{abs(delta_days)}d overdue · {local.strftime('%b %-d')}"
    if delta_days < 7:
        return local.strftime("%a") + time_part
    return local.strftime("%b %-d") + time_part


def to_api_iso(dt: datetime) -> str:
    """Format a tz-aware datetime in the shape TickTick expects on writes."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=user_tz())
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def workday_morning(d: date_cls) -> datetime:
    """Default time-of-day when scheduling 'today' / 'tomorrow' / '+Nd'.

    AGENT.md sets a 30-minute default duration; we use 9:00am local as the
    canonical start time so it behaves like a planned slot rather than EOD.
    """
    return datetime(d.year, d.month, d.day, 9, 0, 0, tzinfo=user_tz())


def is_waiting(task: Dict) -> bool:
    return (task.get("title") or "").startswith(WAITING_PREFIX)


def is_highlight(task: Dict) -> bool:
    return task.get("priority") == 5 and task.get("status") != 2


# ---------------------------------------------------------------------------
# View-model construction
# ---------------------------------------------------------------------------

def project_lookup(store: TickTickStore) -> Dict[str, Dict]:
    return {p["id"]: p for p in store.projects(include_closed=True) if p.get("id")}


def task_view(task: Dict, projects: Dict[str, Dict]) -> Dict:
    pid = task.get("projectId") or ""
    proj = projects.get(pid) or {}
    return {
        "id": task.get("id"),
        "project_id": pid,
        "project_name": proj.get("name") or "Unknown",
        "project_color": proj.get("color") or "#888",
        "title": task.get("title") or "(untitled)",
        "content": task.get("content") or "",
        "priority": task.get("priority", 0),
        "priority_name": PRIORITY_NAMES.get(task.get("priority", 0), "?"),
        "due_date": task.get("dueDate"),
        "due_label": fmt_due(task),
        "days_overdue": days_overdue(task),
        "is_overdue": is_overdue(task),
        "is_today": is_due_today(task),
        "is_highlight": is_highlight(task),
        "is_waiting": is_waiting(task),
        "is_inbox": pid == INBOX_PROJECT_ID,
    }


def sort_for_triage(tasks: List[Dict]) -> List[Dict]:
    # Most overdue first; break ties by priority desc, then earliest due
    return sorted(
        tasks,
        key=lambda t: (
            -days_overdue(t),
            -(t.get("priority") or 0),
            (task_due(t) or datetime.max.replace(tzinfo=timezone.utc)),
        ),
    )


def sort_for_today(tasks: List[Dict]) -> List[Dict]:
    # Highlight first, then by priority desc, then by due time
    def key(t: Dict) -> Tuple:
        due = task_due(t)
        return (
            0 if is_highlight(t) else 1,
            -(t.get("priority") or 0),
            due or datetime.max.replace(tzinfo=timezone.utc),
        )
    return sorted(tasks, key=key)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

class ActionError(Exception):
    pass


def reschedule_to_date(client, store: TickTickStore, task: Dict, target: date_cls,
                      keep_time: bool = False) -> Dict:
    pid = task["projectId"]
    tid = task["id"]
    if keep_time and task.get("dueDate"):
        existing = parse_iso_dt(task["dueDate"])
        if existing:
            local = existing.astimezone(user_tz())
            new_dt = local.replace(year=target.year, month=target.month, day=target.day)
        else:
            new_dt = workday_morning(target)
    else:
        new_dt = workday_morning(target)
    iso = to_api_iso(new_dt)
    res = client.update_task(task_id=tid, project_id=pid, start_date=iso, due_date=iso)
    store.invalidate()
    return res


def move_to_someday(client, store: TickTickStore, task: Dict) -> Dict:
    res = client.update_task(
        task_id=task["id"],
        project_id=SOMEDAY_PROJECT_ID,
    )
    store.invalidate()
    return res


def set_priority(client, store: TickTickStore, task: Dict, new_priority: int) -> Dict:
    if new_priority not in PRIORITY_NAMES:
        raise ActionError(f"invalid priority {new_priority}")
    title = task.get("title") or ""
    if new_priority == 5 and not title.startswith(HIGHLIGHT_PREFIX):
        title = HIGHLIGHT_PREFIX + title
    elif new_priority != 5 and title.startswith(HIGHLIGHT_PREFIX):
        title = title[len(HIGHLIGHT_PREFIX):]
    res = client.update_task(
        task_id=task["id"],
        project_id=task["projectId"],
        priority=new_priority,
        title=title if title != task.get("title") else None,
    )
    store.invalidate()
    return res


def find_existing_highlight(store: TickTickStore, exclude_id: str) -> Optional[Dict]:
    for t in store.all_active_tasks():
        if t.get("id") == exclude_id:
            continue
        if is_highlight(t):
            return t
    return None


def promote_to_highlight(client, store: TickTickStore, task: Dict, force: bool = False) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Set Highlight, demoting any existing one. Returns (existing, new) tuple.

    If force=False and another Highlight exists, returns (existing, None) so
    the UI can ask for confirmation before demoting.
    """
    existing = find_existing_highlight(store, exclude_id=task["id"])
    if existing and not force:
        return existing, None
    if existing:
        set_priority(client, store, existing, 3)
    set_priority(client, store, task, 5)
    return existing, task


def assign_inbox_task(client, store: TickTickStore, task: Dict, project_id: str,
                     priority: int, due_date_str: Optional[str]) -> Dict:
    iso_due = None
    if due_date_str:
        try:
            d = date_cls.fromisoformat(due_date_str)
        except ValueError:
            raise ActionError(f"bad date: {due_date_str}")
        iso_due = to_api_iso(workday_morning(d))
    res = client.update_task(
        task_id=task["id"],
        project_id=project_id,
        priority=priority,
        start_date=iso_due,
        due_date=iso_due,
    )
    store.invalidate()
    return res


# ---------------------------------------------------------------------------
# Counts / panels
# ---------------------------------------------------------------------------

def compute_counts(store: TickTickStore) -> Dict[str, int]:
    tasks = store.all_active_tasks()
    today_tasks = [t for t in tasks if is_due_today(t)]
    return {
        "triage": sum(1 for t in tasks if is_overdue(t)),
        "today": len(today_tasks),
        "tomorrow": sum(1 for t in tasks if is_due_tomorrow(t)),
        "inbox": sum(1 for t in tasks if t.get("projectId") == INBOX_PROJECT_ID),
        "waiting": sum(1 for t in tasks if is_waiting(t)),
        "someday": sum(1 for t in tasks if t.get("projectId") == SOMEDAY_PROJECT_ID),
        "highlight_conflicts": sum(1 for t in tasks if is_highlight(t)),
    }


def panel_data(name: str, store: TickTickStore, event_log: Optional[EventLog] = None) -> Dict:
    projects = project_lookup(store)
    tasks = store.all_active_tasks()
    prompt = panel_prompt(name, today_local())
    if name == "triage":
        items = [t for t in tasks if is_overdue(t)]
        items = sort_for_triage(items)
        return {
            "title": "Overdue triage",
            "subtitle": "Oldest first. Decide and clear.",
            "prompt": prompt,
            "tasks": [task_view(t, projects) for t in items],
            "show_quick_reschedule": True,
        }
    if name == "today":
        items = [t for t in tasks if is_due_today(t)]
        items = sort_for_today(items)
        highlight = next((t for t in items if is_highlight(t)), None)
        # The Highlight gets its own section above; pull it out of the rest
        # so it doesn't render twice.
        non_highlight = [t for t in items if t is not highlight]
        big_three = non_highlight[:3]
        tail = non_highlight[3:]
        return {
            "title": "Today",
            "subtitle": "Highlight + Three Big Things, then the tail.",
            "prompt": prompt,
            "highlight": task_view(highlight, projects) if highlight else None,
            "big_three": [task_view(t, projects) for t in big_three],
            "tail": [task_view(t, projects) for t in tail],
        }
    if name == "tomorrow":
        items = [t for t in tasks if is_due_tomorrow(t)]
        return {
            "title": "Tomorrow",
            "subtitle": "What you've already lined up. Push more from Today during End of Day.",
            "prompt": prompt,
            "tasks": [task_view(t, projects) for t in sort_for_today(items)],
        }
    if name == "inbox":
        items = [t for t in tasks if t.get("projectId") == INBOX_PROJECT_ID]
        items.sort(key=lambda t: t.get("title") or "")
        active_projects = [
            {"id": p["id"], "name": p["name"]}
            for p in store.projects()
            if p.get("id") not in (INBOX_PROJECT_ID,)
        ]
        active_projects.sort(key=lambda p: p["name"].lower())
        return {
            "title": "Inbox triage",
            "subtitle": "Assign each capture a project, priority, and (optional) due date.",
            "prompt": prompt,
            "tasks": [task_view(t, projects) for t in items],
            "projects": active_projects,
        }
    if name == "waiting":
        items = [t for t in tasks if is_waiting(t)]
        items = sort_for_triage(items)
        return {
            "title": "Waiting for",
            "subtitle": "Tasks blocked on someone else.",
            "prompt": prompt,
            "tasks": [task_view(t, projects) for t in items],
        }
    if name == "someday":
        items = [t for t in tasks if t.get("projectId") == SOMEDAY_PROJECT_ID]
        items.sort(key=lambda t: t.get("title") or "")
        return {
            "title": "Someday/Maybe",
            "subtitle": "Scan during weekly review. Promote anything timely.",
            "prompt": prompt,
            "tasks": [task_view(t, projects) for t in items],
        }
    if name == "eod":
        today_items = [t for t in tasks if is_due_today(t)]
        tomorrow_items = [t for t in tasks if is_due_tomorrow(t)]
        unfinished_today = sort_for_today(today_items)
        completed_today: List[Dict] = []
        activity_stats: Dict[str, int] = {}
        if event_log is not None:
            today_str = event_log.today_local_date()
            completed_today = event_log.completed_on(today_str)
            activity_stats = event_log.stats_on(today_str)
        return {
            "title": "End of day",
            "subtitle": "Close out today, set up tomorrow.",
            "prompt": prompt,
            "unfinished": [task_view(t, projects) for t in unfinished_today],
            "tomorrow": [task_view(t, projects) for t in sort_for_today(tomorrow_items)],
            "highlight_for_tomorrow": next(
                (task_view(t, projects) for t in tomorrow_items if is_highlight(t)),
                None,
            ),
            "completed_today": completed_today,
            "activity_stats": activity_stats,
        }
    raise ActionError(f"unknown panel: {name}")


PANELS = ["triage", "today", "tomorrow", "inbox", "waiting", "someday", "eod"]


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app(client, event_log: Optional[EventLog] = None) -> Flask:
    package_root = Path(__file__).parent
    app = Flask(
        __name__,
        template_folder=str(package_root / "templates"),
        static_folder=str(package_root / "static"),
    )
    store = TickTickStore(client)
    if event_log is None:
        event_log = EventLog(":memory:", DEFAULT_TZ)
    app.config["STORE"] = store
    app.config["CLIENT"] = client
    app.config["EVENT_LOG"] = event_log

    def get_task_or_404(project_id: str, task_id: str) -> Dict:
        for t in store.all_active_tasks():
            if t.get("id") == task_id and t.get("projectId") == project_id:
                return t
        # tolerate stale cache: refresh once and retry
        store.invalidate()
        for t in store.all_active_tasks():
            if t.get("id") == task_id and t.get("projectId") == project_id:
                return t
        abort(404, description=f"task {task_id} not found in project {project_id}")

    def htmx_response(html: str = "", trigger: Optional[str] = None, status: int = 200):
        from flask import Response
        resp = Response(html, status=status, mimetype="text/html; charset=utf-8")
        triggers = ["refreshCounts"]
        if trigger:
            triggers.append(trigger)
        resp.headers["HX-Trigger"] = ",".join(triggers)
        return resp

    @app.route("/")
    def index():
        counts = compute_counts(store)
        projects = store.projects()
        projects.sort(key=lambda p: p.get("name", "").lower())
        return render_template(
            "dashboard.html",
            counts=counts,
            panels=PANELS,
            tz=DEFAULT_TZ,
            projects=projects,
        )

    @app.route("/panel/<name>")
    def panel(name: str):
        if name not in PANELS:
            abort(404)
        try:
            data = panel_data(name, store, app.config.get("EVENT_LOG"))
        except RuntimeError as e:
            return render_template("_error.html", message=str(e)), 502
        return render_template(f"_panel_{name}.html", **data, panel=name)

    @app.route("/counts")
    def counts():
        return render_template("_counts.html", counts=compute_counts(store), panels=PANELS)

    @app.route("/task/<project_id>/<task_id>/action", methods=["POST"])
    def task_action(project_id: str, task_id: str):
        action = request.form.get("action") or ""
        task = get_task_or_404(project_id, task_id)
        try:
            return _dispatch_action(action, task, request.form, htmx_response)
        except ActionError as e:
            return htmx_response(
                f'<div class="error">{e}</div>',
                status=400,
            )

    def _dispatch_action(action: str, task: Dict, form, resp):
        client = app.config["CLIENT"]
        log: EventLog = app.config["EVENT_LOG"]
        projects = project_lookup(store)
        proj_name = (projects.get(task.get("projectId") or "") or {}).get("name")

        if action == "complete":
            client.complete_task(task["projectId"], task["id"])
            log.record("complete", task, project_name=proj_name)
            store.invalidate()
            return resp("")
        if action == "delete":
            client.delete_task(task["projectId"], task["id"])
            log.record("delete", task, project_name=proj_name)
            store.invalidate()
            return resp("")
        if action in ("today", "tomorrow", "plus_days", "specific_date"):
            if action == "today":
                target = today_local()
            elif action == "tomorrow":
                target = today_local() + timedelta(days=1)
            elif action == "plus_days":
                try:
                    n = int(form.get("days", "3"))
                except ValueError:
                    raise ActionError("days must be an integer")
                target = today_local() + timedelta(days=n)
            else:
                raw = form.get("date") or ""
                try:
                    target = date_cls.fromisoformat(raw)
                except ValueError:
                    raise ActionError(f"bad date: {raw}")
            due_before = task.get("dueDate")
            reschedule_to_date(client, store, task, target)
            log.record(
                "reschedule", task, project_name=proj_name,
                due_before=due_before,
                due_after=to_api_iso(workday_morning(target)),
                details={"kind": action, "target_date": target.isoformat()},
            )
            return resp("")
        if action == "someday":
            due_before = task.get("dueDate")
            move_to_someday(client, store, task)
            log.record(
                "reschedule", task, project_name=proj_name,
                due_before=due_before,
                details={"kind": "someday"},
            )
            return resp("")
        if action == "set_priority":
            try:
                p = int(form.get("priority", "0"))
            except ValueError:
                raise ActionError("priority must be int")
            before = task.get("priority")
            set_priority(client, store, task, p)
            log.record(
                "set_priority", task, project_name=proj_name,
                priority_before=before, priority_after=p,
            )
            return resp(_render_card(task["projectId"], task["id"], store))
        if action == "highlight":
            force = form.get("force") == "1"
            existing, new = promote_to_highlight(client, store, task, force=force)
            if existing and new is None:
                view = task_view(task, projects)
                existing_view = task_view(existing, projects)
                html = render_template(
                    "_highlight_conflict.html",
                    task=view, existing=existing_view,
                )
                return resp(html, status=200)
            log.record(
                "highlight", task, project_name=proj_name,
                priority_before=task.get("priority"), priority_after=5,
                details={"demoted_id": existing.get("id") if existing else None,
                         "demoted_title": existing.get("title") if existing else None},
            )
            return resp(_render_card(task["projectId"], task["id"], store))
        if action == "unhighlight":
            set_priority(client, store, task, 3)
            log.record(
                "unhighlight", task, project_name=proj_name,
                priority_before=5, priority_after=3,
            )
            return resp(_render_card(task["projectId"], task["id"], store))
        if action == "assign_inbox":
            pid = form.get("project_id") or ""
            try:
                pri = int(form.get("priority", "3"))
            except ValueError:
                pri = 3
            due = form.get("due_date") or None
            if not pid:
                raise ActionError("project_id required")
            assign_inbox_task(client, store, task, pid, pri, due)
            log.record(
                "assign_inbox", task, project_name=proj_name,
                priority_before=task.get("priority"), priority_after=pri,
                details={"target_project_id": pid, "target_due_date": due},
            )
            return resp("")
        raise ActionError(f"unknown action: {action}")

    def _render_card(project_id: str, task_id: str, store: TickTickStore) -> str:
        # After in-place mutations we re-render the card so the UI updates
        # without a full panel reload.
        store.invalidate()
        for t in store.all_active_tasks():
            if t.get("id") == task_id and t.get("projectId") == project_id:
                view = task_view(t, project_lookup(store))
                return render_template("_card.html", task=view, panel="generic")
        return ""

    @app.route("/refresh", methods=["POST"])
    def refresh():
        store.invalidate()
        return htmx_response("")

    @app.route("/health")
    def health():
        return jsonify({"ok": True})

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_client(mock: bool):
    if mock:
        from .mock_data import MockClient
        return MockClient()
    load_dotenv()
    if not os.getenv("TICKTICK_ACCESS_TOKEN"):
        print(
            "TICKTICK_ACCESS_TOKEN missing. Run `ticktick-auth` first, "
            "or pass --mock to demo with fake data.",
            file=sys.stderr,
        )
        sys.exit(2)
    from .src.ticktick_client import TickTickClient
    return TickTickClient()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TickTick triage dashboard")
    parser.add_argument("--mock", action="store_true",
                       help="Run with seeded fake data (no TickTick credentials needed)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true",
                       help="Don't auto-open the dashboard in a browser")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = _build_client(args.mock)
    if args.mock:
        # Keep the demo DB ephemeral so re-runs always start fresh.
        event_log = EventLog(":memory:", DEFAULT_TZ)
        from .mock_data import seed_mock_events
        seed_mock_events(event_log)
    else:
        event_log = EventLog(str(DEFAULT_DB_PATH), DEFAULT_TZ)
        logger.info("event log: %s", DEFAULT_DB_PATH)
    app = create_app(client, event_log=event_log)

    url = f"http://{args.host}:{args.port}/"
    if args.mock:
        print(f"⚠  Running in MOCK mode (no TickTick API calls). Open {url}")
    else:
        print(f"TickTick dashboard running at {url}")

    if not args.no_browser and not args.debug:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Local web dashboard for triaging TickTick tasks.

Run with `ticktick-companion-dashboard` (uses the same .env tokens as the
MCP server) or `ticktick-companion-dashboard --mock` to demo the UI without
API credentials. `ticktick-dashboard` remains as a backward-compatible alias.

The dashboard reuses `TickTickClient` for all writes, so OAuth refresh and
.env handling are unchanged. A 30-second read-through cache keeps UI swaps
fast without hammering the API.
"""

import argparse
import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date as date_cls, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

from ..api.oauth import TickTickAuth
from ..api.token_store import TokenStore, default_token_store, load_tokens_with_env_fallback
from .event_log import DEFAULT_DB_PATH, EventLog

logger = logging.getLogger(__name__)

# Project IDs from CLAUDE.md
INBOX_PROJECT_ID = "699a5943b1bed115b35b1e10"
SOMEDAY_PROJECT_ID = "69b6e5088f085ebce14b22d6"
WAITING_HOME_PROJECT_ID = "699c8a338f088b3b190a1a5d"  # BR Commercial & BD

PRIORITY_NAMES = {0: "None", 1: "Low", 3: "Medium", 5: "High"}
PRIORITY_ORDER = [5, 3, 1, 0]

PROJECT_FAMILIES = {
    "Bedrock": {
        "699c8a338f088b3b190a1a5d",
        "699c8a3c8f088b3b190a1ba1",
        "69547156d4ca9147cf3c78fa",
    },
    "Extensible": {
        "6988fcb958ca9155b99ecc3f",
        "6757d67c8f0808587783ab86",
        "6851d328bc6ad1525900e1df",
        "69239e3c064f51f8c0a66b2f",
    },
    "Tools + AI": {
        "693a3b6a34db910305e570fc",
        "6925de124d1951f8c0a709b0",
        "6695fb18fdb29194d7492736",
        "686c57f73c47910441e8f414",
    },
    "Study + Work": {
        "67ae557f9ebd91593b682a01",
        "6828b39ea96b91032980817c",
        "668b7ff488305102443ea121",
        "66d74dacbb201101c5b3232b",
        "673a768c7a9a519a677d41c3",
        "673a7a716a1a119a677d590d",
    },
    "Investing": {
        "66884177becd911b75279a94",
        "66c2a448151bd14d76dab830",
        "66c36b538f08a02ea8eedeaf",
        "66f6b22b8e53512eb9cf07a0",
        "66f6b2938ba3112eb9cf0f11",
        "672e789064be5181d618b716",
        "6748ad361bda5112cf35c4be",
        "684db6c3227ed1033cf0fd47",
        "68ccc3155baf11eddd3914db",
        "681102b0fb161104da46de81",
    },
    "Personal Ops": {
        "69239f54252c91f8c0a68ad4",
        "69239fd13854d1f8c0a69082",
        "6695fb3aab509194d7492975",
        "669c9bd88f088125da4c32bf",
        "669df8928f089169e9760578",
        "66c2a48e45f3514d76dab9f0",
    },
}

FAMILY_ORDER = {
    "Inbox": 0,
    "Bedrock": 1,
    "Extensible": 2,
    "Tools + AI": 3,
    "Study + Work": 4,
    "Investing": 5,
    "Personal Ops": 6,
}

PROJECT_TRIAGE_WEIGHTS = {
    "Bedrock": 40,
    "Extensible": 34,
    "Tools + AI": 26,
    "Study + Work": 16,
    "Investing": 12,
    "Personal Ops": 10,
    "Inbox": -25,
    "Someday": -35,
}

BUCKET_LABELS = {
    "overdue": "Overdue",
    "today": "Today",
    "tomorrow": "Tomorrow",
    "week": "This week",
    "unscheduled": "No date",
    "later": "Later",
    "waiting": "Waiting",
    "inbox": "Inbox",
    "someday": "Someday",
}

BUCKET_ORDER = {
    "overdue": 0,
    "today": 1,
    "tomorrow": 2,
    "week": 3,
    "unscheduled": 4,
    "later": 5,
    "waiting": 6,
    "inbox": 7,
    "someday": 8,
}

HIGHLIGHT_PREFIX = "⭐ "
WAITING_PREFIX = "WAITING:"
CLAUDE_PREFIX = "🚩 "
TODAY_NON_WAITING_CAPACITY = 4
PANEL_PAGE_SIZE = 25

DEFAULT_TZ = os.getenv("TICKTICK_TIMEZONE", "America/Los_Angeles")
DEFAULT_SNAPSHOT_PATH = DEFAULT_DB_PATH.with_suffix(".cache.json")
ASSET_VERSION = "20260507-sunsama"


def ticktick_task_url(project_id: Optional[str], task_id: Optional[str],
                      api_base_url: Optional[str] = None) -> str:
    if not project_id or not task_id:
        return ""
    configured_base = api_base_url if api_base_url is not None else os.getenv("TICKTICK_BASE_URL", "")
    web_base = "https://dida365.com" if "dida365.com" in configured_base.lower() else "https://ticktick.com"
    project = quote(str(project_id), safe="")
    task = quote(str(task_id), safe="")
    return f"{web_base}/webapp/#p/{project}/tasks/{task}"


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using %s", name, default)
        return default
    if value < minimum:
        logger.warning("%s must be >= %s; using %s", name, minimum, default)
        return default
    return value


# ---------------------------------------------------------------------------
# Cache layer over TickTickClient
# ---------------------------------------------------------------------------

class TickTickStore:
    """Read-through cache for projects + active tasks.

    The TickTick public API has no bulk-tasks endpoint, so we walk every
    non-closed project once and cache the result. Full refreshes fetch
    project data in parallel; mutations refresh only affected projects.
    """

    def __init__(
        self,
        client,
        ttl_seconds: Optional[int] = None,
        max_workers: Optional[int] = None,
        snapshot_path: Optional[Path] = None,
    ):
        self.client = client
        ttl = ttl_seconds if ttl_seconds is not None else env_int("TICKTICK_DASHBOARD_CACHE_TTL_SECONDS", 60)
        workers = max_workers if max_workers is not None else env_int("TICKTICK_DASHBOARD_FETCH_WORKERS", 6)
        self._ttl = timedelta(seconds=ttl)
        self._max_workers = workers
        self._snapshot_path = Path(snapshot_path).expanduser() if snapshot_path else None
        self._lock = threading.Lock()
        self._projects: Optional[List[Dict]] = None
        self._tasks_by_project: Dict[str, List[Dict]] = {}
        self._fetched_at: Optional[datetime] = None
        self._last_refresh_error: Optional[str] = None
        self._project_errors: Dict[str, str] = {}
        self._loaded_from_snapshot = False
        self._refreshing = False
        self._revision = 0
        self._project_latencies: Dict[str, float] = {}
        self._last_fetch_seconds: Optional[float] = None
        self._last_project_count = 0
        self._load_snapshot()

    def invalidate(self) -> None:
        with self._lock:
            self._projects = None
            self._tasks_by_project = {}
            self._fetched_at = None
            self._last_refresh_error = None
            self._project_errors = {}
            self._loaded_from_snapshot = False
            self._revision += 1

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _ensure_fresh(self) -> None:
        if self.client is None:
            raise RuntimeError("TickTick authentication is required before loading dashboard data.")
        with self._lock:
            if self._loaded_from_snapshot and self._projects is not None:
                self._start_background_refresh_locked()
                return
            if self._fetched_at and (self._now() - self._fetched_at) < self._ttl:
                return
            self._refresh_all_locked()

    def _start_background_refresh_locked(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True

        def refresh() -> None:
            try:
                self.refresh_all()
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=refresh, name="ticktick-refresh", daemon=True).start()

    def _open_project_ids(self) -> List[str]:
        return [
            p["id"]
            for p in (self._projects or [])
            if p.get("id") and not p.get("closed")
        ]

    def _fetch_project_tasks(self, project_id: str) -> Tuple[str, List[Dict], Optional[str], float]:
        started = time.perf_counter()
        data = self.client.get_project_with_data(project_id)
        latency = time.perf_counter() - started
        if isinstance(data, dict) and data.get("error"):
            return project_id, [], str(data["error"]), latency
        return project_id, (data.get("tasks") or []), None, latency

    def _refresh_project_ids_locked(self, project_ids: List[str]) -> None:
        unique_project_ids = list(dict.fromkeys(pid for pid in project_ids if pid))
        if not unique_project_ids:
            return

        workers = max(1, min(self._max_workers, len(unique_project_ids)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch_project_tasks, pid): pid
                for pid in unique_project_ids
            }
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    project_id, tasks, error, latency = future.result()
                except Exception as e:  # defensive: client normally returns {"error": ...}
                    logger.warning("project %s: %s", pid, e)
                    self._tasks_by_project[pid] = []
                    self._project_errors[pid] = str(e)
                    continue

                self._tasks_by_project[project_id] = tasks
                self._project_latencies[project_id] = latency
                if error:
                    logger.warning("project %s: %s", project_id, error)
                    self._project_errors[project_id] = error
                else:
                    self._project_errors.pop(project_id, None)

    def _refresh_all_locked(self) -> None:
        started = time.perf_counter()
        try:
            projects = self.client.get_projects()
            if isinstance(projects, dict) and projects.get("error"):
                raise RuntimeError(f"TickTick get_projects failed: {projects['error']}")
            self._projects = projects or []
            self._tasks_by_project = {}
            self._project_errors = {}
            self._refresh_project_ids_locked(self._open_project_ids())
            self._fetched_at = self._now()
            self._last_refresh_error = None
            self._loaded_from_snapshot = False
            self._last_fetch_seconds = time.perf_counter() - started
            self._last_project_count = len(self._open_project_ids())
            self._revision += 1
            self._save_snapshot_locked()
            logger.info(
                "TickTick refresh fetched %s projects in %.3fs",
                self._last_project_count,
                self._last_fetch_seconds,
            )
        except RuntimeError as e:
            self._last_refresh_error = str(e)
            if self._projects is None:
                raise
            logger.warning("Keeping stale TickTick cache after refresh failure: %s", e)

    def refresh_all(self) -> None:
        with self._lock:
            self._refresh_all_locked()

    def refresh_project(self, project_id: str) -> None:
        self.refresh_projects([project_id])

    def refresh_projects(self, project_ids: List[str]) -> None:
        with self._lock:
            if self._projects is None:
                self._refresh_all_locked()
                return
            self._refresh_project_ids_locked(project_ids)
            self._fetched_at = self._now()
            self._loaded_from_snapshot = False
            self._revision += 1
            self._save_snapshot_locked()

    def _load_snapshot(self) -> None:
        if not self._snapshot_path or not self._snapshot_path.exists():
            return
        try:
            payload = json.loads(self._snapshot_path.read_text())
            projects = payload.get("projects")
            tasks_by_project = payload.get("tasks_by_project")
            fetched_at = parse_iso_dt(payload.get("fetched_at"))
            if not isinstance(projects, list) or not isinstance(tasks_by_project, dict):
                raise ValueError("invalid snapshot shape")
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning("Ignoring dashboard snapshot %s: %s", self._snapshot_path, e)
            return
        self._projects = projects
        self._tasks_by_project = {
            str(project_id): tasks if isinstance(tasks, list) else []
            for project_id, tasks in tasks_by_project.items()
        }
        self._fetched_at = fetched_at
        self._loaded_from_snapshot = True
        self._revision += 1
        logger.info("Loaded stale dashboard snapshot from %s", self._snapshot_path)

    def _save_snapshot_locked(self) -> None:
        if not self._snapshot_path or self._projects is None:
            return
        payload = {
            "version": 1,
            "fetched_at": self._fetched_at.isoformat() if self._fetched_at else "",
            "projects": self._projects,
            "tasks_by_project": self._tasks_by_project,
        }
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._snapshot_path.with_suffix(self._snapshot_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload, separators=(",", ":")))
            tmp_path.replace(self._snapshot_path)
        except OSError as e:
            logger.warning("Could not save dashboard snapshot %s: %s", self._snapshot_path, e)

    @property
    def last_refresh_at(self) -> Optional[datetime]:
        return self._fetched_at

    @property
    def last_refresh_error(self) -> Optional[str]:
        return self._last_refresh_error

    @property
    def project_errors(self) -> Dict[str, str]:
        return dict(self._project_errors)

    @property
    def is_stale_snapshot(self) -> bool:
        return self._loaded_from_snapshot

    @property
    def is_refreshing(self) -> bool:
        return self._refreshing

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def has_cached_data(self) -> bool:
        return self._projects is not None

    @property
    def performance(self) -> Dict[str, Any]:
        return {
            "last_fetch_seconds": self._last_fetch_seconds,
            "project_count": self._last_project_count,
            "project_latencies": dict(self._project_latencies),
        }

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


def task_start(task: Dict) -> Optional[datetime]:
    return parse_iso_dt(task.get("startDate"))


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


def fmt_time(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.astimezone(user_tz()).strftime("%-I:%M %p")


def fmt_time_range(task: Dict) -> str:
    if task.get("isAllDay"):
        return ""
    start = task_start(task)
    due = task_due(task)
    if start and due:
        start_local = start.astimezone(user_tz())
        due_local = due.astimezone(user_tz())
        if start_local.date() == due_local.date():
            return f"{start_local.strftime('%-I:%M')} - {due_local.strftime('%-I:%M %p')}"
    if due:
        return fmt_time(due)
    return fmt_time(start)


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


def reschedule_count_for_task(task: Dict, event_log: Optional[EventLog] = None) -> int:
    if "_reschedule_count" in task:
        return int(task.get("_reschedule_count") or 0)
    if event_log is None:
        return 0
    return event_log.reschedule_count(task.get("id") or "")


def attach_reschedule_counts(tasks: List[Dict], event_log: Optional[EventLog]) -> None:
    if event_log is None:
        return
    counts = event_log.reschedule_counts()
    for task in tasks:
        task["_reschedule_count"] = counts.get(task.get("id") or "", 0)


def project_triage_weight(task: Dict, projects: Dict[str, Dict]) -> int:
    return PROJECT_TRIAGE_WEIGHTS.get(project_family(task, projects), 8)


def score_task_for_triage(
    task: Dict,
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []
    family = project_family(task, projects)
    family_weight = project_triage_weight(task, projects)
    if family_weight:
        score += family_weight
        if family_weight > 0:
            reasons.append(f"{family} focus area")
        elif family == "Inbox":
            reasons.append("Inbox capture needs processing, not overdue pressure")
        elif task.get("projectId") == SOMEDAY_PROJECT_ID:
            reasons.append("Someday item is parked by design")

    priority = task.get("priority") or 0
    priority_weights = {5: 35, 3: 18, 1: 4, 0: 0}
    if priority in priority_weights:
        score += priority_weights[priority]
        if priority == 5:
            reasons.append("current Highlight")
        elif priority == 3:
            reasons.append("marked as next action")
        elif priority == 1:
            reasons.append("low priority")

    overdue_days = days_overdue(task)
    if overdue_days:
        age_weight = min(overdue_days * 3, 45)
        score += age_weight
        reasons.append(f"{overdue_days}d overdue")
        if overdue_days >= 14:
            score += 10
            reasons.append("old enough to renegotiate")
        elif overdue_days >= 3:
            score += 6
            reasons.append("stale")

    reschedules = reschedule_count_for_task(task, event_log)
    if reschedules:
        score += min(reschedules * 8, 32)
        reasons.append(f"pushed {reschedules}x")
    if reschedules >= 3:
        score += 18
        reasons.append("stuck: needs a different decision")

    if is_waiting(task):
        score -= 18
        if overdue_days >= 7:
            score += 12
            reasons.append("waiting follow-up is stale")
        else:
            reasons.append("blocked on someone else")

    if task.get("projectId") == SOMEDAY_PROJECT_ID:
        score -= 18
    if task.get("projectId") == INBOX_PROJECT_ID:
        score -= 10

    checklist_count = len(task.get("items") or [])
    if checklist_count >= 3:
        score += 5
        reasons.append(f"{checklist_count} subtasks")

    return score, reasons[:4]


def recommended_triage_decision(
    task: Dict,
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> str:
    reschedules = reschedule_count_for_task(task, event_log)
    overdue_days = days_overdue(task)
    priority = task.get("priority") or 0
    if is_waiting(task):
        return "Follow up"
    if reschedules >= 3:
        return "Break down"
    if priority == 5:
        return "Do Today"
    if task.get("projectId") == INBOX_PROJECT_ID:
        return "Process"
    if priority <= 1 and overdue_days >= 14:
        return "Park or Drop"
    if overdue_days >= 7 and project_triage_weight(task, projects) <= 12:
        return "Renegotiate"
    if priority >= 3:
        return "Schedule honestly"
    return "Park"


def sort_for_recovery(
    tasks: List[Dict],
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> List[Dict]:
    return sorted(
        tasks,
        key=lambda t: (
            -score_task_for_triage(t, projects, event_log)[0],
            -days_overdue(t),
            task_due(t) or datetime.max.replace(tzinfo=timezone.utc),
            (t.get("title") or "").lower(),
        ),
    )


def task_view(task: Dict, projects: Dict[str, Dict],
              event_log: Optional[EventLog] = None) -> Dict:
    pid = task.get("projectId") or ""
    proj = projects.get(pid) or {}
    bucket = task_bucket(task)
    checklist_items = task.get("items") or []
    triage_score, triage_reasons = score_task_for_triage(task, projects, event_log)
    reschedule_count = reschedule_count_for_task(task, event_log)
    return {
        "id": task.get("id"),
        "project_id": pid,
        "ticktick_url": ticktick_task_url(pid, task.get("id")),
        "project_name": proj.get("name") or "Unknown",
        "project_color": proj.get("color") or "#888",
        "project_family": project_family(task, projects),
        "title": task.get("title") or "(untitled)",
        "content": task.get("content") or "",
        "checklist_count": len(checklist_items),
        "priority": task.get("priority", 0),
        "priority_name": PRIORITY_NAMES.get(task.get("priority", 0), "?"),
        "start_date": task.get("startDate"),
        "due_date": task.get("dueDate"),
        "due_label": fmt_due(task),
        "time_label": fmt_time_range(task),
        "days_overdue": days_overdue(task),
        "is_overdue": is_overdue(task),
        "is_today": is_due_today(task),
        "is_highlight": is_highlight(task),
        "is_waiting": is_waiting(task),
        "is_inbox": pid == INBOX_PROJECT_ID,
        "bucket": bucket,
        "bucket_label": BUCKET_LABELS.get(bucket, bucket.title()),
        "triage_score": triage_score,
        "triage_reasons": triage_reasons,
        "reschedule_count": reschedule_count,
        "is_stuck": reschedule_count >= 3,
        "recommended_decision": recommended_triage_decision(task, projects, event_log),
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


def task_bucket(task: Dict) -> str:
    pid = task.get("projectId")
    if pid == INBOX_PROJECT_ID:
        return "inbox"
    if pid == SOMEDAY_PROJECT_ID:
        return "someday"
    if is_waiting(task):
        return "waiting"
    if is_overdue(task):
        return "overdue"
    if is_due_today(task):
        return "today"
    if is_due_tomorrow(task):
        return "tomorrow"
    due = task_due(task)
    if due:
        days = (due.astimezone(user_tz()).date() - today_local()).days
        if days <= 7:
            return "week"
        return "later"
    return "unscheduled"


def project_family(task: Dict, projects: Dict[str, Dict]) -> str:
    pid = task.get("projectId") or ""
    if pid == INBOX_PROJECT_ID:
        return "Inbox"
    if pid == SOMEDAY_PROJECT_ID:
        return "Someday"
    for family, ids in PROJECT_FAMILIES.items():
        if pid in ids:
            return family
    name = (projects.get(pid) or {}).get("name") or "Other"
    return name


def sort_for_focus(tasks: List[Dict]) -> List[Dict]:
    return sorted(
        tasks,
        key=lambda t: (
            BUCKET_ORDER.get(task_bucket(t), 99),
            0 if is_highlight(t) else 1,
            -(t.get("priority") or 0),
            task_due(t) or datetime.max.replace(tzinfo=timezone.utc),
            (t.get("title") or "").lower(),
        ),
    )


def focus_groups(
    tasks: List[Dict],
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> List[Dict]:
    grouped: Dict[str, List[Dict]] = {}
    for task in tasks:
        # Someday already has its own review lane; keep the focus board to
        # active/open-loop work plus Inbox captures.
        if task.get("projectId") == SOMEDAY_PROJECT_ID:
            continue
        grouped.setdefault(project_family(task, projects), []).append(task)

    out = []
    for family, raw_items in grouped.items():
        items = sort_for_focus(raw_items)
        counts: Dict[str, int] = {}
        project_names = sorted({
            (projects.get(t.get("projectId") or "") or {}).get("name") or "Unknown"
            for t in items
        })
        for item in items:
            bucket = task_bucket(item)
            counts[bucket] = counts.get(bucket, 0) + 1
        count_pills = [
            {"bucket": bucket, "label": BUCKET_LABELS.get(bucket, bucket.title()), "count": counts[bucket]}
            for bucket in sorted(counts, key=lambda b: BUCKET_ORDER.get(b, 99))
        ]
        out.append({
            "name": family,
            "order": FAMILY_ORDER.get(family, 50),
            "tasks": [task_view(t, projects, event_log) for t in items],
            "project_names": project_names,
            "counts": counts,
            "count_pills": count_pills,
            "urgent_count": counts.get("overdue", 0) + counts.get("today", 0),
        })
    return sorted(
        out,
        key=lambda g: (
            g["order"],
            0 if g["urgent_count"] else 1,
            g["name"].lower(),
        ),
    )


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

class ActionError(Exception):
    pass


def require_api_success(result: Dict, action: str) -> Dict:
    if isinstance(result, dict) and result.get("error"):
        raise ActionError(f"{action} failed: {result['error']}")
    return result


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
    return require_api_success(res, "reschedule")


def move_to_someday(client, store: TickTickStore, task: Dict) -> Dict:
    res = client.update_task(
        task_id=task["id"],
        project_id=SOMEDAY_PROJECT_ID,
    )
    return require_api_success(res, "move to Someday")


def mark_waiting(client, store: TickTickStore, task: Dict) -> Dict:
    title = task.get("title") or ""
    if title.startswith(HIGHLIGHT_PREFIX):
        title = title[len(HIGHLIGHT_PREFIX):]
    if title.startswith(CLAUDE_PREFIX):
        title = title[len(CLAUDE_PREFIX):]
    if not title.startswith(WAITING_PREFIX):
        title = f"{WAITING_PREFIX} {title}".strip()
    res = client.update_task(
        task_id=task["id"],
        project_id=WAITING_HOME_PROJECT_ID,
        title=title if title != task.get("title") else None,
        priority=3 if (task.get("priority") or 0) == 0 else task.get("priority"),
    )
    return require_api_success(res, "mark waiting")


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
    return require_api_success(res, "set priority")


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
    return require_api_success(res, "assign inbox task")


# ---------------------------------------------------------------------------
# Counts / panels
# ---------------------------------------------------------------------------

def dashboard_health(tasks: List[Dict], event_log: Optional[EventLog] = None) -> List[Dict]:
    highlights = [t for t in tasks if is_highlight(t)]
    overdue_stale = [t for t in tasks if days_overdue(t) >= 3]
    today_non_waiting = [t for t in tasks if is_due_today(t) and not is_waiting(t)]
    inbox_items = [t for t in tasks if t.get("projectId") == INBOX_PROJECT_ID]
    stale_waiting = [t for t in tasks if is_waiting(t) and days_overdue(t) >= 7]
    stuck_tasks = [
        t for t in tasks
        if event_log is not None and reschedule_count_for_task(t, event_log) >= 3
    ]

    warnings: List[Dict] = []
    if len(highlights) > 1:
        warnings.append({
            "kind": "highlight_conflict",
            "label": "Highlight conflict",
            "summary": f"{len(highlights)} tasks are marked High.",
            "panel": "today",
            "panel_label": "Today",
            "severity": "danger",
        })
    elif not highlights:
        warnings.append({
            "kind": "missing_highlight",
            "label": "No Highlight",
            "summary": "Pick the one task that makes today a win.",
            "panel": "today",
            "panel_label": "Today",
            "severity": "warn",
        })
    if overdue_stale:
        warnings.append({
            "kind": "stale_overdue",
            "label": "Stale overdue",
            "summary": f"{len(overdue_stale)} task{'s' if len(overdue_stale) != 1 else ''} overdue 3+ days.",
            "panel": "triage",
            "panel_label": "Overdue",
            "severity": "danger",
        })
    if len(today_non_waiting) > 4:
        warnings.append({
            "kind": "today_overload",
            "label": "Today overload",
            "summary": f"{len(today_non_waiting)} non-waiting tasks scheduled today.",
            "panel": "today",
            "panel_label": "Today",
            "severity": "warn",
        })
    if inbox_items:
        warnings.append({
            "kind": "inbox",
            "label": "Inbox captures",
            "summary": f"{len(inbox_items)} capture{'s' if len(inbox_items) != 1 else ''} need assignment.",
            "panel": "inbox",
            "panel_label": "Inbox",
            "severity": "notice",
        })
    if stale_waiting:
        warnings.append({
            "kind": "stale_waiting",
            "label": "Waiting follow-up",
            "summary": f"{len(stale_waiting)} waiting task{'s' if len(stale_waiting) != 1 else ''} overdue 7+ days.",
            "panel": "waiting",
            "panel_label": "Waiting",
            "severity": "notice",
        })
    if stuck_tasks:
        warnings.append({
            "kind": "stuck_tasks",
            "label": "Stuck tasks",
            "summary": f"{len(stuck_tasks)} task{'s' if len(stuck_tasks) != 1 else ''} pushed 3+ times.",
            "panel": "triage",
            "panel_label": "Recovery",
            "severity": "warn",
        })
    return warnings


def today_capacity(tasks: List[Dict], projects: Dict[str, Dict],
                   event_log: Optional[EventLog] = None) -> Dict:
    today_items = sort_for_today([t for t in tasks if is_due_today(t)])
    non_waiting = [t for t in today_items if not is_waiting(t)]
    highlight = next((t for t in non_waiting if is_highlight(t)), None)
    others = [t for t in non_waiting if t is not highlight]
    big_things = others[:2]
    tail = others[2:]
    return {
        "count": len(non_waiting),
        "capacity": TODAY_NON_WAITING_CAPACITY,
        "is_over_capacity": len(non_waiting) >= TODAY_NON_WAITING_CAPACITY,
        "highlight": task_view(highlight, projects, event_log) if highlight else None,
        "big_things": [task_view(t, projects, event_log) for t in big_things],
        "tail": [task_view(t, projects, event_log) for t in tail],
        "tail_count": len(tail),
    }


def today_commitment(
    tasks: List[Dict],
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> Dict:
    items = sort_for_today([t for t in tasks if is_due_today(t)])
    highlight = next((t for t in items if is_highlight(t)), None)
    others = [t for t in items if t is not highlight and not is_waiting(t)]
    waiting_today = [t for t in items if t is not highlight and is_waiting(t)]
    big_things = others[:2]
    tail = others[2:] + waiting_today
    return {
        "highlight": task_view(highlight, projects, event_log) if highlight else None,
        "big_things": [task_view(t, projects, event_log) for t in big_things],
        "tail_count": len(tail),
        "today_count": len(items),
        "waiting_count": len(waiting_today),
    }


def today_timebox_plan(
    tasks: List[Dict],
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> Dict:
    today_items = [t for t in tasks if is_due_today(t)]
    timed = [
        t for t in today_items
        if not t.get("isAllDay") and (task_start(t) or task_due(t))
    ]
    untimed = [
        t for t in today_items
        if t not in timed and not is_waiting(t)
    ]
    timed.sort(key=lambda t: (
        task_start(t) or task_due(t) or datetime.max.replace(tzinfo=timezone.utc),
        0 if is_highlight(t) else 1,
        -(t.get("priority") or 0),
    ))

    rows = []
    for task in timed:
        start = task_start(task)
        due = task_due(task)
        duration = ""
        if start and due:
            minutes = max(0, round((due - start).total_seconds() / 60))
            if minutes:
                duration = f"{minutes}m" if minutes < 60 else f"{minutes // 60}h {minutes % 60:02d}m"
        rows.append({
            "task": task_view(task, projects, event_log),
            "time": fmt_time_range(task),
            "duration": duration,
        })

    return {
        "rows": rows,
        "untimed": [task_view(t, projects, event_log) for t in sort_for_today(untimed)[:4]],
        "untimed_count": len(untimed),
        "timed_count": len(rows),
    }


def planning_rituals(
    tasks: List[Dict],
    attention: List[Dict],
    event_log: Optional[EventLog] = None,
) -> List[Dict]:
    today_items = [t for t in tasks if is_due_today(t)]
    today_non_waiting = [t for t in today_items if not is_waiting(t)]
    timed_count = sum(
        1 for t in today_non_waiting
        if not t.get("isAllDay") and (task_start(t) or task_due(t))
    )
    highlight = next((t for t in today_non_waiting if is_highlight(t)), None)
    tomorrow_items = [t for t in tasks if is_due_tomorrow(t)]
    completed_count = 0
    if event_log is not None:
        completed_count = len(event_log.completed_on(event_log.today_local_date()))

    return [
        {
            "name": "Daily Planning",
            "status": "done" if highlight and len(today_non_waiting) <= TODAY_NON_WAITING_CAPACITY else "needs",
            "detail": (
                "Highlight set"
                if highlight and len(today_non_waiting) <= TODAY_NON_WAITING_CAPACITY
                else f"{len(attention)} signal{'s' if len(attention) != 1 else ''}"
            ),
            "panel": "today",
            "panel_label": "Plan",
        },
        {
            "name": "Timebox",
            "status": "done" if timed_count >= len(today_non_waiting) and today_non_waiting else "ready",
            "detail": f"{timed_count}/{len(today_non_waiting)} planned",
            "panel": "today",
            "panel_label": "Schedule",
        },
        {
            "name": "Highlight",
            "status": "ready" if highlight else "needs",
            "detail": (highlight.get("title") or "Highlight") if highlight else "Pick Highlight",
            "panel": "today",
            "panel_label": "Today",
        },
        {
            "name": "Shutdown",
            "status": "done" if completed_count and tomorrow_items else "ready",
            "detail": f"{completed_count} done · {len(tomorrow_items)} tomorrow",
            "panel": "eod",
            "panel_label": "Close",
        },
    ]


def _recommendation(
    kind: str,
    title: str,
    body: str,
    panel: str,
    panel_label: str,
    *,
    task: Optional[Dict] = None,
    projects: Optional[Dict[str, Dict]] = None,
    event_log: Optional[EventLog] = None,
    primary_action: Optional[Dict] = None,
    secondary_actions: Optional[List[Dict]] = None,
) -> Dict:
    view = task_view(task, projects or {}, event_log) if task and projects is not None else None
    return {
        "kind": kind,
        "title": title,
        "body": body,
        "panel": panel,
        "panel_label": panel_label,
        "task": view,
        "primary_action": primary_action,
        "secondary_actions": secondary_actions or [],
    }


def recommended_actions(
    tasks: List[Dict],
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> List[Dict]:
    recs: List[Dict] = []
    highlights = [t for t in tasks if is_highlight(t)]
    today_items = [t for t in tasks if is_due_today(t)]
    today_non_waiting = [t for t in today_items if not is_waiting(t)]
    tomorrow_items = [t for t in tasks if is_due_tomorrow(t)]

    if len(highlights) > 1:
        recs.append(_recommendation(
            "highlight_conflict",
            "Resolve the Highlight conflict",
            "More than one task is marked High. Pick the single real Highlight.",
            "today",
            "Today",
        ))
    elif not highlights:
        candidate = (sort_for_today(today_non_waiting) or sort_for_focus([
            t for t in tasks
            if t.get("projectId") != SOMEDAY_PROJECT_ID and not is_waiting(t)
        ]))
        task = candidate[0] if candidate else None
        recs.append(_recommendation(
            "missing_highlight",
            "Pick today's Highlight",
            "Choose the one task that would make today feel like a win.",
            "today",
            "Today",
            task=task,
            projects=projects,
            event_log=event_log,
            primary_action={"label": "Set Highlight", "action": "highlight"} if task else None,
        ))

    for task in sort_for_recovery([t for t in tasks if is_overdue(t)], projects, event_log)[:2]:
        decision = recommended_triage_decision(task, projects, event_log)
        recs.append(_recommendation(
            "overdue",
            f"Recovery decision: {decision}",
            f"This surfaced because {', '.join(score_task_for_triage(task, projects, event_log)[1])}.",
            "triage",
            "Recovery",
            task=task,
            projects=projects,
            event_log=event_log,
            primary_action={"label": "Move to Today", "action": "today"},
            secondary_actions=[
                {"label": "Tomorrow", "action": "tomorrow"},
                {"label": "Someday", "action": "someday"},
            ],
        ))

    if len(today_non_waiting) > 4:
        recs.append(_recommendation(
            "today_overload",
            "Reduce today's commitment",
            f"{len(today_non_waiting)} non-waiting tasks are scheduled today. Keep the Highlight plus two Big Things.",
            "today",
            "Today",
        ))

    inbox_items = [t for t in tasks if t.get("projectId") == INBOX_PROJECT_ID]
    if inbox_items:
        task = sorted(inbox_items, key=lambda t: (t.get("title") or "").lower())[0]
        recs.append(_recommendation(
            "inbox",
            "Assign the next Inbox capture",
            f"{len(inbox_items)} capture{'s' if len(inbox_items) != 1 else ''} need a project, priority, and date.",
            "inbox",
            "Inbox",
            task=task,
            projects=projects,
            event_log=event_log,
        ))

    stale_waiting = sort_for_triage([t for t in tasks if is_waiting(t) and days_overdue(t) >= 7])
    if stale_waiting:
        recs.append(_recommendation(
            "waiting",
            "Follow up on stale Waiting",
            f"{len(stale_waiting)} waiting task{'s' if len(stale_waiting) != 1 else ''} are overdue 7+ days.",
            "waiting",
            "Waiting",
            task=stale_waiting[0],
            projects=projects,
            event_log=event_log,
            primary_action={"label": "Move to Today", "action": "today"},
        ))

    near_eod = datetime.now(user_tz()).hour >= 15
    if near_eod and tomorrow_items and not any(is_highlight(t) for t in tomorrow_items):
        recs.append(_recommendation(
            "tomorrow_highlight",
            "Pick tomorrow's Highlight",
            "Tomorrow has tasks lined up, but no Highlight yet.",
            "eod",
            "End of Day",
            task=sort_for_today(tomorrow_items)[0],
            projects=projects,
            event_log=event_log,
            primary_action={"label": "Set Highlight", "action": "highlight"},
        ))

    return recs[:5]


def weekly_review_data(
    tasks: List[Dict],
    projects: Dict[str, Dict],
    event_log: Optional[EventLog] = None,
) -> Dict:
    overdue = [t for t in tasks if is_overdue(t)]
    reschedule_counts = event_log.reschedule_counts() if event_log else {}
    most_rescheduled = sorted(
        [t for t in tasks if reschedule_counts.get(t.get("id") or "", 0) > 0],
        key=lambda t: (
            -reschedule_counts.get(t.get("id") or "", 0),
            -score_task_for_triage(t, projects, event_log)[0],
            (t.get("title") or "").lower(),
        ),
    )[:5]
    stale_waiting = sort_for_recovery(
        [t for t in tasks if is_waiting(t) and days_overdue(t) >= 7],
        projects,
        event_log,
    )[:5]
    someday_candidates = sorted(
        [
            t for t in tasks
            if is_overdue(t)
            and not is_waiting(t)
            and (t.get("priority") or 0) <= 1
            and days_overdue(t) >= 7
        ],
        key=lambda t: (-days_overdue(t), (t.get("title") or "").lower()),
    )[:5]

    family_counts: Dict[str, int] = {}
    for task in overdue:
        family = project_family(task, projects)
        family_counts[family] = family_counts.get(family, 0) + 1
    overloaded_families = [
        {"name": family, "count": count}
        for family, count in sorted(
            family_counts.items(),
            key=lambda item: (-item[1], FAMILY_ORDER.get(item[0], 50), item[0]),
        )
        if count >= 3
    ][:4]

    highlights = [t for t in tasks if is_highlight(t)]
    return {
        "oldest_overdue": [
            task_view(t, projects, event_log)
            for t in sort_for_triage(overdue)[:5]
        ],
        "most_rescheduled": [
            task_view(t, projects, event_log)
            for t in most_rescheduled
        ],
        "stale_waiting": [
            task_view(t, projects, event_log)
            for t in stale_waiting
        ],
        "someday_candidates": [
            task_view(t, projects, event_log)
            for t in someday_candidates
        ],
        "highlight_conflicts": [
            task_view(t, projects, event_log)
            for t in highlights
        ] if len(highlights) > 1 else [],
        "overloaded_families": overloaded_families,
    }


def project_pressure_data(tasks: List[Dict], projects: Dict[str, Dict]) -> List[Dict]:
    families: Dict[str, Dict[str, int]] = {}
    for task in tasks:
        family = project_family(task, projects)
        if family in ("Inbox", "Someday"):
            continue
        bucket = families.setdefault(
            family,
            {
                "total_count": 0,
                "overdue_count": 0,
                "today_count": 0,
                "waiting_count": 0,
                "score": 0,
            },
        )
        bucket["total_count"] += 1
        if is_overdue(task):
            bucket["overdue_count"] += 1
        if is_due_today(task):
            bucket["today_count"] += 1
        if is_waiting(task):
            bucket["waiting_count"] += 1

    rows = []
    max_score = 1
    for family, counts in families.items():
        score = (
            counts["overdue_count"] * 3
            + counts["today_count"] * 2
            + counts["waiting_count"]
            + counts["total_count"]
        )
        counts["score"] = score
        max_score = max(max_score, score)
        rows.append({"name": family, **counts})

    rows.sort(
        key=lambda row: (
            -row["overdue_count"],
            -row["today_count"],
            FAMILY_ORDER.get(row["name"], 50),
            row["name"].lower(),
        )
    )
    for row in rows:
        row["bar_percent"] = max(8, min(100, round(row["score"] * 100 / max_score)))
    return rows[:6]


def card_page_data(
    tasks: List[Dict],
    projects: Dict[str, Dict],
    event_log: Optional[EventLog],
    *,
    offset: int = 0,
) -> Dict[str, Any]:
    offset = max(0, offset)
    page = tasks[offset:offset + PANEL_PAGE_SIZE]
    next_offset = offset + PANEL_PAGE_SIZE
    return {
        "tasks": [task_view(t, projects, event_log) for t in page],
        "total_count": len(tasks),
        "next_offset": next_offset if next_offset < len(tasks) else None,
    }


def home_data(store: TickTickStore, event_log: Optional[EventLog] = None) -> Dict:
    projects = project_lookup(store)
    tasks = store.all_active_tasks()
    attach_reschedule_counts(tasks, event_log)
    activity_stats: Dict[str, int] = {}
    completed_today: List[Dict] = []
    if event_log is not None:
        today_str = event_log.today_local_date()
        activity_stats = event_log.stats_on(today_str)
        completed_today = event_log.completed_on(today_str)

    attention = dashboard_health(tasks, event_log)
    return {
        "title": "Home",
        "subtitle": "Choose the next honest move.",
        "rituals": planning_rituals(tasks, attention, event_log),
        "attention": attention,
        "attention_count": len(attention),
        "commitment": today_commitment(tasks, projects, event_log),
        "recommendations": recommended_actions(tasks, projects, event_log),
        "project_pressure": project_pressure_data(tasks, projects),
        "weekly_review": weekly_review_data(tasks, projects, event_log),
        "momentum": {
            "completed_count": len(completed_today),
            "activity_stats": activity_stats,
        },
    }


def compute_counts(store: TickTickStore) -> Dict[str, int]:
    tasks = store.all_active_tasks()
    today_tasks = [t for t in tasks if is_due_today(t)]
    attention = dashboard_health(tasks)
    return {
        "home": len(attention),
        "triage": sum(1 for t in tasks if is_overdue(t)),
        "today": len(today_tasks),
        "inbox": sum(1 for t in tasks if t.get("projectId") == INBOX_PROJECT_ID),
        "waiting": sum(1 for t in tasks if is_waiting(t)),
        "someday": sum(1 for t in tasks if t.get("projectId") == SOMEDAY_PROJECT_ID),
        "highlight_conflicts": sum(1 for t in tasks if is_highlight(t)),
    }


def panel_data(
    name: str,
    store: TickTickStore,
    event_log: Optional[EventLog] = None,
    *,
    offset: int = 0,
) -> Dict:
    projects = project_lookup(store)
    tasks = store.all_active_tasks()
    attach_reschedule_counts(tasks, event_log)
    if name == "home":
        return home_data(store, event_log)
    if name == "focus":
        groups = focus_groups(tasks, projects, event_log)
        return {
            "title": "Focus board",
            "subtitle": "Related work clustered by area.",
            "groups": groups,
            "total": sum(len(g["tasks"]) for g in groups),
            "bucket_labels": BUCKET_LABELS,
        }
    if name == "triage":
        items = [t for t in tasks if is_overdue(t)]
        items = sort_for_recovery(items, projects, event_log)
        page = card_page_data(items, projects, event_log, offset=offset)
        return {
            "title": "Recovery Mode",
            "subtitle": "Decide what still matters, what to renegotiate, what to park, and what to drop.",
            **page,
            "capacity": today_capacity(tasks, projects, event_log),
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
            "highlight": task_view(highlight, projects, event_log) if highlight else None,
            "big_three": [task_view(t, projects, event_log) for t in big_three],
            "tail": [task_view(t, projects, event_log) for t in tail],
            "timebox_plan": today_timebox_plan(items, projects, event_log),
        }
    if name == "tomorrow":
        items = [t for t in tasks if is_due_tomorrow(t)]
        return {
            "title": "Tomorrow",
            "subtitle": "What you've already lined up. Push more from Today during End of Day.",
            "tasks": [task_view(t, projects, event_log) for t in sort_for_today(items)],
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
            "tasks": [task_view(t, projects, event_log) for t in items],
            "projects": active_projects,
        }
    if name == "waiting":
        items = [t for t in tasks if is_waiting(t)]
        items = sort_for_triage(items)
        return {
            "title": "Waiting for",
            "subtitle": "Tasks blocked on someone else.",
            "tasks": [task_view(t, projects, event_log) for t in items],
        }
    if name == "someday":
        items = [t for t in tasks if t.get("projectId") == SOMEDAY_PROJECT_ID]
        items.sort(key=lambda t: t.get("title") or "")
        page = card_page_data(items, projects, event_log, offset=offset)
        return {
            "title": "Someday/Maybe",
            "subtitle": "Scan during weekly review. Promote anything timely.",
            **page,
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
            "unfinished": [task_view(t, projects, event_log) for t in unfinished_today],
            "tomorrow": [task_view(t, projects, event_log) for t in sort_for_today(tomorrow_items)],
            "highlight_for_tomorrow": next(
                (task_view(t, projects, event_log) for t in tomorrow_items if is_highlight(t)),
                None,
            ),
            "completed_today": completed_today,
            "activity_stats": activity_stats,
        }
    raise ActionError(f"unknown panel: {name}")


PANELS = ["home", "triage", "today", "inbox", "waiting", "someday", "eod"]


def cache_status(store: TickTickStore) -> Dict[str, Any]:
    refreshed_at = store.last_refresh_at
    if store.is_stale_snapshot and store.is_refreshing:
        label = "Refreshing live data..."
    elif store.is_stale_snapshot:
        label = "Using saved snapshot"
    elif refreshed_at:
        local = refreshed_at.astimezone(user_tz())
        label = "Refreshed " + local.strftime("%-I:%M:%S %p")
    else:
        label = "Not loaded yet"
    project_errors = store.project_errors
    return {
        "last_refresh_at": refreshed_at.isoformat() if refreshed_at else "",
        "last_refresh_label": label,
        "last_refresh_error": store.last_refresh_error or "",
        "project_error_count": len(project_errors),
        "is_stale": store.is_stale_snapshot,
        "is_refreshing": store.is_refreshing,
    }


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app(
    client,
    event_log: Optional[EventLog] = None,
    snapshot_path: Optional[Path] = None,
    token_store: Optional[TokenStore] = None,
) -> Flask:
    package_root = Path(__file__).parent
    app = Flask(
        __name__,
        template_folder=str(package_root / "templates"),
        static_folder=str(package_root / "static"),
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.secret_key = (
        os.getenv("TICKTICK_DASHBOARD_SECRET_KEY")
        or os.getenv("TICKTICK_DASHBOARD_PASSWORD")
        or secrets.token_hex(32)
    )
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("VERCEL") == "1",
    )
    dashboard_password = os.getenv("TICKTICK_DASHBOARD_PASSWORD")
    auth_enabled = bool(dashboard_password)
    token_store = token_store or default_token_store()
    store = TickTickStore(client, snapshot_path=snapshot_path)
    if event_log is None:
        event_log = EventLog(":memory:", DEFAULT_TZ)
    app.config["STORE"] = store
    app.config["CLIENT"] = client
    app.config["EVENT_LOG"] = event_log
    app.config["PANEL_CACHE"] = {}
    app.config["DASHBOARD_AUTH_ENABLED"] = auth_enabled
    app.config["DASHBOARD_PASSWORD"] = dashboard_password or ""
    app.config["TICKTICK_CLIENT_ID"] = os.getenv("TICKTICK_CLIENT_ID") or ""
    app.config["TICKTICK_CLIENT_SECRET"] = os.getenv("TICKTICK_CLIENT_SECRET") or ""
    app.config["TOKEN_STORE"] = token_store

    def clear_panel_cache() -> None:
        app.config["PANEL_CACHE"].clear()

    def is_logged_in() -> bool:
        return not app.config["DASHBOARD_AUTH_ENABLED"] or session.get("dashboard_authenticated") is True

    def htmx_redirect(location: str, status: int = 401) -> Response:
        resp = Response("", status=status)
        resp.headers["HX-Redirect"] = location
        return resp

    @app.before_request
    def require_dashboard_login():
        if not app.config["DASHBOARD_AUTH_ENABLED"]:
            return None
        allowed_endpoints = {
            "login",
            "health",
            "static",
            "ticktick_oauth_callback",
        }
        if request.endpoint in allowed_endpoints:
            return None
        if is_logged_in():
            return None
        if request.headers.get("HX-Request"):
            return htmx_redirect(url_for("login"))
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))

    def ticktick_ready() -> bool:
        return app.config.get("CLIENT") is not None

    def is_ticktick_auth_error(error: Exception) -> bool:
        text = str(error).lower()
        return "401" in text or "unauthorized" in text or "invalid_grant" in text

    def render_ticktick_setup(message: Optional[str] = None, status: int = 200):
        has_client_credentials = bool(app.config["TICKTICK_CLIENT_ID"] and app.config["TICKTICK_CLIENT_SECRET"])
        redirect_uri = os.getenv("TICKTICK_REDIRECT_URI") or url_for("ticktick_oauth_callback", _external=True)
        response = make_response(render_template(
            "auth_setup.html",
            message=message,
            has_client_credentials=has_client_credentials,
            redirect_uri=redirect_uri,
            logged_in=is_logged_in(),
        ), status)
        return response

    def rebuild_ticktick_client() -> bool:
        try:
            from ..api.client import TickTickClient
            new_client = TickTickClient(token_store=app.config["TOKEN_STORE"])
        except ValueError as e:
            logger.warning("TickTick client is not ready: %s", e)
            app.config["CLIENT"] = None
            store.client = None
            return False
        app.config["CLIENT"] = new_client
        store.client = new_client
        store.invalidate()
        clear_panel_cache()
        return True

    def add_perf_headers(resp, *, started: float, render_seconds: float, cache_state: str):
        total_seconds = time.perf_counter() - started
        ticktick_seconds = store.performance.get("last_fetch_seconds")
        timings = [
            f"render;dur={render_seconds * 1000:.1f}",
            f"total;dur={total_seconds * 1000:.1f}",
        ]
        if ticktick_seconds is not None:
            timings.append(f"ticktick;dur={ticktick_seconds * 1000:.1f}")
        resp.headers["Server-Timing"] = ", ".join(timings)
        resp.headers["X-TickTick-Cache"] = cache_state
        resp.headers["X-Response-Bytes"] = str(len(resp.get_data()))
        return resp

    def get_task_or_404(project_id: str, task_id: str) -> Dict:
        for t in store.all_active_tasks():
            if t.get("id") == task_id and t.get("projectId") == project_id:
                return t
        # tolerate stale cache: refresh once and retry
        store.refresh_project(project_id)
        for t in store.all_active_tasks():
            if t.get("id") == task_id and t.get("projectId") == project_id:
                return t
        abort(404, description=f"task {task_id} not found in project {project_id}")

    def htmx_response(
        html: str = "",
        trigger: Optional[str] = None,
        status: int = 200,
        retarget: Optional[str] = None,
        reswap: Optional[str] = None,
    ):
        resp = Response(html, status=status, mimetype="text/html; charset=utf-8")
        triggers = ["refreshCounts"]
        if trigger:
            triggers.append(trigger)
        resp.headers["HX-Trigger"] = ",".join(triggers)
        if retarget:
            resp.headers["HX-Retarget"] = retarget
        if reswap:
            resp.headers["HX-Reswap"] = reswap
        return resp

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not app.config["DASHBOARD_AUTH_ENABLED"]:
            return redirect(url_for("index"))
        error = None
        next_url = request.values.get("next") or url_for("index")
        if request.method == "POST":
            submitted = request.form.get("password") or ""
            expected = app.config["DASHBOARD_PASSWORD"]
            if hmac.compare_digest(submitted, expected):
                session.clear()
                session["dashboard_authenticated"] = True
                return redirect(next_url if next_url.startswith("/") else url_for("index"))
            error = "Incorrect password."
        return render_template("login.html", error=error, next_url=next_url)

    @app.route("/logout", methods=["POST", "GET"])
    def logout():
        session.clear()
        return redirect(url_for("login") if app.config["DASHBOARD_AUTH_ENABLED"] else url_for("index"))

    @app.route("/auth/ticktick/start")
    def ticktick_oauth_start():
        if not app.config["TICKTICK_CLIENT_ID"] or not app.config["TICKTICK_CLIENT_SECRET"]:
            return render_ticktick_setup("TickTick client credentials are missing.", status=400)
        redirect_uri = os.getenv("TICKTICK_REDIRECT_URI") or url_for("ticktick_oauth_callback", _external=True)
        state = secrets.token_urlsafe(32)
        session["ticktick_oauth_state"] = state
        auth = TickTickAuth(
            client_id=app.config["TICKTICK_CLIENT_ID"],
            client_secret=app.config["TICKTICK_CLIENT_SECRET"],
            redirect_uri=redirect_uri,
            token_store=app.config["TOKEN_STORE"],
        )
        return redirect(auth.get_authorization_url(state=state))

    @app.route("/auth/ticktick/callback")
    def ticktick_oauth_callback():
        expected_state = session.get("ticktick_oauth_state")
        actual_state = request.args.get("state")
        if not expected_state or actual_state != expected_state:
            return render_ticktick_setup("TickTick authorization state did not match. Please try again.", status=400)
        if request.args.get("error"):
            return render_ticktick_setup(f"TickTick authorization failed: {request.args['error']}", status=400)
        code = request.args.get("code")
        if not code:
            return render_ticktick_setup("TickTick did not return an authorization code.", status=400)
        redirect_uri = os.getenv("TICKTICK_REDIRECT_URI") or url_for("ticktick_oauth_callback", _external=True)
        auth = TickTickAuth(
            client_id=app.config["TICKTICK_CLIENT_ID"],
            client_secret=app.config["TICKTICK_CLIENT_SECRET"],
            redirect_uri=redirect_uri,
            token_store=app.config["TOKEN_STORE"],
        )
        result = auth.exchange_authorization_code(code)
        session.pop("ticktick_oauth_state", None)
        if "successful" not in result.lower():
            return render_ticktick_setup(result, status=400)
        if not rebuild_ticktick_client():
            return render_ticktick_setup("TickTick authorized, but the API client could not be initialized.", status=500)
        return redirect(url_for("index"))

    @app.route("/")
    def index():
        if not ticktick_ready():
            return render_ticktick_setup()
        counts = {}
        initial_home_html = ""
        if store.has_cached_data:
            try:
                counts = compute_counts(store)
                data = panel_data("home", store, app.config.get("EVENT_LOG"))
                initial_home_html = render_template("_panel_home.html", **data, panel="home")
            except RuntimeError:
                counts = {}
                initial_home_html = ""
        return render_template(
            "dashboard.html",
            counts=counts,
            panels=PANELS,
            tz=DEFAULT_TZ,
            cache=cache_status(store),
            initial_home_html=initial_home_html,
            asset_version=ASSET_VERSION,
        )

    @app.route("/panel/<name>")
    def panel(name: str):
        if name not in PANELS:
            abort(404)
        if not ticktick_ready():
            return render_ticktick_setup(status=401)
        started = time.perf_counter()
        log: EventLog = app.config["EVENT_LOG"]
        cache_key = (name, store.revision, log.revision)
        panel_cache = app.config["PANEL_CACHE"]
        if cache_key in panel_cache:
            html = panel_cache[cache_key]
            resp = make_response(html)
            return add_perf_headers(
                resp,
                started=started,
                render_seconds=0.0,
                cache_state="hit-stale" if store.is_stale_snapshot else "hit",
            )
        try:
            data_started = time.perf_counter()
            data = panel_data(name, store, app.config.get("EVENT_LOG"))
            data_seconds = time.perf_counter() - data_started
        except RuntimeError as e:
            if is_ticktick_auth_error(e):
                return render_ticktick_setup("TickTick authorization expired. Reconnect TickTick to continue.", status=401)
            return render_template("_error.html", message=str(e)), 502
        render_started = time.perf_counter()
        html = render_template(f"_panel_{name}.html", **data, panel=name)
        render_seconds = time.perf_counter() - render_started
        cache_key = (name, store.revision, log.revision)
        panel_cache[cache_key] = html
        resp = make_response(html)
        resp.headers["Server-Timing"] = (
            f"data;dur={data_seconds * 1000:.1f}, "
            f"render;dur={render_seconds * 1000:.1f}, "
            f"total;dur={(time.perf_counter() - started) * 1000:.1f}"
        )
        resp.headers["X-TickTick-Cache"] = "miss-stale" if store.is_stale_snapshot else "miss"
        resp.headers["X-Response-Bytes"] = str(len(resp.get_data()))
        return resp

    @app.route("/panel/<name>/page")
    def panel_page(name: str):
        if name not in {"triage", "someday"}:
            abort(404)
        if not ticktick_ready():
            return render_ticktick_setup(status=401)
        try:
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            abort(400)
        data = panel_data(name, store, app.config.get("EVENT_LOG"), offset=offset)
        return render_template("_cards_page.html", **data, panel=name)

    @app.route("/counts")
    def counts():
        started = time.perf_counter()
        if not ticktick_ready():
            return make_response("", 204)
        try:
            values = compute_counts(store)
        except RuntimeError:
            values = {}
        render_started = time.perf_counter()
        html = render_template(
            "_counts.html",
            counts=values,
            panels=PANELS,
            cache=cache_status(store),
        )
        resp = make_response(html)
        return add_perf_headers(
            resp,
            started=started,
            render_seconds=time.perf_counter() - render_started,
            cache_state="stale" if store.is_stale_snapshot else "fresh",
        )

    @app.route("/task/<project_id>/<task_id>/action", methods=["POST"])
    def task_action(project_id: str, task_id: str):
        if not ticktick_ready():
            return render_ticktick_setup(status=401)
        action = request.form.get("action") or ""
        task = get_task_or_404(project_id, task_id)
        try:
            return _dispatch_action(action, task, request.form, htmx_response)
        except ActionError as e:
            return htmx_response(_render_card(project_id, task_id, store, action_error=str(e)))

    def _dispatch_action(action: str, task: Dict, form, resp):
        client = app.config["CLIENT"]
        log: EventLog = app.config["EVENT_LOG"]
        projects = project_lookup(store)
        proj_name = (projects.get(task.get("projectId") or "") or {}).get("name")

        if action == "complete":
            require_api_success(client.complete_task(task["projectId"], task["id"]), "complete")
            log.record("complete", task, project_name=proj_name)
            store.refresh_project(task["projectId"])
            clear_panel_cache()
            return resp("")
        if action == "delete":
            require_api_success(client.delete_task(task["projectId"], task["id"]), "delete")
            log.record("delete", task, project_name=proj_name)
            store.refresh_project(task["projectId"])
            clear_panel_cache()
            return resp("")
        if action in ("today", "tomorrow", "plus_days", "specific_date"):
            if action == "today":
                target = today_local()
                if _needs_today_capacity_guard(task, form):
                    html = render_template(
                        "_capacity_guard.html",
                        task=task_view(task, projects, log),
                        capacity=today_capacity(store.all_active_tasks(), projects, log),
                    )
                    return resp(html, retarget="#modal-root", reswap="innerHTML")
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
            reason = form.get("reason") or None
            log.record(
                "reschedule", task, project_name=proj_name,
                due_before=due_before,
                due_after=to_api_iso(workday_morning(target)),
                details={"kind": action, "target_date": target.isoformat(), "reason": reason},
            )
            store.refresh_project(task["projectId"])
            clear_panel_cache()
            return resp("")
        if action == "someday":
            due_before = task.get("dueDate")
            move_to_someday(client, store, task)
            log.record(
                "reschedule", task, project_name=proj_name,
                due_before=due_before,
                details={"kind": "someday", "reason": form.get("reason") or None},
            )
            store.refresh_projects([task["projectId"], SOMEDAY_PROJECT_ID])
            clear_panel_cache()
            return resp("")
        if action == "waiting":
            mark_waiting(client, store, task)
            log.record(
                "mark_waiting", task, project_name=proj_name,
                details={"target_project_id": WAITING_HOME_PROJECT_ID},
            )
            store.refresh_projects([task["projectId"], WAITING_HOME_PROJECT_ID])
            clear_panel_cache()
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
            store.refresh_project(task["projectId"])
            clear_panel_cache()
            return resp(_render_card(task["projectId"], task["id"], store))
        if action == "highlight":
            force = form.get("force") == "1"
            existing, new = promote_to_highlight(client, store, task, force=force)
            if existing and new is None:
                view = task_view(task, projects, log)
                existing_view = task_view(existing, projects, log)
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
            touched = [task["projectId"]]
            if existing:
                touched.append(existing["projectId"])
            store.refresh_projects(touched)
            clear_panel_cache()
            return resp(_render_card(task["projectId"], task["id"], store))
        if action == "unhighlight":
            set_priority(client, store, task, 3)
            log.record(
                "unhighlight", task, project_name=proj_name,
                priority_before=5, priority_after=3,
            )
            store.refresh_project(task["projectId"])
            clear_panel_cache()
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
            store.refresh_projects([task["projectId"], pid])
            clear_panel_cache()
            return resp("")
        raise ActionError(f"unknown action: {action}")

    def _needs_today_capacity_guard(task: Dict, form) -> bool:
        if form.get("force_capacity") == "1":
            return False
        if is_waiting(task) or is_due_today(task):
            return False
        projects = project_lookup(store)
        capacity = today_capacity(store.all_active_tasks(), projects, app.config.get("EVENT_LOG"))
        return bool(capacity["is_over_capacity"])

    @app.route("/task/<project_id>/<task_id>/drag", methods=["POST"])
    def task_drag(project_id: str, task_id: str):
        if not ticktick_ready():
            return jsonify({"ok": False, "error": "TickTick authentication required"}), 401
        target = request.form.get("target") or ""
        task = get_task_or_404(project_id, task_id)
        client = app.config["CLIENT"]
        log: EventLog = app.config["EVENT_LOG"]
        projects = project_lookup(store)
        proj_name = (projects.get(task.get("projectId") or "") or {}).get("name")
        due_before = task.get("dueDate")
        try:
            if target == "today":
                target_date = today_local()
                reschedule_to_date(client, store, task, target_date)
                details = {"kind": "drag_today", "target_date": target_date.isoformat()}
                due_after = to_api_iso(workday_morning(target_date))
                action = "reschedule"
                touched = [task["projectId"]]
            elif target == "tomorrow":
                target_date = today_local() + timedelta(days=1)
                reschedule_to_date(client, store, task, target_date)
                details = {"kind": "drag_tomorrow", "target_date": target_date.isoformat()}
                due_after = to_api_iso(workday_morning(target_date))
                action = "reschedule"
                touched = [task["projectId"]]
            elif target == "week":
                target_date = today_local() + timedelta(days=3)
                reschedule_to_date(client, store, task, target_date)
                details = {"kind": "drag_week", "target_date": target_date.isoformat()}
                due_after = to_api_iso(workday_morning(target_date))
                action = "reschedule"
                touched = [task["projectId"]]
            elif target == "someday":
                move_to_someday(client, store, task)
                details = {"kind": "drag_someday"}
                due_after = None
                action = "reschedule"
                touched = [task["projectId"], SOMEDAY_PROJECT_ID]
            elif target == "waiting":
                mark_waiting(client, store, task)
                details = {"kind": "drag_waiting", "target_project_id": WAITING_HOME_PROJECT_ID}
                due_after = due_before
                action = "mark_waiting"
                touched = [task["projectId"], WAITING_HOME_PROJECT_ID]
            else:
                raise ActionError(f"unknown drag target: {target}")
        except ActionError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        store.refresh_projects(touched)
        log.record(
            action, task, project_name=proj_name,
            due_before=due_before, due_after=due_after, details=details,
        )
        clear_panel_cache()
        return jsonify({"ok": True, "target": target})

    @app.route("/task/<project_id>/<task_id>/diagnose")
    def diagnose_task(project_id: str, task_id: str):
        if not ticktick_ready():
            return render_ticktick_setup(status=401)
        task = get_task_or_404(project_id, task_id)
        projects = project_lookup(store)
        log: EventLog = app.config["EVENT_LOG"]
        title = (task.get("title") or "(untitled)").removeprefix(HIGHLIGHT_PREFIX).removeprefix(CLAUDE_PREFIX).strip()
        if is_waiting(task):
            first_step = "Send a short follow-up or decide the next person who owns the wait."
            rewrite = title
        elif len(title.split()) <= 4 or task_view(task, projects, log)["is_stuck"]:
            first_step = "Define the next physical action that would take 10 minutes or less."
            rewrite = f"Clarify next step for: {title}"
        else:
            first_step = "Spend 10 minutes opening the relevant notes, draft, or thread and make the next edit."
            rewrite = title
        suggestions = [
            first_step,
            "If this still matters, schedule one concrete next action instead of the whole project.",
            "If it no longer matters this week, park it in Someday or drop it deliberately.",
        ]
        return render_template(
            "_diagnosis.html",
            task=task_view(task, projects, log),
            rewrite=rewrite,
            suggestions=suggestions,
        )

    def _render_card(project_id: str, task_id: str, store: TickTickStore,
                     action_error: Optional[str] = None) -> str:
        for t in store.all_active_tasks():
            if t.get("id") == task_id and t.get("projectId") == project_id:
                view = task_view(t, project_lookup(store), app.config.get("EVENT_LOG"))
                return render_template(
                    "_card.html",
                    task=view,
                    panel="generic",
                    action_error=action_error,
                )
        return ""

    @app.route("/refresh", methods=["POST"])
    def refresh():
        if not ticktick_ready() and not rebuild_ticktick_client():
            return render_ticktick_setup("TickTick authentication required.", status=401)
        try:
            store.refresh_all()
        except RuntimeError as e:
            if is_ticktick_auth_error(e):
                return render_ticktick_setup("TickTick authorization expired. Reconnect TickTick to continue.", status=401)
            return htmx_response(f'<div class="error">{e}</div>', status=502)
        clear_panel_cache()
        return htmx_response("", trigger="reloadPanel")

    @app.route("/health")
    def health():
        return jsonify({"ok": True})

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_client(mock: bool, token_store: Optional[TokenStore] = None):
    if mock:
        from .mock_data import MockClient
        return MockClient()
    load_dotenv()
    token_store = token_store or default_token_store()
    tokens = load_tokens_with_env_fallback(token_store)
    if not tokens.get("TICKTICK_ACCESS_TOKEN"):
        print(
            "TICKTICK_ACCESS_TOKEN missing. Start the dashboard and use "
            "the in-app TickTick authorization flow, or pass --mock to demo "
            "with fake data.",
            file=sys.stderr,
        )
        return None
    from ..api.client import TickTickClient
    return TickTickClient(token_store=token_store)


def _profile_load(app: Flask) -> int:
    store: TickTickStore = app.config["STORE"]
    event_log: EventLog = app.config["EVENT_LOG"]

    store.invalidate()
    started = time.perf_counter()
    cold_home = panel_data("home", store, event_log)
    cold_seconds = time.perf_counter() - started

    started = time.perf_counter()
    warm_home = panel_data("home", store, event_log)
    warm_seconds = time.perf_counter() - started

    perf = store.performance
    task_count = len(store.all_active_tasks())
    print("Dashboard load profile")
    print(f"  cold home data: {cold_seconds:.3f}s")
    print(f"  warm home data: {warm_seconds:.3f}s")
    print(f"  projects fetched: {perf.get('project_count') or 0}")
    print(f"  active tasks: {task_count}")
    print(f"  attention items: {cold_home.get('attention_count', 0)}")
    print(f"  warm recommendations: {len(warm_home.get('recommendations', []))}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TickTick triage dashboard")
    parser.add_argument("--mock", action="store_true",
                       help="Run with seeded fake data (no TickTick credentials needed)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true",
                       help="Don't auto-open the dashboard in a browser")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--profile-load", action="store_true",
                       help="Load dashboard data once cold and once warm, then print timings")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token_store = default_token_store()
    client = _build_client(args.mock, token_store=token_store)
    if args.mock:
        # Keep the demo DB ephemeral so re-runs always start fresh.
        event_log = EventLog(":memory:", DEFAULT_TZ)
        from .mock_data import seed_mock_events
        seed_mock_events(event_log)
    else:
        event_log = EventLog(str(DEFAULT_DB_PATH), DEFAULT_TZ)
        logger.info("event log: %s", DEFAULT_DB_PATH)
    snapshot_path = None if args.mock else DEFAULT_SNAPSHOT_PATH
    app = create_app(client, event_log=event_log, snapshot_path=snapshot_path, token_store=token_store)

    if args.profile_load:
        return _profile_load(app)

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

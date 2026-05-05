"""Fake TickTick dataset for `ticktick-dashboard --mock` mode.

The mock client mimics the subset of `TickTickClient` that the dashboard
uses, so the UI can be exercised end-to-end without real API credentials.
Project IDs and names mirror the user's CLAUDE.md so the dashboard looks
familiar in mock mode.
"""

import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from typing import Dict, List
from zoneinfo import ZoneInfo


def _user_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("TICKTICK_TIMEZONE", "America/Los_Angeles"))


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_user_tz())
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _seed_projects() -> List[Dict]:
    return [
        {"id": "699a5943b1bed115b35b1e10", "name": "Inbox", "color": "#9aa0a6", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "699c8a338f088b3b190a1a5d", "name": "BR Commercial & BD", "color": "#E84A4A", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "699c8a3c8f088b3b190a1ba1", "name": "BR Ops & Intelligence", "color": "#E84A4A", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "6988fcb958ca9155b99ecc3f", "name": "BSIF Fellowship", "color": "#FFB900", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "693a3b6a34db910305e570fc", "name": "Tools", "color": "#4A90E2", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "6925de124d1951f8c0a709b0", "name": "AI Research", "color": "#9B59B6", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "6757d67c8f0808587783ab86", "name": "GTM & Relationships", "color": "#27AE60", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "6851d328bc6ad1525900e1df", "name": "Product & Engineering", "color": "#16A085", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "69239e3c064f51f8c0a66b2f", "name": "Strategy & Research", "color": "#2ECC71", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "67ae557f9ebd91593b682a01", "name": "PS Agency", "color": "#F39C12", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "6828b39ea96b91032980817c", "name": "CS Study", "color": "#3498DB", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "69b6e5088f085ebce14b22d6", "name": "Someday/Maybe", "color": "#7F8C8D", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "69547156d4ca9147cf3c78fa", "name": "Bedrock Robotics (closed)", "color": "#bdbdbd", "viewMode": "list", "closed": True, "kind": "TASK"},
    ]


def _seed_tasks() -> List[Dict]:
    tz = _user_tz()
    now_local = datetime.now(tz)
    now = now_local.astimezone(timezone.utc)
    # Build "today" timestamps in the user's tz so they always land on the
    # current local calendar day, regardless of what tz the host runs in.
    today_10 = now_local.replace(hour=10, minute=0, second=0, microsecond=0)
    today_14 = now_local.replace(hour=14, minute=0, second=0, microsecond=0)
    tasks = [
        # --- Overdue (varying staleness) ---
        {
            "id": "t-overdue-1",
            "projectId": "699c8a338f088b3b190a1a5d",
            "title": "🚩 Follow up with Acme on contract redlines",
            "content": "They went quiet after we sent v2 of the MSA.",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=4)),
            "dueDate": _iso(now - timedelta(days=4)),
        },
        {
            "id": "t-overdue-2",
            "projectId": "693a3b6a34db910305e570fc",
            "title": "🚩 Push v0.4 release notes",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=2, hours=3)),
            "dueDate": _iso(now - timedelta(days=2, hours=3)),
        },
        {
            "id": "t-overdue-3",
            "projectId": "6988fcb958ca9155b99ecc3f",
            "title": "Review Extensible pitch deck v3",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=1, hours=5)),
            "dueDate": _iso(now - timedelta(days=1, hours=5)),
        },
        {
            "id": "t-overdue-4",
            "projectId": "699c8a338f088b3b190a1a5d",
            "title": "WAITING: Legal sign-off on MSA",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=7)),
            "dueDate": _iso(now - timedelta(days=7)),
        },
        {
            "id": "t-overdue-5",
            "projectId": "67ae557f9ebd91593b682a01",
            "title": "🚩 Send PS Agency invoice for April",
            "priority": 1,
            "status": 0,
            "startDate": _iso(now - timedelta(days=11)),
            "dueDate": _iso(now - timedelta(days=11)),
        },
        # --- Due today ---
        {
            "id": "t-today-1",
            "projectId": "6988fcb958ca9155b99ecc3f",
            "title": "⭐ Write investor memo draft",
            "content": "Two-pager on Extensible's wedge.",
            "priority": 5,
            "status": 0,
            "startDate": _iso(today_10),
            "dueDate": _iso(today_10 + timedelta(minutes=30)),
        },
        {
            "id": "t-today-2",
            "projectId": "699c8a338f088b3b190a1a5d",
            "title": "🚩 BR weekly standup notes",
            "priority": 3,
            "status": 0,
            "startDate": _iso(today_14),
            "dueDate": _iso(today_14 + timedelta(minutes=30)),
        },
        {
            "id": "t-today-3",
            "projectId": "699c8a3c8f088b3b190a1ba1",
            "title": "🚩 Triage ops Slack backlog",
            "priority": 3,
            "status": 0,
            "startDate": _iso(today_14 + timedelta(hours=2)),
            "dueDate": _iso(today_14 + timedelta(hours=2, minutes=30)),
        },
        {
            "id": "t-today-4",
            "projectId": "699c8a338f088b3b190a1a5d",
            "title": "WAITING: Recruiting reply from Sarah",
            "priority": 1,
            "status": 0,
            "startDate": _iso(today_14 + timedelta(hours=4)),
            "dueDate": _iso(today_14 + timedelta(hours=4)),
        },
        # --- Due tomorrow ---
        {
            "id": "t-tomorrow-1",
            "projectId": "6925de124d1951f8c0a709b0",
            "title": "🚩 Read DSPy paper",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now + timedelta(days=1)),
            "dueDate": _iso(now + timedelta(days=1)),
        },
        {
            "id": "t-tomorrow-2",
            "projectId": "6851d328bc6ad1525900e1df",
            "title": "🚩 Pair with Kev on auth flow",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now + timedelta(days=1, hours=2)),
            "dueDate": _iso(now + timedelta(days=1, hours=2, minutes=30)),
        },
        # --- Inbox (no project, no priority, no due) ---
        {
            "id": "t-inbox-1",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "🚩 Random idea: spreadsheet → SQL converter",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-2",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "🚩 Email Jeff re: Reframe acquisition brief",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-3",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "Quick note from coffee w/ Priya — intro to Apollo PE?",
            "priority": 0,
            "status": 0,
        },
        # --- Someday/Maybe ---
        {
            "id": "t-someday-1",
            "projectId": "69b6e5088f085ebce14b22d6",
            "title": "Learn Rust",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-someday-2",
            "projectId": "69b6e5088f085ebce14b22d6",
            "title": "AZ raw land thesis writeup",
            "priority": 0,
            "status": 0,
        },
    ]
    return tasks


class MockClient:
    """Drop-in replacement for `TickTickClient` for dashboard --mock mode."""

    def __init__(self):
        self._projects: List[Dict] = _seed_projects()
        self._tasks: Dict[str, Dict] = {t["id"]: deepcopy(t) for t in _seed_tasks()}

    # --- read ---
    def get_projects(self) -> List[Dict]:
        return deepcopy(self._projects)

    def get_project(self, project_id: str) -> Dict:
        for p in self._projects:
            if p["id"] == project_id:
                return deepcopy(p)
        return {"error": "project not found"}

    def get_project_with_data(self, project_id: str) -> Dict:
        proj = next((p for p in self._projects if p["id"] == project_id), None)
        if proj is None:
            return {"error": "project not found"}
        tasks = [
            deepcopy(t)
            for t in self._tasks.values()
            if t.get("projectId") == project_id and t.get("status") != 2
        ]
        return {"project": deepcopy(proj), "tasks": tasks, "columns": []}

    def get_task(self, project_id: str, task_id: str) -> Dict:
        t = self._tasks.get(task_id)
        if not t or t.get("projectId") != project_id:
            return {"error": "task not found"}
        return deepcopy(t)

    # --- write ---
    def create_task(self, title, project_id, content=None, start_date=None,
                    due_date=None, priority=0, is_all_day=False) -> Dict:
        tid = "t-" + uuid.uuid4().hex[:10]
        t = {
            "id": tid,
            "projectId": project_id,
            "title": title,
            "priority": priority,
            "status": 0,
        }
        if content is not None:
            t["content"] = content
        if start_date is not None:
            t["startDate"] = start_date
        if due_date is not None:
            t["dueDate"] = due_date
        if is_all_day:
            t["isAllDay"] = True
        self._tasks[tid] = t
        return deepcopy(t)

    def update_task(self, task_id, project_id, title=None, content=None,
                    priority=None, start_date=None, due_date=None) -> Dict:
        t = self._tasks.get(task_id)
        if not t:
            return {"error": "task not found"}
        if title is not None:
            t["title"] = title
        if content is not None:
            t["content"] = content
        if priority is not None:
            t["priority"] = priority
        if start_date is not None:
            t["startDate"] = start_date
        if due_date is not None:
            t["dueDate"] = due_date
        if project_id and project_id != t.get("projectId"):
            t["projectId"] = project_id
        return deepcopy(t)

    def complete_task(self, project_id: str, task_id: str) -> Dict:
        t = self._tasks.get(task_id)
        if not t:
            return {"error": "task not found"}
        t["status"] = 2
        return {}

    def delete_task(self, project_id: str, task_id: str) -> Dict:
        self._tasks.pop(task_id, None)
        return {}

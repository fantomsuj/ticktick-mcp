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
        {"id": "6695fb3aab509194d7492975", "name": "Routines", "color": "#1ABC9C", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "669c9bd88f088125da4c32bf", "name": "Reading", "color": "#8E44AD", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "66c2a448151bd14d76dab830", "name": "Real Estate", "color": "#D35400", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "66f6b22b8e53512eb9cf07a0", "name": "Public Markets", "color": "#34495E", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "684db6c3227ed1033cf0fd47", "name": "Energy", "color": "#C0392B", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "672e789064be5181d618b716", "name": "Venture Capital", "color": "#2980B9", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "66884177becd911b75279a94", "name": "Chaumet Office", "color": "#7D3C98", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "69239f54252c91f8c0a68ad4", "name": "Admin & Errands", "color": "#95A5A6", "viewMode": "list", "closed": False, "kind": "TASK"},
        {"id": "69239fd13854d1f8c0a69082", "name": "Relationships & Social", "color": "#E91E63", "viewMode": "list", "closed": False, "kind": "TASK"},
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
        {
            "id": "t-overdue-6",
            "projectId": "6757d67c8f0808587783ab86",
            "title": "🚩 Schedule intro call with Founders Fund associate",
            "content": "Warm intro from Priya — they're looking at infra plays.",
            "priority": 5,
            "status": 0,
            "startDate": _iso(now - timedelta(days=1, hours=8)),
            "dueDate": _iso(now - timedelta(days=1, hours=8)),
        },
        {
            "id": "t-overdue-7",
            "projectId": "6851d328bc6ad1525900e1df",
            "title": "🚩 Refactor auth middleware to share token between web + extension",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=3)),
            "dueDate": _iso(now - timedelta(days=3)),
        },
        {
            "id": "t-overdue-8",
            "projectId": "69239e3c064f51f8c0a66b2f",
            "title": "Write up competitive landscape memo (Linear/Asana/Cursor)",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=14)),
            "dueDate": _iso(now - timedelta(days=14)),
        },
        {
            "id": "t-overdue-9",
            "projectId": "69239f54252c91f8c0a68ad4",
            "title": "🚩 Renew passport — appointment booking",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=21)),
            "dueDate": _iso(now - timedelta(days=21)),
        },
        {
            "id": "t-overdue-10",
            "projectId": "699c8a3c8f088b3b190a1ba1",
            "title": "🚩 Q2 ops dashboard owner review",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=2)),
            "dueDate": _iso(now - timedelta(days=2)),
        },
        {
            "id": "t-overdue-11",
            "projectId": "6925de124d1951f8c0a709b0",
            "title": "Skim Anthropic Constitutional AI followup paper",
            "priority": 1,
            "status": 0,
            "startDate": _iso(now - timedelta(days=6)),
            "dueDate": _iso(now - timedelta(days=6)),
        },
        {
            "id": "t-overdue-12",
            "projectId": "699c8a338f088b3b190a1a5d",
            "title": "WAITING: Sandeep's intro to GM at MegaCorp",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=9)),
            "dueDate": _iso(now - timedelta(days=9)),
        },
        {
            "id": "t-overdue-13",
            "projectId": "693a3b6a34db910305e570fc",
            "title": "🚩 File GitHub issue: dashboard cache stampede on cold start",
            "priority": 1,
            "status": 0,
            "startDate": _iso(now - timedelta(days=8)),
            "dueDate": _iso(now - timedelta(days=8)),
        },
        {
            "id": "t-overdue-14",
            "projectId": "6828b39ea96b91032980817c",
            "title": "Finish Berkeley CS61B problem set 4",
            "priority": 1,
            "status": 0,
            "startDate": _iso(now - timedelta(days=18)),
            "dueDate": _iso(now - timedelta(days=18)),
        },
        {
            "id": "t-overdue-15",
            "projectId": "66884177becd911b75279a94",
            "title": "🚩 Pull together Chaumet philanthropy options memo",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now - timedelta(days=5)),
            "dueDate": _iso(now - timedelta(days=5)),
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
        {
            "id": "t-today-5",
            "projectId": "6695fb3aab509194d7492975",
            "title": "🚩 Daily email triage (15 min)",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now_local.replace(hour=8, minute=30, second=0, microsecond=0)),
            "dueDate": _iso(now_local.replace(hour=8, minute=45, second=0, microsecond=0)),
        },
        {
            "id": "t-today-6",
            "projectId": "693a3b6a34db910305e570fc",
            "title": "🚩 Ship triage dashboard PR for review",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now_local.replace(hour=16, minute=0, second=0, microsecond=0)),
            "dueDate": _iso(now_local.replace(hour=16, minute=30, second=0, microsecond=0)),
        },
        {
            "id": "t-today-7",
            "projectId": "69239fd13854d1f8c0a69082",
            "title": "Call Mom",
            "priority": 1,
            "status": 0,
            "startDate": _iso(now_local.replace(hour=18, minute=0, second=0, microsecond=0)),
            "dueDate": _iso(now_local.replace(hour=18, minute=30, second=0, microsecond=0)),
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
        {
            "id": "t-tomorrow-3",
            "projectId": "699c8a338f088b3b190a1a5d",
            "title": "🚩 Prep BR commercial pipeline review deck",
            "content": "Pull last 30 days of Hubspot stage transitions; flag stuck deals.",
            "priority": 3,
            "status": 0,
            "startDate": _iso((now_local + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)),
            "dueDate": _iso((now_local + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)),
        },
        {
            "id": "t-tomorrow-4",
            "projectId": "6988fcb958ca9155b99ecc3f",
            "title": "🚩 BSIF cohort office hours w/ Tom",
            "priority": 3,
            "status": 0,
            "startDate": _iso((now_local + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)),
            "dueDate": _iso((now_local + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)),
        },
        # --- Later this week (won't show in Today/Tomorrow but exists in API) ---
        {
            "id": "t-week-1",
            "projectId": "6851d328bc6ad1525900e1df",
            "title": "🚩 v0.5 design review with team",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now + timedelta(days=3)),
            "dueDate": _iso(now + timedelta(days=3)),
        },
        {
            "id": "t-week-2",
            "projectId": "6757d67c8f0808587783ab86",
            "title": "🚩 Send LP update email to investor list",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now + timedelta(days=4)),
            "dueDate": _iso(now + timedelta(days=4)),
        },
        {
            "id": "t-week-3",
            "projectId": "669c9bd88f088125da4c32bf",
            "title": "Finish chapter 7 of \"Working Backwards\"",
            "priority": 1,
            "status": 0,
            "startDate": _iso(now + timedelta(days=5)),
            "dueDate": _iso(now + timedelta(days=5)),
        },
        {
            "id": "t-week-4",
            "projectId": "672e789064be5181d618b716",
            "title": "🚩 Coffee with DRF associate",
            "priority": 3,
            "status": 0,
            "startDate": _iso(now + timedelta(days=6)),
            "dueDate": _iso(now + timedelta(days=6)),
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
        {
            "id": "t-inbox-4",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "🚩 Look into Modal Labs for hosted Hex pipelines",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-5",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "🚩 Dentist appointment — book follow-up",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-6",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "🚩 Send thank-you to Kara for the BSIF intro",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-7",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "Idea: GTD-style daily review template inside Notion",
            "content": "Could pair with this dashboard — markdown export of the day.",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-8",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "🚩 Voice memo: thoughts on Reframe go-to-market",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-9",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "Read: \"How Continuous Generation Won\" (a16z)",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-inbox-10",
            "projectId": "699a5943b1bed115b35b1e10",
            "title": "🚩 Renewing AWS reserved instance",
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
        {
            "id": "t-someday-3",
            "projectId": "69b6e5088f085ebce14b22d6",
            "title": "Build a personal CRM out of Linear + Apple Contacts",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-someday-4",
            "projectId": "69b6e5088f085ebce14b22d6",
            "title": "Take a glassblowing class",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-someday-5",
            "projectId": "69b6e5088f085ebce14b22d6",
            "title": "Write up: the case against most productivity apps",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-someday-6",
            "projectId": "69b6e5088f085ebce14b22d6",
            "title": "Run a half-marathon (Q4 target)",
            "priority": 0,
            "status": 0,
        },
        # --- Background / no-due active items so other projects aren't empty ---
        {
            "id": "t-bg-1",
            "projectId": "6695fb3aab509194d7492975",
            "title": "🚩 Weekly review (Sunday 5pm)",
            "priority": 1,
            "status": 0,
        },
        {
            "id": "t-bg-2",
            "projectId": "669c9bd88f088125da4c32bf",
            "title": "Queue: \"The Hard Thing About Hard Things\"",
            "priority": 0,
            "status": 0,
        },
        {
            "id": "t-bg-3",
            "projectId": "66c2a448151bd14d76dab830",
            "title": "🚩 Re-read AZ STR market notes; sketch one-pager",
            "priority": 1,
            "status": 0,
        },
        {
            "id": "t-bg-4",
            "projectId": "66f6b22b8e53512eb9cf07a0",
            "title": "🚩 Watch Q1 earnings calls: NVDA, MSFT, GOOGL",
            "priority": 1,
            "status": 0,
        },
        {
            "id": "t-bg-5",
            "projectId": "684db6c3227ed1033cf0fd47",
            "title": "🚩 Skim Fuse internship prep packet",
            "priority": 1,
            "status": 0,
        },
        {
            "id": "t-bg-6",
            "projectId": "69239f54252c91f8c0a68ad4",
            "title": "🚩 Drop off dry cleaning",
            "priority": 1,
            "status": 0,
        },
        {
            "id": "t-bg-7",
            "projectId": "66884177becd911b75279a94",
            "title": "🚩 Update Chaumet credit-building tracker",
            "priority": 1,
            "status": 0,
        },
    ]
    return tasks


def seed_mock_events(event_log) -> None:
    """Pre-seed the in-memory event log so --mock shows 'Completed today'."""
    samples = [
        ("complete", "old-completed-1", "699c8a338f088b3b190a1a5d",
         "🚩 Reply to Acme MSA email", "BR Commercial & BD"),
        ("complete", "old-completed-2", "6695fb3aab509194d7492975",
         "🚩 Daily email triage (15 min)", "Routines"),
        ("complete", "old-completed-3", "6925de124d1951f8c0a709b0",
         "🚩 Skim morning AI papers digest", "AI Research"),
        ("reschedule", "t-overdue-2", "693a3b6a34db910305e570fc",
         "🚩 Push v0.4 release notes", "Tools"),
        ("set_priority", "t-today-3", "699c8a3c8f088b3b190a1ba1",
         "🚩 Triage ops Slack backlog", "BR Ops & Intelligence"),
    ]
    for action, tid, pid, title, proj in samples:
        event_log.record(
            action,
            {"id": tid, "projectId": pid, "title": title},
            project_name=proj,
        )


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

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from ticktick_companion.dashboard.app import (
    INBOX_PROJECT_ID,
    PANELS,
    TickTickStore,
    create_app,
    home_data,
    project_lookup,
    project_pressure_data,
    recommended_actions,
    recommended_triage_decision,
    score_task_for_triage,
    sort_for_recovery,
    task_view,
    ticktick_task_url,
    today_capacity,
    today_local,
    parse_iso_dt,
    user_tz,
)
from ticktick_companion.api.oauth import TickTickAuth
from ticktick_companion.api.client import TickTickClient
from ticktick_companion.api.token_store import EnvFileTokenStore, TokenStore
from ticktick_companion.dashboard.event_log import EventLog
from ticktick_companion.dashboard.mock_data import MockClient


class FakeTokenStore(TokenStore):
    def __init__(self, tokens=None):
        self.tokens = dict(tokens or {})
        self.saved = []

    def load_tokens(self):
        return dict(self.tokens)

    def save_tokens(self, tokens):
        self.saved.append(dict(tokens))
        if tokens.get("access_token"):
            self.tokens["TICKTICK_ACCESS_TOKEN"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            self.tokens["TICKTICK_REFRESH_TOKEN"] = tokens["refresh_token"]


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text="{}"):
        self.payload = payload or {}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return dict(self.payload)


class CountingClient:
    def __init__(self, fail_project_id=None):
        self.fail_project_id = fail_project_id
        self.project_data_calls = []
        self.projects = [
            {"id": "p1", "name": "Alpha", "closed": False},
            {"id": "p2", "name": "Beta", "closed": False},
            {"id": "p3", "name": "Closed", "closed": True},
        ]

    def get_projects(self):
        return list(self.projects)

    def get_project_with_data(self, project_id):
        self.project_data_calls.append(project_id)
        if project_id == self.fail_project_id:
            return {"error": "project failed"}
        return {
            "project": {"id": project_id},
            "tasks": [
                {
                    "id": f"t-{project_id}",
                    "projectId": project_id,
                    "title": f"Task {project_id}",
                    "status": 0,
                }
            ],
        }


class FailingWriteClient(MockClient):
    def complete_task(self, project_id, task_id):
        return {"error": "complete exploded"}


class DashboardEndpointTests(unittest.TestCase):
    def make_app(self, client=None):
        log = EventLog(":memory:")
        app = create_app(client or MockClient(), event_log=log)
        app.config["TESTING"] = True
        return app, log

    def test_index_renders_without_fetching_projects(self):
        client = CountingClient()
        app, _ = self.make_app(client)

        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'TickTick triage', response.data)
        self.assertIn(b"data-panel=\"home\"", response.data)
        self.assertNotIn(b"data-panel=\"focus\"", response.data)
        self.assertNotIn(b"data-panel=\"tomorrow\"", response.data)
        self.assertIn(b"hx-get=\"/panel/home\"", response.data)
        self.assertIn(b"command-bar", response.data)
        self.assertIn(b"drop-dock", response.data)
        self.assertIn(b"data-default-drag-target=\"today\"", response.data)
        self.assertEqual(client.project_data_calls, [])

    def test_htmx_panel_redirects_to_setup_when_ticktick_is_not_ready(self):
        log = EventLog(":memory:")
        app = create_app(None, event_log=log)
        app.config["TESTING"] = True
        http = app.test_client()

        response = http.get("/panel/today", headers={"HX-Request": "true"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["HX-Redirect"], "/")
        self.assertEqual(response.data, b"")

    def test_direct_panel_shows_setup_when_ticktick_is_not_ready(self):
        log = EventLog(":memory:")
        app = create_app(None, event_log=log)
        app.config["TESTING"] = True
        http = app.test_client()

        response = http.get("/panel/today")

        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Connect TickTick", response.data)

    def test_index_uses_local_assets_and_slow_counts_polling(self):
        app, _ = self.make_app()

        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/static/htmx.min.js", response.data)
        self.assertIn(b"every 5m", response.data)
        self.assertNotIn(b"every 60s", response.data)
        self.assertIn(b'id="badge-home"', response.data)
        self.assertIn(b'hx-swap="none"', response.data)
        self.assertIn(b"htmx.ajax('POST'", response.data)
        self.assertNotIn(b"fetch(", response.data)

    def test_counts_and_all_panels_render_with_mock_data(self):
        app, _ = self.make_app()
        http = app.test_client()

        counts = http.get("/counts")
        self.assertEqual(counts.status_code, 200)
        self.assertIn("Server-Timing", counts.headers)
        self.assertEqual(counts.headers["X-TickTick-Cache"], "fresh")
        self.assertIn(b'id="refresh-meta"', counts.data)
        self.assertIn(b'data-refresh-meta', counts.data)
        self.assertIn(b'hx-swap-oob="outerHTML"', counts.data)
        self.assertIn(b'id="badge-home"', counts.data)
        self.assertNotIn(b'id="badge-focus"', counts.data)
        self.assertNotIn(b'id="badge-tomorrow"', counts.data)

        for panel in PANELS:
            with self.subTest(panel=panel):
                response = http.get(f"/panel/{panel}")
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.data), 100)

    def test_home_panel_renders_cockpit_sections(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/home")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Server-Timing", response.headers)
        self.assertEqual(response.headers["X-TickTick-Cache"], "miss")
        self.assertIn(b"Attention", response.data)
        self.assertNotIn(b"kpi-strip", response.data)
        self.assertNotIn(b"Today Load", response.data)
        self.assertIn(b"Attention Queue", response.data)
        self.assertIn(b"Today", response.data)
        self.assertIn(b"Next Best Actions", response.data)
        self.assertIn(b"Project Pressure", response.data)
        self.assertIn(b"Momentum", response.data)
        self.assertIn(b'"source_panel":"home"', response.data)
        self.assertNotIn(b"hx-on::after-request", response.data)

    def test_today_panel_renders_timebox_timeline(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/today")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Today Timeline", response.data)
        self.assertIn(b"timebox-row", response.data)

    def test_end_of_day_contains_tomorrow_planning(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/eod")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tomorrow's lineup", response.data)
        self.assertIn(b"t-tomorrow", response.data)
        self.assertIn(b"Roll to Tomorrow", response.data)

    def test_rollover_today_moves_unfinished_tasks_to_tomorrow_keeping_times(self):
        client = MockClient()
        before_start = parse_iso_dt(client._tasks["t-today-2"]["startDate"]).astimezone(user_tz())
        before_due = parse_iso_dt(client._tasks["t-today-2"]["dueDate"]).astimezone(user_tz())
        app, log = self.make_app(client)

        response = app.test_client().post("/tasks/rollover-today")

        self.assertEqual(response.status_code, 200)
        self.assertIn("reloadPanel", response.headers["HX-Trigger"])
        self.assertIn("refreshCounts", response.headers["HX-Trigger"])
        after_start = parse_iso_dt(client._tasks["t-today-2"]["startDate"]).astimezone(user_tz())
        after_due = parse_iso_dt(client._tasks["t-today-2"]["dueDate"]).astimezone(user_tz())
        self.assertEqual(after_start.date(), today_local() + timedelta(days=1))
        self.assertEqual(after_due.date(), today_local() + timedelta(days=1))
        self.assertEqual((after_start.hour, after_start.minute), (before_start.hour, before_start.minute))
        self.assertEqual((after_due.hour, after_due.minute), (before_due.hour, before_due.minute))
        self.assertTrue(any(ev["details"]["kind"] == "rollover_today" for ev in log.recent()))

    def test_next_slot_action_schedules_task_in_tomorrow_morning_gap(self):
        client = MockClient()
        for task_id in list(client._tasks):
            if task_id.startswith("t-tomorrow-"):
                del client._tasks[task_id]
        app, log = self.make_app(client)

        response = app.test_client().post(
            "/task/693a3b6a34db910305e570fc/t-overdue-2/action",
            data={"action": "next_slot"},
        )

        self.assertEqual(response.status_code, 200)
        task = client._tasks["t-overdue-2"]
        start = parse_iso_dt(task["startDate"]).astimezone(user_tz())
        due = parse_iso_dt(task["dueDate"]).astimezone(user_tz())
        self.assertEqual(start.date(), today_local() + timedelta(days=1))
        self.assertEqual((start.hour, start.minute), (9, 0))
        self.assertEqual((due.hour, due.minute), (9, 30))
        self.assertTrue(any(ev["details"]["kind"] == "next_slot" for ev in log.recent()))

    def test_removed_panels_do_not_render(self):
        app, _ = self.make_app()
        http = app.test_client()

        self.assertEqual(http.get("/panel/focus").status_code, 404)
        self.assertEqual(http.get("/panel/tomorrow").status_code, 404)

    def test_recovery_panel_renders_reasons_and_decisions(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/triage")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Recovery Mode", response.data)
        self.assertIn(b"Today capacity", response.data)
        self.assertNotIn(b"Break Down", response.data)
        self.assertNotIn(b"diagnosis-", response.data)
        self.assertIn(b"Mark Waiting", response.data)

    def test_major_actions_succeed_with_mock_data(self):
        actions = [
            ("699c8a338f088b3b190a1a5d", "t-today-2", {"action": "complete"}),
            ("693a3b6a34db910305e570fc", "t-overdue-2", {"action": "today"}),
            ("693a3b6a34db910305e570fc", "t-overdue-13", {"action": "someday"}),
            ("699c8a338f088b3b190a1a5d", "t-today-4", {"action": "waiting"}),
            ("699c8a3c8f088b3b190a1ba1", "t-today-3", {"action": "set_priority", "priority": "1"}),
            (
                "699a5943b1bed115b35b1e10",
                "t-inbox-1",
                {
                    "action": "assign_inbox",
                    "project_id": "693a3b6a34db910305e570fc",
                    "priority": "3",
                    "due_date": "",
                },
            ),
        ]

        for project_id, task_id, form in actions:
            with self.subTest(action=form["action"]):
                app, _ = self.make_app()
                response = app.test_client().post(
                    f"/task/{project_id}/{task_id}/action",
                    data=form,
                )
                self.assertEqual(response.status_code, 200)

    def test_drag_action_succeeds_with_mock_data(self):
        app, _ = self.make_app()
        response = app.test_client().post(
            "/task/693a3b6a34db910305e570fc/t-overdue-2/drag",
            data={"target": "tomorrow"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertEqual(response.data, b"")
        self.assertIsNone(response.get_json(silent=True))
        self.assertIn("refreshCounts", response.headers["HX-Trigger"])
        self.assertIn("reloadPanel", response.headers["HX-Trigger"])

    def test_drag_action_error_returns_flash_fragment(self):
        app, _ = self.make_app()
        response = app.test_client().post(
            "/task/693a3b6a34db910305e570fc/t-overdue-2/drag",
            data={"target": "nowhere"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn(b"unknown drag target: nowhere", response.data)
        self.assertEqual(response.headers["HX-Retarget"], "#flash-region")
        self.assertEqual(response.headers["HX-Reswap"], "innerHTML")
        self.assertNotIn("HX-Trigger", response.headers)

    def test_write_failure_keeps_card_visible_and_does_not_log(self):
        app, log = self.make_app(FailingWriteClient())
        response = app.test_client().post(
            "/task/699c8a338f088b3b190a1a5d/t-today-2/action",
            data={"action": "complete"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"complete failed: complete exploded", response.data)
        self.assertIn(b"BR weekly standup notes", response.data)
        self.assertEqual(log.recent(), [])

    def test_today_capacity_guard_intercepts_overload(self):
        app, _ = self.make_app()
        response = app.test_client().post(
            "/task/693a3b6a34db910305e570fc/t-overdue-2/action",
            data={"action": "today"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Today is already full", response.data)
        self.assertEqual(response.headers["HX-Retarget"], "#modal-root")

    def test_capacity_override_records_reason_and_moves_task(self):
        app, log = self.make_app()
        response = app.test_client().post(
            "/task/693a3b6a34db910305e570fc/t-overdue-2/action",
            data={"action": "today", "force_capacity": "1", "reason": "Capacity override"},
        )

        self.assertEqual(response.status_code, 200)
        events = log.recent()
        self.assertEqual(events[0]["action"], "reschedule")
        self.assertEqual(events[0]["details"]["reason"], "Capacity override")
        self.assertIn("reloadPanel", response.headers["HX-Trigger"])

    def test_refresh_triggers_counts_and_panel_reload(self):
        app, _ = self.make_app()

        response = app.test_client().post("/refresh")

        self.assertEqual(response.status_code, 200)
        self.assertIn("refreshCounts", response.headers["HX-Trigger"])
        self.assertIn("reloadPanel", response.headers["HX-Trigger"])

    def test_home_sourced_task_action_triggers_panel_reload(self):
        app, _ = self.make_app()

        response = app.test_client().post(
            "/task/693a3b6a34db910305e570fc/t-overdue-13/action",
            data={"action": "someday", "source_panel": "home"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("refreshCounts", response.headers["HX-Trigger"])
        self.assertIn("reloadPanel", response.headers["HX-Trigger"])

    def test_large_someday_panel_uses_lazy_card_page(self):
        client = MockClient()
        for i in range(30):
            client._tasks[f"t-someday-extra-{i}"] = {
                "id": f"t-someday-extra-{i}",
                "projectId": "69b6e5088f085ebce14b22d6",
                "title": f"Someday extra {i:02d}",
                "priority": 0,
                "status": 0,
            }
        app, _ = self.make_app(client)

        response = app.test_client().get("/panel/someday")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Loading more", response.data)
        self.assertIn(b"/panel/someday/page?offset=25", response.data)

    def test_task_cards_render_ticktick_external_link(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/triage")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'href="https://ticktick.com/webapp/#p/69239e3c064f51f8c0a66b2f/tasks/t-overdue-8"',
            response.data,
        )
        self.assertIn(b'target="_blank"', response.data)
        self.assertIn(b'rel="noopener noreferrer"', response.data)
        self.assertIn(b"View task in TickTick", response.data)

    def test_inbox_cards_render_ticktick_external_link(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/inbox")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'href="https://ticktick.com/webapp/#p/699a5943b1bed115b35b1e10/tasks/t-inbox-1"',
            response.data,
        )
        self.assertIn(b'target="_blank"', response.data)

    def test_home_recommendations_render_ticktick_external_link(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/home")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'href="https://ticktick.com/webapp/#p/69239e3c064f51f8c0a66b2f/tasks/t-overdue-8"',
            response.data,
        )
        self.assertIn(b'target="_blank"', response.data)


class DashboardAuthTests(unittest.TestCase):
    def make_protected_app(self, client=None, token_store=None):
        log = EventLog(":memory:")
        with patch.dict(os.environ, {
            "TICKTICK_DASHBOARD_PASSWORD": "secret",
            "TICKTICK_DASHBOARD_SECRET_KEY": "test-secret-key",
        }, clear=False):
            app = create_app(client or MockClient(), event_log=log, token_store=token_store or FakeTokenStore())
        app.config["TESTING"] = True
        return app

    def test_unauthenticated_index_redirects_to_login(self):
        app = self.make_protected_app()

        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_correct_password_creates_session(self):
        app = self.make_protected_app()
        http = app.test_client()

        response = http.post("/login", data={"password": "secret"})

        self.assertEqual(response.status_code, 302)
        with http.session_transaction() as sess:
            self.assertTrue(sess["dashboard_authenticated"])

    def test_wrong_password_stays_on_login(self):
        app = self.make_protected_app()

        response = app.test_client().post("/login", data={"password": "wrong"})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Incorrect password", response.data)

    def test_htmx_request_redirects_to_login_header(self):
        app = self.make_protected_app()

        response = app.test_client().get("/panel/home", headers={"HX-Request": "true"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["HX-Redirect"], "/login")

    def test_logout_clears_session(self):
        app = self.make_protected_app()
        http = app.test_client()
        http.post("/login", data={"password": "secret"})

        response = http.get("/logout")

        self.assertEqual(response.status_code, 302)
        with http.session_transaction() as sess:
            self.assertNotIn("dashboard_authenticated", sess)


class TickTickOAuthRecoveryTests(unittest.TestCase):
    def test_missing_tokens_render_setup_screen(self):
        with patch.dict(os.environ, {
            "TICKTICK_CLIENT_ID": "client",
            "TICKTICK_CLIENT_SECRET": "secret",
        }, clear=False):
            app = create_app(None, event_log=EventLog(":memory:"), token_store=FakeTokenStore())
        app.config["TESTING"] = True

        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Connect TickTick", response.data)
        self.assertIn(b"Authorize TickTick", response.data)

    def test_vercel_setup_requires_durable_token_store_before_oauth(self):
        with patch.dict(os.environ, {
            "VERCEL": "1",
            "TICKTICK_CLIENT_ID": "client",
            "TICKTICK_CLIENT_SECRET": "secret",
        }, clear=True):
            app = create_app(None, event_log=EventLog(":memory:"), token_store=FakeTokenStore())
            app.config["TESTING"] = True
            http = app.test_client()
            setup = http.get("/")
            start = http.get("/auth/ticktick/start")

        self.assertEqual(setup.status_code, 200)
        self.assertIn(b"UPSTASH_REDIS_REST_URL", setup.data)
        self.assertIn(b"UPSTASH_REDIS_REST_TOKEN", setup.data)
        self.assertNotIn(b"Authorize TickTick", setup.data)
        self.assertEqual(start.status_code, 400)
        self.assertIn(b"UPSTASH_REDIS_REST_URL", start.data)

    def test_oauth_start_is_protected_by_dashboard_login(self):
        app = self.make_protected_oauth_app()

        response = app.test_client().get("/auth/ticktick/start")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def make_protected_oauth_app(self, token_store=None):
        with patch.dict(os.environ, {
            "TICKTICK_CLIENT_ID": "client",
            "TICKTICK_CLIENT_SECRET": "secret",
            "TICKTICK_DASHBOARD_PASSWORD": "secret",
            "TICKTICK_DASHBOARD_SECRET_KEY": "test-secret-key",
        }, clear=False):
            app = create_app(None, event_log=EventLog(":memory:"), token_store=token_store or FakeTokenStore())
        app.config["TESTING"] = True
        return app

    def test_oauth_callback_rejects_bad_state(self):
        app = self.make_protected_oauth_app()
        http = app.test_client()
        with http.session_transaction() as sess:
            sess["dashboard_authenticated"] = True
            sess["ticktick_oauth_state"] = "good"

        response = http.get("/auth/ticktick/callback?code=abc&state=bad")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"state did not match", response.data)

    def test_oauth_callback_rejects_missing_code(self):
        app = self.make_protected_oauth_app()
        http = app.test_client()
        with http.session_transaction() as sess:
            sess["dashboard_authenticated"] = True
            sess["ticktick_oauth_state"] = "good"

        response = http.get("/auth/ticktick/callback?state=good")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"authorization code", response.data)

    def test_successful_oauth_callback_saves_tokens(self):
        token_store = FakeTokenStore()
        app = self.make_protected_oauth_app(token_store)
        http = app.test_client()
        with http.session_transaction() as sess:
            sess["dashboard_authenticated"] = True
            sess["ticktick_oauth_state"] = "good"

        with patch("ticktick_companion.api.oauth.requests.post") as post:
            post.return_value = FakeResponse({
                "access_token": "new-access",
                "refresh_token": "new-refresh",
            })
            response = http.get("/auth/ticktick/callback?code=abc&state=good")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(token_store.tokens["TICKTICK_ACCESS_TOKEN"], "new-access")
        self.assertEqual(token_store.tokens["TICKTICK_REFRESH_TOKEN"], "new-refresh")

    def test_oauth_callback_token_store_failure_returns_setup_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_store = EnvFileTokenStore(env_path=tmp)
            app = self.make_protected_oauth_app(token_store)
            http = app.test_client()
            with http.session_transaction() as sess:
                sess["dashboard_authenticated"] = True
                sess["ticktick_oauth_state"] = "good"

            with patch("ticktick_companion.api.oauth.requests.post") as post:
                post.return_value = FakeResponse({
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                })
                response = http.get("/auth/ticktick/callback?code=abc&state=good")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Error exchanging code for token", response.data)
        self.assertIn(b"Could not read token env file", response.data)

    def test_invalid_grant_error_explains_redirect_uri_and_fresh_code(self):
        auth = TickTickAuth(
            client_id="client",
            client_secret="secret",
            redirect_uri="https://example.vercel.app/auth/ticktick/callback",
            token_store=FakeTokenStore(),
        )

        with patch("ticktick_companion.api.oauth.requests.post") as post:
            post.return_value = FakeResponse(
                {
                    "error": "invalid_grant",
                    "error_description": "Invalid authorization code: 7J4xaO",
                },
                status_code=400,
            )
            result = auth.exchange_authorization_code("7J4xaO")

        self.assertIn("Start authorization again", result)
        self.assertIn("can only be used once", result)
        self.assertIn("https://example.vercel.app/auth/ticktick/callback", result)
        self.assertNotIn("7J4xaO", result)

    @patch.dict(os.environ, {
        "TICKTICK_CLIENT_ID": "client",
        "TICKTICK_CLIENT_SECRET": "secret",
    }, clear=False)
    def test_ticktick_client_refresh_saves_tokens_to_store(self):
        token_store = FakeTokenStore({
            "TICKTICK_ACCESS_TOKEN": "old-access",
            "TICKTICK_REFRESH_TOKEN": "old-refresh",
        })
        client = TickTickClient(token_store=token_store)

        with patch.object(client._session, "post") as post:
            post.return_value = FakeResponse({
                "access_token": "fresh-access",
                "refresh_token": "fresh-refresh",
            })
            refreshed = client._refresh_access_token()

        self.assertTrue(refreshed)
        self.assertEqual(token_store.tokens["TICKTICK_ACCESS_TOKEN"], "fresh-access")
        self.assertEqual(token_store.tokens["TICKTICK_REFRESH_TOKEN"], "fresh-refresh")


class VercelDeploymentTests(unittest.TestCase):
    def test_vercel_wsgi_entrypoint_imports(self):
        with patch.dict(os.environ, {}, clear=False):
            import api.index as index

        self.assertTrue(hasattr(index, "app"))


class TickTickStoreTests(unittest.TestCase):
    def test_cold_refresh_fetches_all_open_projects(self):
        client = CountingClient()
        store = TickTickStore(client, ttl_seconds=60, max_workers=2)

        tasks = store.all_active_tasks()

        self.assertEqual({t["id"] for t in tasks}, {"t-p1", "t-p2"})
        self.assertEqual(set(client.project_data_calls), {"p1", "p2"})
        self.assertNotIn("p3", client.project_data_calls)

    def test_refresh_project_only_fetches_requested_project_after_warmup(self):
        client = CountingClient()
        store = TickTickStore(client, ttl_seconds=60, max_workers=2)
        store.all_active_tasks()
        client.project_data_calls.clear()

        store.refresh_project("p1")

        self.assertEqual(client.project_data_calls, ["p1"])

    def test_partial_project_failure_is_recorded_without_breaking_cache(self):
        client = CountingClient(fail_project_id="p2")
        store = TickTickStore(client, ttl_seconds=60, max_workers=2)

        tasks = store.all_active_tasks()

        self.assertEqual([t["id"] for t in tasks], ["t-p1"])
        self.assertEqual(store.project_errors, {"p2": "project failed"})

    def test_refresh_saves_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            store = TickTickStore(
                CountingClient(),
                ttl_seconds=60,
                max_workers=2,
                snapshot_path=snapshot,
            )

            store.all_active_tasks()

            payload = json.loads(snapshot.read_text())
            self.assertEqual(len(payload["projects"]), 3)
            self.assertEqual(set(payload["tasks_by_project"]), {"p1", "p2"})

    def test_snapshot_loads_as_stale_usable_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps({
                "version": 1,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "projects": [{"id": "p-saved", "name": "Saved", "closed": False}],
                "tasks_by_project": {
                    "p-saved": [{
                        "id": "t-saved",
                        "projectId": "p-saved",
                        "title": "Saved task",
                        "status": 0,
                    }]
                },
            }))

            class SlowClient(CountingClient):
                def get_projects(self):
                    time.sleep(0.1)
                    return super().get_projects()

            store = TickTickStore(
                SlowClient(),
                ttl_seconds=60,
                max_workers=2,
                snapshot_path=snapshot,
            )

            self.assertTrue(store.is_stale_snapshot)
            self.assertEqual(store.all_active_tasks()[0]["id"], "t-saved")

    def test_snapshot_with_mock_seed_tasks_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps({
                "version": 1,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "projects": [{"id": "p-saved", "name": "Saved", "closed": False}],
                "tasks_by_project": {
                    "p-saved": [{
                        "id": "t-overdue-1",
                        "projectId": "p-saved",
                        "title": "Seed task that must not leak",
                        "status": 0,
                    }]
                },
            }))

            store = TickTickStore(
                CountingClient(),
                ttl_seconds=60,
                max_workers=2,
                snapshot_path=snapshot,
            )

            self.assertFalse(store.is_stale_snapshot)
            self.assertFalse(snapshot.exists())
            self.assertEqual([t["id"] for t in store.all_active_tasks()], ["t-p1", "t-p2"])


class HomeViewModelTests(unittest.TestCase):
    def make_store(self, client=None):
        store = TickTickStore(client or MockClient(), ttl_seconds=60, max_workers=2)
        store.all_active_tasks()
        return store

    def test_recovery_recommendation_ranks_high_value_overdue_work(self):
        client = MockClient()
        client._tasks["t-overdue-6"]["priority"] = 3
        store = self.make_store(client)
        tasks = store.all_active_tasks()

        recs = recommended_actions(tasks, project_lookup(store))

        self.assertEqual(recs[0]["kind"], "overdue")
        self.assertEqual(recs[0]["task"]["id"], "t-overdue-8")
        self.assertLess(
            [r["kind"] for r in recs].index("overdue"),
            [r["kind"] for r in recs].index("inbox"),
        )

    def test_inbox_and_waiting_recommendations_include_counts(self):
        client = MockClient()
        client._tasks["t-overdue-6"]["priority"] = 3
        store = self.make_store(client)

        recs = recommended_actions(store.all_active_tasks(), project_lookup(store))
        by_kind = {r["kind"]: r for r in recs}

        self.assertIn("inbox", by_kind)
        self.assertIn("waiting", by_kind)
        self.assertIn("capture", by_kind["inbox"]["body"])
        self.assertIn("7+ days", by_kind["waiting"]["body"])

    def test_completed_today_count_reads_event_log(self):
        store = self.make_store()
        log = EventLog(":memory:")
        log.record(
            "complete",
            {"id": "done-1", "projectId": INBOX_PROJECT_ID, "title": "Done one"},
            project_name="Inbox",
        )

        data = home_data(store, log)

        self.assertEqual(data["momentum"]["completed_count"], 1)
        self.assertEqual(data["momentum"]["activity_stats"]["complete"], 1)

    def test_home_data_includes_project_pressure(self):
        client = MockClient()
        client._tasks["t-overdue-6"]["priority"] = 3
        store = self.make_store(client)

        data = home_data(store, EventLog(":memory:"))

        self.assertNotIn("metrics", data)
        self.assertGreater(len(data["project_pressure"]), 0)
        self.assertIn("bar_percent", data["project_pressure"][0])

    def test_project_pressure_orders_by_overdue_then_today(self):
        store = self.make_store()
        projects = project_lookup(store)

        rows = project_pressure_data(store.all_active_tasks(), projects)

        self.assertGreaterEqual(rows[0]["overdue_count"], rows[1]["overdue_count"])
        self.assertTrue(all(8 <= row["bar_percent"] <= 100 for row in rows))


class TaskLinkViewModelTests(unittest.TestCase):
    @patch.dict(os.environ, {"TICKTICK_BASE_URL": ""})
    def test_ticktick_task_url_defaults_to_ticktick_webapp(self):
        self.assertEqual(
            ticktick_task_url("project id", "task/id"),
            "https://ticktick.com/webapp/#p/project%20id/tasks/task%2Fid",
        )

    @patch.dict(os.environ, {"TICKTICK_BASE_URL": "https://api.dida365.com/open/v1"})
    def test_ticktick_task_url_uses_dida365_for_dida365_api_base(self):
        self.assertEqual(
            ticktick_task_url("p1", "t1"),
            "https://dida365.com/webapp/#p/p1/tasks/t1",
        )

    @patch.dict(os.environ, {"TICKTICK_BASE_URL": ""})
    def test_task_view_includes_ticktick_url(self):
        task = {"id": "t1", "projectId": "p1", "title": "Task", "status": 0}

        view = task_view(task, {"p1": {"name": "Project"}})

        self.assertEqual(
            view["ticktick_url"],
            "https://ticktick.com/webapp/#p/p1/tasks/t1",
        )


class RecoveryViewModelTests(unittest.TestCase):
    def make_store(self, client=None):
        store = TickTickStore(client or MockClient(), ttl_seconds=60, max_workers=2)
        store.all_active_tasks()
        return store

    def test_triage_score_prefers_core_focus_over_low_priority_admin(self):
        client = MockClient()
        client._tasks["t-overdue-6"]["priority"] = 3
        store = self.make_store(client)
        projects = project_lookup(store)
        tasks = {t["id"]: t for t in store.all_active_tasks()}

        extensible_score, extensible_reasons = score_task_for_triage(
            tasks["t-overdue-8"], projects
        )
        admin_score, _ = score_task_for_triage(tasks["t-overdue-9"], projects)

        self.assertGreater(extensible_score, admin_score)
        self.assertIn("Extensible focus area", extensible_reasons)

    def test_repeated_reschedules_make_task_stuck(self):
        store = self.make_store()
        projects = project_lookup(store)
        task = next(t for t in store.all_active_tasks() if t["id"] == "t-overdue-2")
        log = EventLog(":memory:")
        for _ in range(3):
            log.record("reschedule", task)

        view = task_view(task, projects, log)

        self.assertEqual(log.reschedule_count("t-overdue-2"), 3)
        self.assertTrue(view["is_stuck"])
        self.assertEqual(view["recommended_decision"], "Break down")

    def test_sort_for_recovery_uses_score_not_only_age(self):
        client = MockClient()
        client._tasks["t-overdue-6"]["priority"] = 3
        store = self.make_store(client)
        projects = project_lookup(store)
        overdue = [t for t in store.all_active_tasks() if t["id"] in ("t-overdue-8", "t-overdue-9")]

        ordered = sort_for_recovery(overdue, projects)

        self.assertEqual(ordered[0]["id"], "t-overdue-8")

    def test_recommended_triage_decision_for_old_low_priority_task(self):
        store = self.make_store()
        projects = project_lookup(store)
        task = next(t for t in store.all_active_tasks() if t["id"] == "t-overdue-14")

        self.assertEqual(recommended_triage_decision(task, projects), "Park or Drop")

    def test_today_capacity_reports_overload(self):
        store = self.make_store()
        capacity = today_capacity(store.all_active_tasks(), project_lookup(store))

        self.assertGreaterEqual(capacity["count"], capacity["capacity"])
        self.assertTrue(capacity["is_over_capacity"])


if __name__ == "__main__":
    unittest.main()

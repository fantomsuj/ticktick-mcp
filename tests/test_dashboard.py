import unittest

from ticktick_companion.dashboard.app import (
    INBOX_PROJECT_ID,
    PANELS,
    TickTickStore,
    create_app,
    home_data,
    project_lookup,
    recommended_actions,
    today_commitment,
)
from ticktick_companion.dashboard.event_log import EventLog
from ticktick_companion.dashboard.mock_data import MockClient


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
        self.assertIn(b"hx-get=\"/panel/home\"", response.data)
        self.assertEqual(client.project_data_calls, [])

    def test_counts_and_all_panels_render_with_mock_data(self):
        app, _ = self.make_app()
        http = app.test_client()

        counts = http.get("/counts")
        self.assertEqual(counts.status_code, 200)
        self.assertIn(b"data-refresh-meta", counts.data)
        self.assertIn(b'data-tab-count="home"', counts.data)

        for panel in PANELS:
            with self.subTest(panel=panel):
                response = http.get(f"/panel/{panel}")
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.data), 100)

    def test_home_panel_renders_cockpit_sections(self):
        app, _ = self.make_app()

        response = app.test_client().get("/panel/home")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Attention Queue", response.data)
        self.assertIn(b"Today Commitment", response.data)
        self.assertIn(b"Next Best Actions", response.data)
        self.assertIn(b"Momentum", response.data)

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
        self.assertEqual(response.get_json()["ok"], True)

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


class HomeViewModelTests(unittest.TestCase):
    def make_store(self, client=None):
        store = TickTickStore(client or MockClient(), ttl_seconds=60, max_workers=2)
        store.all_active_tasks()
        return store

    def test_missing_highlight_produces_pick_highlight_recommendation(self):
        client = MockClient()
        for task in client._tasks.values():
            if task.get("priority") == 5:
                task["priority"] = 3
                task["title"] = task.get("title", "").removeprefix("⭐ ")
        store = self.make_store(client)
        tasks = store.all_active_tasks()

        recs = recommended_actions(tasks, project_lookup(store))

        self.assertEqual(recs[0]["kind"], "missing_highlight")
        self.assertEqual(recs[0]["primary_action"]["action"], "highlight")

    def test_existing_highlight_today_commitment_uses_two_big_things(self):
        client = MockClient()
        # Keep a single Highlight, due today, so the commitment model is stable.
        client._tasks["t-overdue-6"]["priority"] = 3
        store = self.make_store(client)
        commitment = today_commitment(store.all_active_tasks(), project_lookup(store))

        self.assertEqual(commitment["highlight"]["id"], "t-today-1")
        self.assertEqual(len(commitment["big_things"]), 2)
        self.assertGreaterEqual(commitment["tail_count"], 1)

    def test_stale_overdue_recommendation_ranks_before_lighter_work(self):
        client = MockClient()
        client._tasks["t-overdue-6"]["priority"] = 3
        store = self.make_store(client)
        tasks = store.all_active_tasks()

        recs = recommended_actions(tasks, project_lookup(store))

        self.assertEqual(recs[0]["kind"], "overdue")
        self.assertEqual(recs[0]["task"]["id"], "t-overdue-9")
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


if __name__ == "__main__":
    unittest.main()

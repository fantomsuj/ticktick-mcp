# Claude Context for TickTick Companion

This file gives AI assistants (Claude Code, MCP clients, etc.) what they need
to (1) make sound code changes to the TickTick Companion codebase, and (2)
operate the user's TickTick workflow consistently with the dashboard.

See `AGENT.md` for scheduling rules. See `README.md` for end-user setup.

---

## Part 1 — Codebase

### What this project is

TickTick Companion is a local productivity layer over TickTick's Open API. It
ships three surfaces from one Python package:

1. **Dashboard** (Flask + HTMX, port `8765`) — the primary daily triage UI.
2. **MCP server** (stdio) — exposes TickTick tools to Claude Desktop and other
   MCP clients.
3. **CLI** (`ticktick-companion`) — entry point for `auth`, `dashboard`, `run`.

All three share the same OAuth tokens (in `.env`) and the same
`TickTickClient`. Dashboard writes go through the same client the MCP tools
use, so the two surfaces stay in sync.

### Layout

```text
ticktick-mcp/                       # repo root (kept name for back-compat)
├── README.md                       # end-user docs
├── AGENT.md                        # scheduling rules (start = due, 30-min default)
├── CLAUDE.md                       # this file
├── ticktick-openapi.md             # bundled TickTick Open API reference
├── setup.py                        # packaging + console_scripts
├── requirements.txt
├── .env.template                   # required env vars
├── test_server.py                  # live-API smoke test (uses real creds)
├── tests/
│   └── test_dashboard.py           # unittest suite (uses MockClient)
├── ticktick_companion/             # ← canonical package; all code lives here
│   ├── cli.py                      # argparse CLI; routes to auth/dashboard/MCP
│   ├── api/
│   │   ├── client.py               # TickTickClient (HTTP, retry, token refresh)
│   │   └── oauth.py                # OAuth flow + interactive auth command
│   ├── dashboard/
│   │   ├── app.py                  # Flask app, view models, action handlers
│   │   ├── event_log.py            # append-only SQLite action log
│   │   ├── mock_data.py            # MockClient for `--mock` demo mode
│   │   ├── templates/              # Jinja partials, HTMX-driven
│   │   └── static/dashboard.css
│   └── mcp/
│       └── server.py               # FastMCP tools wrapping TickTickClient
└── ticktick_mcp/                   # ← thin back-compat wrappers; do not add logic
    ├── cli.py, authenticate.py, dashboard.py
```

`ticktick_mcp/` exists only because earlier integrations and Claude Desktop
configs reference the old package name. New code goes in `ticktick_companion/`.
Each shim re-exports `main` from the new module:

```python
# ticktick_mcp/cli.py
from ticktick_companion.cli import main
```

### Console scripts (`setup.py`)

| Command | Target |
|---|---|
| `ticktick-companion` | `ticktick_companion.cli:main` (canonical) |
| `ticktick-companion-dashboard` | `ticktick_companion.dashboard.app:main` |
| `ticktick-auth` | `ticktick_companion.api.oauth:main` |
| `ticktick-mcp` | back-compat alias → `ticktick_companion.cli:main` |
| `ticktick-dashboard` | back-compat alias → dashboard `main` |

### How requests reach TickTick

```
caller (CLI / dashboard / MCP tool)
        │
        ▼
TickTickClient (api/client.py)
  • _send_request(method, url, data)        # raw HTTP
  • _make_request(method, endpoint, data)   # adds 401 refresh + GET retry
        │
        ▼
TickTick Open API (https://api.ticktick.com/open/v1)
```

`TickTickClient._make_request` handles:
- **Auth refresh on 401** — calls `_refresh_access_token()`, persists new
  tokens to `.env`, retries the original request once.
- **Transient retry on GET** — for `429/500/502/503/504`, retries up to twice
  with `0.25s`/`0.75s` backoff.
- **Errors** — returns `{"error": str(e)}` on `RequestException`. Callers must
  check `if 'error' in result:` and surface the message; never raise.

The base/auth URLs are env-configurable so the same code works against
[Dida365](https://dida365.com) (`TICKTICK_BASE_URL`, `TICKTICK_AUTH_URL`,
`TICKTICK_TOKEN_URL`).

### Dashboard architecture (`ticktick_companion/dashboard/app.py`)

This is the largest module. It has four layers — keep new code in the right one.

1. **`TickTickStore`** — read-through cache wrapping `TickTickClient`. The
   public TickTick API has no bulk-tasks endpoint, so the store walks every
   non-closed project and caches the result. Default TTL `60s`, default 6
   parallel workers (`TICKTICK_DASHBOARD_CACHE_TTL_SECONDS`,
   `TICKTICK_DASHBOARD_FETCH_WORKERS`). After mutations call
   `store.refresh_project(pid)` or `store.refresh_projects([...])` — never
   `refresh_all()` for a single-task change.
2. **Date / task helpers** — `parse_iso_dt`, `task_due`, `is_due_today`,
   `is_overdue`, `days_overdue`, `fmt_due`, `to_api_iso`, `workday_morning`.
   All TZ-aware; user TZ comes from `TICKTICK_TIMEZONE`
   (default `America/Los_Angeles`).
3. **View-model builders** — `task_view`, `panel_data`, `home_data`,
   `focus_groups`, `recommended_actions`, `dashboard_health`,
   `today_commitment`, sort helpers.
4. **Action handlers** — `reschedule_to_date`, `move_to_someday`,
   `mark_waiting`, `set_priority`, `promote_to_highlight`,
   `assign_inbox_task`. Each returns a TickTick API result and raises
   `ActionError` on failure. Routes in `create_app(...)` translate HTMX form
   posts into these handlers and log every successful mutation to `EventLog`.

Important constants at the top of `app.py`:

- `INBOX_PROJECT_ID = "699a5943b1bed115b35b1e10"`
- `SOMEDAY_PROJECT_ID = "69b6e5088f085ebce14b22d6"`
- `WAITING_HOME_PROJECT_ID = "699c8a338f088b3b190a1a5d"` (BR Commercial & BD)
- `HIGHLIGHT_PREFIX = "⭐ "`, `WAITING_PREFIX = "WAITING:"`,
  `CLAUDE_PREFIX = "🚩 "`
- `PROJECT_FAMILIES`, `FAMILY_ORDER` — Focus board grouping.
- `BUCKET_LABELS`, `BUCKET_ORDER` — `overdue / today / tomorrow / week /
  unscheduled / later / waiting / inbox / someday`.
- `PANELS = ["home", "focus", "triage", "today", "tomorrow", "inbox",
  "waiting", "someday", "eod"]`

Dashboard routes (HTMX):

| Route | Purpose |
|---|---|
| `GET /` | Renders the shell only — no API calls until a panel loads |
| `GET /panel/<name>` | Renders one of the `PANELS` partials |
| `GET /counts` | Returns sidebar count badges + cache status |
| `POST /task/<pid>/<tid>/action` | Form-driven action dispatch (`_dispatch_action`) |
| `POST /task/<pid>/<tid>/drag` | Drag-and-drop reschedule (`today/tomorrow/week/someday/waiting`) |
| `POST /refresh` | Force `store.refresh_all()` |
| `GET /health` | `{"ok": true}` |

### Activity log (`event_log.py`)

`EventLog` is an append-only SQLite table at `~/.ticktick-dashboard.db` (or
`":memory:"` in tests / `--mock` mode). One row per dashboard mutation:

```sql
events(id, ts, ts_local_date, action, task_id, project_id, task_title,
       project_name, priority_before, priority_after, due_before, due_after,
       details_json)
```

`action` values currently emitted: `complete`, `delete`, `reschedule`,
`mark_waiting`, `set_priority`, `highlight`, `unhighlight`, `assign_inbox`.
The End of Day panel uses `completed_on(date)` and `stats_on(date)`.

When adding a new mutation route, **always log it** with `log.record(...)` so
End of Day and momentum stay accurate.

### MCP server (`ticktick_companion/mcp/server.py`)

Built on `FastMCP("ticktick")`. Each tool is an `@mcp.tool()` async function
that:

1. Lazy-initializes the client via `initialize_client()`.
2. Calls a `TickTickClient` method.
3. Checks `if 'error' in result:` and returns a string error message; never
   raises.
4. Formats the result with `format_task` / `format_project` for human
   consumption.

`PRIORITY_MAP = {0: "None", 1: "Low", 3: "Medium", 5: "High"}` — note the gap;
`2` and `4` are not valid TickTick priorities. All tools that accept
`priority` validate against `[0, 1, 3, 5]`.

Filter helpers (`_is_task_due_today`, `_is_task_overdue`,
`_is_task_due_in_days`, `_task_matches_search`) parse `dueDate` with
`%Y-%m-%dT%H:%M:%S.%f%z` against UTC. Note this differs slightly from the
dashboard's TZ-aware comparisons — when extending these tools, prefer the
dashboard helpers in `dashboard/app.py` for consistency with the user's local
day.

`_get_project_tasks_by_filter(projects, filter_func, name)` is the shared
listing format: it walks open projects, applies a predicate, and renders the
result. Reuse it when adding a new "list tasks where X" tool rather than
rolling new formatting.

When adding a new MCP tool: register `@mcp.tool()` in `server.py`, add it to
the README's MCP Tools table, and verify `priority` validation if the tool
takes one.

### OAuth flow (`api/oauth.py`)

`TickTickAuth.start_auth_flow()`:

1. Generates a CSRF `state` and the authorize URL.
2. Opens the browser with `webbrowser.open(...)`.
3. Spins up `OAuthCallbackServer` on `localhost:8000` (`/callback`).
4. Polls `OAuthCallbackHandler.auth_code` with a 5-minute timeout.
5. On success, exchanges the code for tokens via Basic Auth and writes them to
   `.env`.

`OAuthCallbackServer` sets `allow_reuse_address = True` so re-running auth on
the same port works immediately.

### Environment variables

Required (set by `auth` flow):

- `TICKTICK_CLIENT_ID`, `TICKTICK_CLIENT_SECRET` — from TickTick Developer
  Center.
- `TICKTICK_ACCESS_TOKEN`, `TICKTICK_REFRESH_TOKEN` — populated automatically.

Optional:

- `TICKTICK_BASE_URL`, `TICKTICK_AUTH_URL`, `TICKTICK_TOKEN_URL` — override for
  Dida365 or a different region.
- `TICKTICK_API_TIMEOUT_SECONDS` (default `10.0`).
- `TICKTICK_TIMEZONE` (default `America/Los_Angeles`) — local TZ for date math
  in the dashboard.
- `TICKTICK_DASHBOARD_CACHE_TTL_SECONDS` (default `60`).
- `TICKTICK_DASHBOARD_FETCH_WORKERS` (default `6`).

### Coding conventions

- **Python 3.10+**. Type hints on public functions; `Dict`/`List`/`Optional`
  from `typing` are used throughout — match the existing style.
- **Module docstrings** at the top of each module that needs context (see
  `dashboard/app.py`, `event_log.py`, `mock_data.py`).
- **Errors from `TickTickClient`** come back as `{"error": "..."}`, not
  exceptions. Always check before unwrapping.
- **Date strings** to TickTick are ISO with `+0000` zone:
  `YYYY-MM-DDThh:mm:ss+0000`. Use `to_api_iso(dt)` from `dashboard/app.py`
  rather than hand-formatting.
- **Priorities** are `0` (None), `1` (Low), `3` (Medium), `5` (High). `2` and
  `4` are not valid.
- **Title prefixes** are load-bearing — they are how the system identifies
  Highlights, Waiting tasks, and Claude-created tasks. Never strip or add
  blindly:
  - `⭐ ` prefix ⇔ `priority == 5` (Highlight). Toggle them together via
    `set_priority` / `promote_to_highlight`.
  - `WAITING: ` prefix ⇔ task lives in `WAITING_HOME_PROJECT_ID`. Toggle via
    `mark_waiting`.
  - `🚩 ` prefix marks tasks Claude created on the user's behalf.

### Testing

- **Unit tests** (use `MockClient`, no network):

  ```bash
  python -m unittest tests.test_dashboard -v
  ```

  Tests cover `TickTickStore` cache behaviour, panel rendering for every
  `PANELS` entry, action endpoint round-trips, and view-model logic
  (`recommended_actions`, `today_commitment`, `home_data`).

- **Live smoke test** (hits real TickTick API with your tokens):

  ```bash
  uv run test_server.py             # read-only project listing
  uv run test_server.py --write     # also creates + deletes a test task/subtask
  ```

- **UI / mock mode** — exercises the full dashboard against seeded fake data:

  ```bash
  ticktick-companion dashboard --mock
  ```

  `MockClient` (`dashboard/mock_data.py`) implements the subset of
  `TickTickClient` the dashboard uses, with project IDs that mirror the user's
  real workspace so screenshots and behaviour are familiar.

When you change panel rendering, action handling, or any view-model helper:
run `python -m unittest tests.test_dashboard` and add a case to it. When you
change `TickTickClient` itself, also run `uv run test_server.py` if real
credentials are available.

### Setup for development

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv && source .venv/bin/activate
uv pip install -e .

# one-time:
ticktick-companion auth

# then any of:
ticktick-companion run                  # MCP server on stdio
ticktick-companion dashboard            # local triage UI on :8765
ticktick-companion dashboard --mock     # UI demo, no creds needed
```

### When changing things, watch for…

- **Both packages**: if you add a new entry-point function, update both
  `setup.py` `console_scripts` and any `ticktick_mcp/` shim that re-exports
  it.
- **Project IDs**: hard-coded throughout `dashboard/app.py`,
  `dashboard/mock_data.py`, and Part 2 of this file. If the user changes
  Inbox / Someday / Waiting home, update all three.
- **Cache invalidation**: any new mutation route must call `store.refresh_*`
  for the affected project(s) so the next panel render reflects the change.
- **Activity log**: any new mutation route must call `log.record(...)` so EOD
  / momentum stays correct.
- **Highlight invariant**: only one task should have `priority == 5` at a
  time. Use `promote_to_highlight` (which demotes the existing one) instead
  of setting `priority=5` directly.

---

## Part 2 — User Workflow

This part is the operational guide for assistants that **use** TickTick
Companion (via MCP or chat) on the user's behalf. The dashboard enforces the
same rules visually; this section keeps conversational behaviour consistent.

### TickTick Projects

#### Inbox (Default Capture)
- **Inbox**: `699a5943b1bed115b35b1e10` — Default for quick captures; unprocessed tasks land here

#### Tier 1 — Core Focus
- **BR Commercial & BD**: `699c8a338f088b3b190a1a5d` — Full-time job (Bedrock Robotics), commercial & business development; highest priority
- **BR Ops & Intelligence**: `699c8a3c8f088b3b190a1ba1` — Full-time job (Bedrock Robotics), operations & intelligence; highest priority
  - _Note: The old Bedrock Robotics project (`69547156d4ca9147cf3c78fa`) is now closed and replaced by these two._
- **BSIF Fellowship**: `6988fcb958ca9155b99ecc3f` — Fellowship to build a new startup (Extensible); second highest priority
- **Tools**: `693a3b6a34db910305e570fc` — Personal projects I'm building; high priority
- **AI Research**: `6925de124d1951f8c0a709b0` — Complements building tools; high priority

#### Tier 2 — Extensible (New Startup, under BSIF)
These three projects are all part of building Extensible, the startup I'm working on through the fellowship:
- **GTM & Relationships**: `6757d67c8f0808587783ab86` — Go-to-market strategy and relationship management for Extensible
- **Product & Engineering**: `6851d328bc6ad1525900e1df` — Product and engineering work for Extensible
- **Strategy & Research**: `69239e3c064f51f8c0a66b2f` — Documentation, ideas, pitch competitions, courses for Extensible

#### Tier 3 — Active Side Projects & Work
- **PS Agency**: `67ae557f9ebd91593b682a01` — Web design agency I still run
- **CS Study**: `6828b39ea96b91032980817c` — Computer science studies (ongoing)
- **Startup Inbox**: `686c57f73c47910441e8f414` — Random startup ideas (overlaps with Tools; can treat as capture bucket for startup/tool ideas)

#### Someday/Maybe (Parking Lot)
- **Someday/Maybe**: `69b6e5088f085ebce14b22d6` — Single parking lot for low-priority ideas across all areas; scan during weekly review for anything that's become relevant

#### Tier 4 — Background / Low Touch
- **Admin & Errands**: `69239f54252c91f8c0a68ad4` — Personal admin, errands, miscellaneous
- **Relationships & Social**: `69239fd13854d1f8c0a69082` — Social and relationship maintenance
- **Afore Cap**: `68ccc3155baf11eddd3914db` — Former employer (VC fund); low activity
- **BASIS VP**: `681102b0fb161104da46de81` — Future venture fund idea; storage for tasks
- **Applimize**: `680fb5bf9c3dd104da46713d` — Old company; winding down

#### Other Active (Lower Priority)
- **Chaumet Office**: `66884177becd911b75279a94` — Family office work for Alban Chaumet; investment research, financial tools, credit building, philanthropy, and real estate listings
- **UNC General**: `668b7ff488305102443ea121` — University of North Carolina academic life; degree planning, class registration, housing/sublease, study abroad, campus events
- **Tech**: `6695fb18fdb29194d7492736` — Developer tools, productivity workflows, AI agents, and tech content/writing ideas
- **Routines**: `6695fb3aab509194d7492975` — Recurring habits and rituals; daily planning, email triage, groceries, gym, reading reviews, Spanish practice, horizon reviews
- **Reading**: `669c9bd88f088125da4c32bf` — Book queue and reading list; business, strategy, fiction, and personal development (32 books tracked)
- **General Personal**: `669df8928f089169e9760578` — Miscellaneous personal tasks; personal website, clothes, developer events, TickTick setup
- **Real Estate**: `66c2a448151bd14d76dab830` — Real estate investing research; REITs, AZ land analysis, mall redevelopment thesis, agent outreach
- **Connecting People**: `66c2a48e45f3514d76dab9f0` — Introductions and networking matchmaking between contacts
- **Private Equity**: `66c36b538f08a02ea8eedeaf` — PE firm research (Carlyle, Apollo, Sequoia), LP account access, VC/PE event attendance
- **Applications**: `66d74dacbb201101c5b3232b` — Fellowships, grants, and scholarships (OSV, Z Fellows, Accel Scholars, ICSC); also personal website/portfolio builds
- **Public Markets**: `66f6b22b8e53512eb9cf07a0` — Stock market research; mining/HPC stocks, healthcare pitches, SEC filings, investment screening tools
- **Active Deals & Projects**: `66f6b2938ba3112eb9cf0f11` — Active commercial real estate deals and CRE education; ICSC networking, agent meetings, North Scottsdale research for Opus/Alban
- **Venture Capital**: `672e789064be5181d618b716` — VC career development; DRF partnership, deal sourcing pipeline, Bain Capital analysis
- **PPE**: `673a768c7a9a519a677d41c3` — UNC Philosophy, Politics & Economics club; reading groups and leadership transition planning
- **Reframe**: `673a7a716a1a119a677d590d` — Advisory/potential CEO role for Reframe (Jeff's company); white papers, acquisition briefs, product evaluation
- **Crypto**: `6748ad361bda5112cf35c4be` — Web3 learning and projects; dilution dashboard, Ethereum/Farcaster deep dives, Legacy Coin
- **Energy**: `684db6c3227ed1033cf0fd47` — Nuclear, renewables, and deep tech research; Fuse internship prep, quantum computing, manufacturing, DER

### Priority System

TickTick priorities map to my GTD approach:
- **High (5)** — The day's **Highlight**: one task that makes the day feel like a win. Never assign to more than one task per day.
- **Medium (3)** — Next actions; things I intend to do soon
- **Low (1)** — Someday/maybe or low-urgency
- **None (0)** — Inbox / unprocessed

"Next actions" in GTD = **medium priority**.
"Waiting for" tasks go in the **Work project** (closest match: Bedrock Robotics or GTM & Relationships) with the title prefix `WAITING:`.

#### Highlight Rule
The Highlight is the single most important task for the day — not the most urgent, but the one that will make the day feel like a win. Only one High (5) task should exist at any time. If I try to set a second one, flag the conflict and ask which should be the Highlight.

**Highlight visual marker**: When setting a task as the Highlight, prepend `⭐ ` to the task title (e.g., `⭐ Write investor memo`). When a task is demoted from Highlight status, remove the `⭐ ` prefix. This makes the Highlight visually distinct in all TickTick views.

### Workflows

#### Weekly Review
When I say **"weekly review"**, do this sequence:
1. Show all overdue tasks
2. Show all high-priority tasks
3. Show all tasks due this week
4. Ask me what I want to reschedule, complete, or delete
5. Execute my decisions one at a time, confirming each
6. Scan the **Someday/Maybe** project — surface anything that's become relevant or timely, and ask if any items should be promoted to an active project with a due date

#### Three Big Things
Each day has a committed core of **three important tasks** (the Highlight + two others). Additional tasks are a "tail" — nice to do if time permits, but not the measure of the day. When planning, identify the three and name them explicitly before time-blocking anything else.

#### Daily Planning
When I say **"plan my day"**:
1. Show overdue tasks
2. Show tasks due today
3. Check if a Highlight (High priority task) is already set — if yes, surface it prominently; if no, ask me to pick one from the list
4. Identify the **Three Big Things**: the Highlight + two other important tasks. Name them explicitly.
5. Help me prioritize the remaining tasks and suggest a rough time-block order (three blocks for the Big Three, buffer/admin for the tail)
6. Ask if I want to add, reschedule, or drop anything
7. At the end, ask: "What's your Highlight for tomorrow?" — set it if I name one

**Highlight enforcement**: if I already have a High (5) task and try to set another, pause and say which task is currently the Highlight, then ask which one should take that role.

#### End of Day Close
When I say **"close my day"**:
1. Show tasks completed today
2. Show tasks that were scheduled today but not done
3. Ask for each unfinished task: reschedule to tomorrow, move to Someday/Maybe, or drop?
4. Execute decisions one at a time
5. Ask: "What's your Highlight for tomorrow?" — set it if I name one

#### Overdue Hygiene
When showing overdue tasks during daily planning or weekly review:
- Flag anything **3+ days overdue** prominently
- For each, ask: reschedule, move to Someday/Maybe, or drop?
- Don't let overdue tasks silently pile up — surface them actively

#### Priority Audit
At the start of each planning session:
- Check how many tasks are marked High (5)
- If more than 1 is High, list them all and ask which one is the real Highlight
- Demote the rest to Medium (3) after confirmation

#### Focus Mode
When I say **"focus on [project]"**:
- Only surface tasks from that project until I say "unfocus" or switch context
- If I mention unrelated tasks, capture them in Inbox but don't derail the focus

### Behavioral Preferences

- **Prepend 🚩 to all task titles you create** — every task created by Claude should start with `🚩 ` (e.g., `🚩 Go through Matt's Hex projects`)
- **Always confirm before deleting tasks** — never delete without explicit yes; always show the task title AND description when confirming deletions
- **Always confirm before marking complete** — never complete without confirming
- **When creating multiple tasks, use `batch_create_tasks`** — not one-by-one
- **Default project for unspecified tasks: Inbox** (`699a5943b1bed115b35b1e10`) — treat it as the capture bucket
- **For work-related task captures: ask for due date and priority** before creating
- **"Waiting for" tasks**: prefix title with `WAITING:` and always place in **BR Commercial & BD** (`699c8a338f088b3b190a1a5d`) regardless of context — single home for all waiting tasks
- **Extensible tasks** (startup): ask which of the three Extensible projects it belongs to (GTM, Product, or Strategy) unless obvious from context

#### Auto Task Capture
When I mention I'm working on something, automatically:
1. Search for an existing matching task
2. If none exists, create one (with 🚩 prefix) in the appropriate project
3. Start time = current PST, duration = 30 min (unless I say otherwise)
4. Priority = Medium (3) — it's an active next action

### Task Update Safety Rules

To prevent updating the wrong task:

- **Always search first, then update** — never update a task using an ID recalled from memory or inferred from context. Always retrieve the task ID via an explicit search in the same session before updating it.
- **Verify title before updating** — confirm the task title in the search result matches the intended task before executing any update.
- **Never batch updates using unverified IDs** — when updating multiple tasks at once, ensure every task ID was explicitly retrieved and verified in the current session, not assumed from prior context.

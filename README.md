# TickTick Companion

TickTick Companion is a local productivity companion for TickTick. Its main
surface is a fast triage dashboard for planning today, clearing overdue work,
processing Inbox captures, and closing the day. It also includes the same
TickTick OAuth setup and an MCP integration for Claude and other MCP clients.

## What It Does

- Runs a local dashboard for daily triage, Today planning, Inbox
  processing, Waiting tasks, Someday review, and End of Day closeout.
- Enforces a single Highlight task by managing TickTick High priority and the
  `⭐ ` title prefix.
- Records local dashboard actions in SQLite so the End of Day view can show
  completed tasks and action counts.
- Provides a `ticktick-companion` CLI for authentication, dashboard launch, and
  MCP server startup.
- Exposes TickTick projects and tasks to Claude through MCP tools when you want
  conversational task management.

## Prerequisites

- Python 3.10 or higher
- [uv](https://github.com/astral-sh/uv)
- A TickTick account with Open API access
- TickTick API credentials from the [TickTick Developer Center](https://developer.ticktick.com/manage)

## Quickstart: Dashboard First

1. Clone this repository and install the package:

   ```bash
   git clone <this repository>
   cd <repo directory>
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv venv
   source .venv/bin/activate
   uv pip install -e .
   ```

2. Register a TickTick API application:

   - Open the [TickTick Developer Center](https://developer.ticktick.com/manage).
   - Set the redirect URI to `http://localhost:8000/callback`.
   - Save the Client ID and Client Secret.

3. Authenticate TickTick Companion:

   ```bash
   ticktick-companion auth
   ```

   The auth flow asks for your Client ID and Client Secret, opens a browser for
   TickTick authorization, and saves tokens to `.env`.

4. Start the dashboard:

   ```bash
   ticktick-companion dashboard
   ```

   You can also use the dedicated dashboard command:

   ```bash
   ticktick-companion-dashboard
   ```

   Open http://127.0.0.1:8765/ if the browser does not open automatically.

5. Try the seeded demo without TickTick credentials:

   ```bash
   ticktick-companion dashboard --mock
   ticktick-companion-dashboard --mock
   ```

## Dashboard

The dashboard is the primary TickTick Companion workflow. It reuses the same
`.env` tokens as the MCP integration and writes changes through the TickTick
Open API.

The dashboard has seven tabs:

- **Home**: a decision cockpit for attention items, today's commitment, next
  recommended actions, and today's local activity momentum.
- **Overdue**: shows every overdue task, oldest first, with actions to move it
  to Today, Tomorrow, +3d, +1w, a specific date, Someday, Done, or Drop.
- **Today**: groups the Highlight, Three Big Things, and the remaining tail of
  today's work.
- **Inbox**: lets you assign captured tasks to a project, priority, and due
  date in one pass.
- **Waiting**: collects tasks titled with the `WAITING:` prefix.
- **Someday**: supports weekly review scans of low-urgency tasks.
- **End of Day**: shows completed work, action counts, unfinished tasks, and
  tomorrow's lineup with tools to set tomorrow's Highlight.

Keyboard shortcuts:

- `1` through `7` switch tabs.
- `r` refreshes from the TickTick API.

Highlight behavior:

- High priority (`5`) is treated as the one daily Highlight.
- Clicking the Highlight control on a second task opens a confirmation modal.
- On confirmation, the previous Highlight is demoted to Medium priority and the
  new task gets the `⭐ ` title prefix.
- When a task is demoted from Highlight, the prefix is removed automatically.

Caching and timezone:

- API reads are cached for 30 seconds to keep dashboard interactions quick.
- The Refresh button and `r` shortcut drop the cache.
- Real dashboard runs save the last successful project/task snapshot to
  `~/.ticktick-dashboard-cache.json`, render it immediately on startup, and
  refresh live TickTick data in the background.
- Panel and counts responses include `Server-Timing` and `X-TickTick-Cache`
  headers for local load-speed debugging.
- Set `TICKTICK_TIMEZONE=America/New_York` or another IANA timezone in `.env`
  to override the default local date math.

Profile dashboard data loading without opening the browser:

```bash
ticktick-companion dashboard --profile-load
ticktick-companion-dashboard --profile-load
```

### Activity Log

The TickTick public API does not expose completion or change history, so the
dashboard appends every local action to SQLite at the retained compatibility
path `~/.ticktick-dashboard.db`. The End of Day panel uses that log to show
completed tasks and an action breakdown. In `--mock` mode, the log lives in
memory and is pre-seeded for the demo.

Inspect the flat `events` table with any SQLite tool:

```bash
sqlite3 ~/.ticktick-dashboard.db \
  "SELECT ts_local_date, action, COUNT(*) FROM events
   GROUP BY ts_local_date, action ORDER BY ts_local_date DESC LIMIT 20;"
```

## Authentication

TickTick Companion handles OAuth2 locally:

1. You provide the TickTick Client ID and Client Secret.
2. A browser opens for TickTick authorization.
3. A local callback server receives the authorization code.
4. The code is exchanged for access and refresh tokens.
5. Tokens are written to `.env`.
6. The API client refreshes expired access tokens automatically.

Re-run this command if you revoke access or delete `.env`:

```bash
ticktick-companion auth
```

For the hosted dashboard, the app also exposes an in-browser recovery flow at
`/auth/ticktick/start`. If the TickTick token expires or is revoked, sign in to
the dashboard and click **Authorize TickTick** on the setup screen. Add the
hosted callback URL to your TickTick Developer Center app:

```text
https://<your-domain>/auth/ticktick/callback
```

## Deploying to Vercel

This repository includes `api/index.py` and `vercel.json` for Vercel's Python
runtime. The hosted app uses the same Flask dashboard with a single-password
login gate and stores refreshed TickTick OAuth tokens in Upstash Redis.

Required Vercel environment variables:

```env
TICKTICK_CLIENT_ID=
TICKTICK_CLIENT_SECRET=
TICKTICK_DASHBOARD_PASSWORD=
TICKTICK_DASHBOARD_SECRET_KEY=
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
```

Optional bootstrap or override variables:

```env
TICKTICK_ACCESS_TOKEN=
TICKTICK_REFRESH_TOKEN=
TICKTICK_REDIRECT_URI=https://<your-domain>/auth/ticktick/callback
TICKTICK_TOKEN_STORE_PREFIX=ticktick_companion
```

Initial access and refresh tokens can be pasted into Vercel env vars once, but
after the first hosted OAuth flow the app saves renewed tokens to Upstash rather
than trying to mutate Vercel environment variables.

Deploy from a linked project:

```bash
vercel
vercel --prod
```

### Dida365

[滴答清单 - Dida365](https://dida365.com/home) uses a similar OAuth flow. Register
an app in the [Dida365 Developer Center](https://developer.dida365.com/manage),
set the redirect URI to `http://localhost:8000/callback`, and add these values
to `.env` before running `ticktick-companion auth`:

```env
TICKTICK_BASE_URL='https://api.dida365.com/open/v1'
TICKTICK_AUTH_URL='https://dida365.com/oauth/authorize'
TICKTICK_TOKEN_URL='https://dida365.com/oauth/token'
```

## MCP Integration

Use the MCP integration when you want Claude or another MCP client to read and
update TickTick through TickTick Companion.

Run the MCP server locally:

```bash
ticktick-companion run
```

### Claude for Desktop

1. Install [Claude for Desktop](https://claude.ai/download).
2. Edit the Claude Desktop config file.

   macOS:

   ```bash
   nano ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

   Windows:

   ```bash
   notepad %APPDATA%\Claude\claude_desktop_config.json
   ```

3. Add this MCP configuration, using absolute paths:

   ```json
   {
     "mcpServers": {
       "ticktick": {
         "command": "<absolute path to uv>",
         "args": ["run", "--directory", "<absolute path to this repo>", "ticktick-companion", "run"]
       }
     }
   }
   ```

4. Restart Claude Desktop.

Once connected, Claude will show the TickTick tools in its tool menu.

### MCP Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_projects` | List all TickTick projects | None |
| `get_project` | Get details for one project | `project_id` |
| `get_project_tasks` | List tasks in one project | `project_id` |
| `get_task` | Get one task | `project_id`, `task_id` |
| `create_task` | Create a task | `title`, `project_id`, `content` optional, `start_date` optional, `due_date` optional, `priority` optional |
| `update_task` | Update a task | `task_id`, `project_id`, `title` optional, `content` optional, `start_date` optional, `due_date` optional, `priority` optional |
| `complete_task` | Mark a task complete | `project_id`, `task_id` |
| `delete_task` | Delete a task | `project_id`, `task_id` |
| `create_project` | Create a project | `name`, `color` optional, `view_mode` optional |
| `delete_project` | Delete a project | `project_id` |
| `get_all_tasks` | Get tasks from all open projects | None |
| `get_tasks_by_priority` | Filter tasks by priority | `priority_id` where `0` none, `1` low, `3` medium, `5` high |
| `search_tasks` | Search titles, content, and subtasks | `search_term` |
| `get_tasks_due_today` | Get tasks due today | None |
| `get_tasks_due_tomorrow` | Get tasks due tomorrow | None |
| `get_tasks_due_in_days` | Get tasks due in exactly N days | `days` |
| `get_tasks_due_this_week` | Get tasks due in the next 7 days | None |
| `get_overdue_tasks` | Get overdue tasks | None |
| `get_engaged_tasks` | Get high-priority, due-today, or overdue tasks | None |
| `get_next_tasks` | Get medium-priority or due-tomorrow tasks | None |
| `batch_create_tasks` | Create multiple tasks at once | `tasks` list |

Example prompts:

- "Show me everything overdue."
- "What are my tasks due today?"
- "Create a task in my Inbox to review the dashboard docs tomorrow."
- "Search for tasks about project alpha."
- "Break this project into five smaller actionable tasks."

## Compatibility

This project used to be centered on the old `ticktick-mcp` package name. The
main package and commands are now TickTick Companion, but existing integrations
that call `ticktick-mcp`, `ticktick-dashboard`, or `python -m ticktick_mcp.cli`
continue to work through compatibility wrappers.

Prefer these commands for new setup:

```bash
ticktick-companion auth
ticktick-companion dashboard
ticktick-companion-dashboard
ticktick-companion run
```

## Development

### Project Structure

```text
ticktick-companion/
├── README.md
├── requirements.txt
├── setup.py
├── test_server.py
├── ticktick_companion/
│   ├── cli.py              # CLI for auth, dashboard, and MCP
│   ├── api/
│   │   ├── client.py       # TickTick API client
│   │   └── oauth.py        # OAuth flow and auth command
│   ├── dashboard/
│   │   ├── app.py          # Local Flask dashboard
│   │   ├── event_log.py    # SQLite activity log
│   │   ├── mock_data.py    # Seeded demo data
│   │   ├── templates/
│   │   └── static/
│   └── mcp/
│       └── server.py       # MCP server tools
└── ticktick_mcp/           # Backward-compatible wrappers
```

### Local Checks

Verify credentials and basic API access:

```bash
uv run test_server.py
```

Run the dashboard in mock mode while developing UI changes:

```bash
ticktick-companion dashboard --mock
```

## Reference

- [TickTick Open API reference](./ticktick-openapi.md)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## License

This project is licensed under the MIT License. See the LICENSE file for
details.

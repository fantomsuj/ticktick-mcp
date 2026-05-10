"""Vercel WSGI entrypoint for TickTick Companion."""

import logging

from ticktick_companion.api.token_store import default_token_store
from ticktick_companion.dashboard.app import _build_client, create_app
from ticktick_companion.dashboard.event_log import EventLog

logging.basicConfig(level=logging.INFO)

token_store = default_token_store()
client = _build_client(mock=False, token_store=token_store)

# Vercel's filesystem is ephemeral, so keep dashboard action history in memory
# and persist only OAuth tokens through the configured token store.
app = create_app(
    client,
    event_log=EventLog(":memory:"),
    snapshot_path=None,
    token_store=token_store,
)

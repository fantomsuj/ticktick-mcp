# Agent Instructions

These instructions support TickTick Companion. The local dashboard is the main
daily planning surface; Claude/MCP usage should follow the same scheduling,
Highlight, Inbox, Waiting, and End of Day rules so changes stay consistent
between the dashboard and conversational workflows.

## Task Scheduling

- When scheduling a task for a specific date, always set the **start date equal to the due date** so it appears as a single day on the calendar (not a multi-day block).
- When scheduling a task at a specific time, default to a **30-minute duration** (i.e. start time = due time - 30 min) unless the user specifies otherwise.

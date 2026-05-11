# Agent Instructions

These instructions support TickTick Companion as a focused TickTick triage app.
The local dashboard is the main daily planning surface. Claude/MCP usage is an
agent bridge for helping build, debug, and maintain the app; when it changes
TickTick data, it should follow the same scheduling, Inbox, Waiting, and End of
Day rules so agent actions stay consistent with the dashboard.

## Task Scheduling

- When scheduling a task for a specific date, always set the **start date equal to the due date** so it appears as a single day on the calendar (not a multi-day block).
- When scheduling a task at a specific time, default to a **30-minute duration** (i.e. start time = due time - 30 min) unless the user specifies otherwise.

"""Reflective prompts shown above each panel.

The dashboard is a thinking tool, not a backlog viewer. Each panel gets one
short, pointed question that nudges a decision before the cards. Prompts
rotate by local date so they stay fresh across days but stay stable during
a single planning session.
"""

from datetime import date as date_cls
from typing import Dict, List, Optional


# Each panel: (question, hint). The question is the focal nudge; the hint is
# a one-liner of how to act on it. Hints are deliberately concrete.
_PROMPTS: Dict[str, List[Dict[str, str]]] = {
    "today": [
        {
            "question": "What's the one thing that, if done today, would make today feel like a win?",
            "hint": "That's your Highlight. Click ⭐ on it.",
        },
        {
            "question": "If you could only finish one task today, which would it be?",
            "hint": "Make it the Highlight. Everything else is the tail.",
        },
        {
            "question": "Which task on this list would you most regret skipping?",
            "hint": "Promote it to Highlight before time-blocking anything else.",
        },
        {
            "question": "What's the smallest version of progress you'd accept today?",
            "hint": "Pick that as the Highlight — momentum beats ambition.",
        },
    ],
    "triage": [
        {
            "question": "Which of these are still real?",
            "hint": "If a task has slid past its due date, decide: do it, redate it, park it, or drop it.",
        },
        {
            "question": "What's been here longest — and why is it still here?",
            "hint": "Stale tasks usually mean the next action is unclear. Rewrite it or drop it.",
        },
        {
            "question": "Which of these would take 5 minutes if you stopped postponing it?",
            "hint": "Do those now or push them to today. Don't let them rot.",
        },
        {
            "question": "If you cleared this entire list right now, what would actually break?",
            "hint": "Probably less than you think. Be ruthless.",
        },
    ],
    "tomorrow": [
        {
            "question": "If tomorrow only had room for one win, what would it be?",
            "hint": "Click ⭐ on tomorrow's Highlight before you close out today.",
        },
        {
            "question": "Is tomorrow already overcommitted?",
            "hint": "If there are more than 3 important items, push the rest to Someday.",
        },
        {
            "question": "What's the first thing you want to touch tomorrow morning?",
            "hint": "Make sure it's at the top of the list, not buried in the tail.",
        },
    ],
    "inbox": [
        {
            "question": "For each capture: what's the very next physical action?",
            "hint": "If you can't name one, it's not a task — it's a project. Rewrite the title.",
        },
        {
            "question": "Is this even yours to do?",
            "hint": "Delegate, defer, or drop. Inbox is a decision queue, not a holding pen.",
        },
        {
            "question": "Which of these would take less than 2 minutes right now?",
            "hint": "Do them immediately. Assign or drop the rest.",
        },
    ],
    "waiting": [
        {
            "question": "Has it been long enough? What's the lightest nudge you could send?",
            "hint": "If it's been a week, ping. If it's been three, escalate or drop.",
        },
        {
            "question": "Are any of these blocking your Highlight?",
            "hint": "If yes, move the nudge into today.",
        },
    ],
    "someday": [
        {
            "question": "Has anything here become real?",
            "hint": "If yes — promote it to a project with a due date. If no — leave it be.",
        },
        {
            "question": "What would you regret never doing?",
            "hint": "Move one item out of someday and into this quarter.",
        },
    ],
    "eod": [
        {
            "question": "What did you actually do today? What did you avoid?",
            "hint": "Naming the avoidance is half the work — the rest is rescheduling honestly.",
        },
        {
            "question": "What's tomorrow's one thing?",
            "hint": "Set tomorrow's Highlight before you close the laptop.",
        },
        {
            "question": "What did today teach you about how you actually work?",
            "hint": "Adjust tomorrow's plan to match the lesson, not the fantasy.",
        },
    ],
}


def panel_prompt(name: str, today: Optional[date_cls] = None) -> Optional[Dict[str, str]]:
    """Return today's prompt for `name`, or None if the panel has no prompts."""
    options = _PROMPTS.get(name)
    if not options:
        return None
    seed = today or date_cls.today()
    # Stable index for the day; rotates as the date advances.
    idx = seed.toordinal() % len(options)
    return options[idx]

"""Compatibility wrapper for the renamed TickTick Companion dashboard."""

import sys

from ticktick_companion.dashboard.app import main


if __name__ == "__main__":
    sys.exit(main())


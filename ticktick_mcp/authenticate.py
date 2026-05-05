"""Compatibility wrapper for the renamed TickTick Companion auth command."""

import sys

from ticktick_companion.api.oauth import main


if __name__ == "__main__":
    sys.exit(main())


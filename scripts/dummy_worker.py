#!/usr/bin/env python3
"""Long-running persistence probe: appends a counter to a file every second.

Usage: dummy_worker.py COUNTER_FILE
Used to verify that tmux sessions (and their processes) outlive browser
tabs, WebSocket drops, SSH connection loss and muxroom restarts.
"""

import sys
import time
from pathlib import Path


def main() -> None:
    target = Path(sys.argv[1])
    count = 0
    while True:
        count += 1
        target.write_text(f"{count} {time.time():.0f}\n")
        print(f"tick {count}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()

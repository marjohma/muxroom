"""The dashboard must never end tmux sessions or kick other clients."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "muxroom"
FORBIDDEN = re.compile(r"kill-session|kill-server|detach-client|kill-pane|kill-window|respawn-")


def test_no_destructive_tmux_verbs_in_source():
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if FORBIDDEN.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []

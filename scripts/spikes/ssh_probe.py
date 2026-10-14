"""M1 spike: probe a host over AsyncSSH using the user's ~/.ssh/config.

Usage: uv run --project backend python scripts/spikes/ssh_probe.py <ssh-alias> [tmux-path]
"""

import asyncio
import sys
import time

import asyncssh

PROBE = r"""
echo "shell=$SHELL"
echo "home=$HOME"
echo "path=$PATH"
echo "uname=$(uname -sr)"
echo "tmux_on_path=$(command -v tmux || echo MISSING)"
for p in /opt/homebrew/bin/tmux /usr/local/bin/tmux /usr/bin/tmux; do [ -x "$p" ] && echo "tmux_candidate=$p $($p -V)"; done
for a in claude codex opencode; do echo "agent_$a=$(command -v $a || echo MISSING)"; done
if command -v loginctl >/dev/null 2>&1; then
  echo "linger=$(loginctl show-user "$USER" -p Linger 2>&1)"
  echo "kill_user_processes=$(grep -E '^\s*KillUserProcesses' /etc/systemd/logind.conf /etc/systemd/logind.conf.d/*.conf 2>/dev/null || echo 'unset (compiled default)')"
  echo "systemd=$(systemctl --version 2>/dev/null | head -1)"
fi
"""


async def main(alias: str) -> None:
    t0 = time.monotonic()
    async with asyncssh.connect(alias) as conn:
        print(f"connected in {time.monotonic() - t0:.2f}s")
        result = await conn.run(PROBE, check=False)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        login = await conn.run("$SHELL -lic 'command -v tmux claude codex opencode' 2>/dev/null", check=False)
        print("login-shell lookup:\n" + str(login.stdout))
        async with conn.start_sftp_client() as sftp:
            home = await sftp.realpath(".")
            print(f"sftp ok, realpath('.')={home}")
            print(f"sftp version={sftp.version}")
            exts = getattr(sftp, "_handler", None)
            print("sftp extensions:", getattr(exts, "_extensions", "n/a"))


asyncio.run(main(sys.argv[1]))

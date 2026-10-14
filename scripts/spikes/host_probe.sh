#!/bin/sh
# M1 spike: run on a remote host via `ssh <alias> sh -s < host_probe.sh`.
echo "shell=$SHELL"
echo "home=$HOME"
echo "path=$PATH"
echo "uname=$(uname -sr)"
echo "tmux_on_path=$(command -v tmux || echo MISSING)"
for p in /opt/homebrew/bin/tmux /usr/local/bin/tmux /usr/bin/tmux; do
  [ -x "$p" ] && echo "tmux_candidate=$p $("$p" -V)"
done
for a in claude codex opencode; do echo "agent_$a=$(command -v "$a" || echo MISSING)"; done
echo "login_shell_lookup=$("$SHELL" -lic 'command -v tmux claude codex opencode' 2>/dev/null | tr '\n' ' ')"
if command -v loginctl >/dev/null 2>&1; then
  echo "linger=$(loginctl show-user "$USER" -p Linger 2>&1)"
  echo "kill_user_processes=$(grep -hE '^[[:space:]]*KillUserProcesses' /etc/systemd/logind.conf /etc/systemd/logind.conf.d/*.conf 2>/dev/null || echo 'not set (systemd default: no)')"
  echo "systemd=$(systemctl --version 2>/dev/null | head -1)"
  echo "session_scope=$(cat /proc/self/cgroup 2>/dev/null | tail -1)"
fi
echo "afs_tokens=$(tokens 2>/dev/null | grep -i 'tokens for' | head -1 || echo none)"
echo "sftp_server=$(ls /usr/lib/openssh/sftp-server /usr/libexec/openssh/sftp-server /usr/libexec/sftp-server 2>/dev/null | tr '\n' ' ')"

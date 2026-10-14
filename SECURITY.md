# Security policy

muxroom runs a local web server that can execute commands on your remote
machines, so security reports are very welcome.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability
reporting instead: on the repository page, open **Security → Report a
vulnerability**. Include the version or commit, steps to reproduce, and the
impact you see. You will get an acknowledgement within a week.

## Supported versions

Only the latest release (and `main`) receives fixes while the project is
below 1.0.

## Threat model (summary)

In scope:

- other local users and processes on the dashboard machine,
- malicious websites open in the same browser (CSRF, DNS rebinding,
  cross-site WebSocket hijacking, framing),
- untrusted content on remote hosts: file contents (SVG, PDF, HTML),
  file and directory names, symlinks, tmux session names, process output,
- leakage of the access token (logs, history, other local ports).

Out of scope:

- an attacker who already runs code as your user, or controls your
  `config.toml` or `~/.ssh/config` (both are trusted),
- exposing muxroom to a network with `--allow-remote-bind` (plain HTTP; do
  this only behind your own TLS proxy and firewall),
- the security of the remote hosts and of the agents themselves.

Known limitation: muxroom serves on a fixed localhost port. If another local
user binds that port while muxroom is not running, pages served there share
the browser origin. The token is kept in per-tab `sessionStorage` (not in a
cookie) to keep that window small; avoid reopening an old muxroom tab while
muxroom is stopped on a shared multi-user machine.

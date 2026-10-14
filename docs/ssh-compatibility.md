# SSH compatibility

muxroom has two remote transports. Choose one per host with `transport = …`.

## `openssh` (recommended)

This transport runs the system `ssh` binary, so it supports everything your
OpenSSH supports:

| Feature | Status |
|---|---|
| `~/.ssh/config`: Host, Match, Include, HostName, User, Port, IdentityFile, … | yes (OpenSSH itself) |
| ProxyJump / ProxyCommand | yes |
| ControlMaster / ControlPersist (shared connections) | yes; needed for password-only hosts |
| ssh-agent, macOS Keychain (`UseKeychain`) | yes |
| Kerberos / GSSAPI | yes |
| Password / OTP / keyboard-interactive | only through the one-time **Sign in** terminal (`ssh -f -N`) with a ControlMaster. All other calls use `BatchMode=yes` |
| Host-key checking | strict, as configured in your ssh_config. muxroom never adds keys |
| Extra options | `ssh_options = ["Key=Value"]` (each becomes `-o Key=Value`), `ssh_config = "path"` (`-F`) |
| Files | SFTP over `ssh -s <host> sftp`. Requires the SFTP subsystem |

Commands (session lists, creating and renaming sessions, status) go through
one long-lived `ssh -T <host> sh` per host. That avoids a new SSH session and
a login shell (which sources `~/.bashrc`) for every refresh. The channel
opens on first use, reconnects after a drop, and closes after 5 minutes
without commands. Each terminal tab and the file browser use their own `ssh`
processes. With a ControlMaster everything shares one TCP connection;
without one, each process opens its own connection.

While muxroom is in use, that channel also keeps a ControlMaster alive,
so password hosts need **Sign in** less often.

## `asyncssh` (pure Python)

AsyncSSH reads `~/.ssh/config` itself and supports the common options
(Host, Match subset, Include, HostName, User, Port, IdentityFile,
IdentitiesOnly, ProxyJump, ProxyCommand, UserKnownHostsFile,
ServerAliveInterval). Limitations:

- **No ControlMaster.** Existing shared OpenSSH connections are not reused,
  and every connection authenticates on its own.
- **No interactive authentication.** Only keys (files without a passphrase
  or keys in ssh-agent) and certificates work.
- **`UseKeychain` is ignored.** On macOS, add keys to the agent with
  `ssh-add --apple-use-keychain` beforehand.
- **Jump hosts must accept key auth too**, because ProxyJump is
  re-implemented by AsyncSSH.
- Unknown host keys are rejected (strict checking against `known_hosts`).

AsyncSSH opens one pooled control connection per host (commands run
through a persistent `sh` on it), one connection per terminal tab, and one
dedicated connection for SFTP.

## Detach semantics (both transports)

Terminals run `tmux -u attach-session -t '$<id>'`. There is no `-d`, so
other clients (your iTerm, a second browser tab) stay attached. Closing a tab
closes the PTY or SSH channel. The tmux client receives SIGHUP and detaches,
and the session and its processes keep running. The code contains no
`kill-session`, `kill-server` or `detach-client`; a unit test enforces this.

tmux 3.1+ sizes windows to the most recently active client
(`window-size latest`). If a browser tab and a terminal emulator are attached
at the same time, the window follows whichever was used last. muxroom does
not change your tmux options.

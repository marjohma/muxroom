# Remote host setup

muxroom needs nothing installed on remote hosts except **tmux**, a **POSIX
login shell** (bash, zsh, sh, dash, ksh) and, for the file browser, the
**SFTP subsystem** (enabled by default in OpenSSH). There is no remote daemon.

muxroom never prompts for passwords and never stores credentials. Every
remote command runs as `ssh -o BatchMode=yes <alias> …`, so whatever lets
`ssh -o BatchMode=yes <alias> true` succeed in your terminal also works for
muxroom.

## 1. Make non-interactive SSH work

Check from a terminal:

```sh
ssh -o BatchMode=yes <alias> true && echo ok
```

Pick one of these options:

**a) Key in ssh-agent.** This is the usual setup. Add your public key to
`~/.ssh/authorized_keys` on the host and load the key into your agent
(`ssh-add --apple-use-keychain ~/.ssh/id_ed25519` on macOS).

**b) Password or one-time-code hosts with a shared connection (ControlMaster).**
Some hosts accept no keys. One example is an AFS home directory: sshd cannot
read `~/.ssh/authorized_keys` before you hold an AFS token. For such hosts,
let OpenSSH keep one authenticated connection open and share it:

```sshconfig
Host *
    ControlMaster auto
    ControlPath ~/.ssh/control/%r@%h:%p
    ControlPersist 10m
```

`mkdir -p ~/.ssh/control && chmod 700 ~/.ssh/control`, then authenticate once:
either `ssh <alias>` in a terminal, or **Sign in** (key icon) next to the host
in muxroom. Sign in runs `ssh -f -N <alias>` in a browser terminal: you
type the password into ssh itself, and ssh keeps the shared connection
afterwards. While muxroom runs, its status polling (every 15 s) keeps
`ControlPersist` from expiring. After a longer break you need to sign in again.

**c) Kerberos / GSSAPI.** With `kinit` locally and `GSSAPIAuthentication yes`
(plus `GSSAPIDelegateCredentials yes` if you need tickets on the host), no
password is needed.

**Jump hosts.** `ProxyJump` works unchanged. With ControlMaster, signing in to
the target host also authenticates the jump connection.

## 2. Host keys

muxroom never accepts unknown host keys. If a host shows *Host key problem*,
run `ssh <alias>` once in a terminal, verify the fingerprint, and accept it.
With `ProxyJump`, both hosts must be in `~/.ssh/known_hosts`.

## 3. tmux on PATH

Non-interactive SSH commands get a minimal PATH. Homebrew on macOS
(`/opt/homebrew/bin`) is usually missing. If a host shows *tmux not found*,
set the full path in `config.toml`:

```toml
tmux_path = "/opt/homebrew/bin/tmux"
```

Agents (claude, codex, opencode) do **not** need to be on that PATH. muxroom
types the agent command into the new session's login shell, which loads your
normal environment.

## 4. systemd: keep processes after logout (Linux)

On some Linux systems logind kills all your processes when your last SSH
session ends (`KillUserProcesses=yes`), and that includes tmux. Check:

```sh
loginctl show-user "$USER" -p Linger
grep -h KillUserProcesses /etc/systemd/logind.conf /etc/systemd/logind.conf.d/*.conf
```

If it is enabled, ask the administrator for `loginctl enable-linger <user>`.
The default on Debian/Ubuntu is `no`.

## 5. AFS home directories

AFS tokens expire (typically after days). Programs inside a tmux session keep
the token of the session that started the tmux server. When it expires, agents
lose write access to your AFS home, even though the session keeps running.
Renew it inside the session (`kinit && aklog`) or start long jobs with a
renewable ticket. muxroom cannot do this for you.

## 6. Remote macOS and Claude Code

Claude Code on macOS stores credentials in the login keychain. A tmux server
first started over SSH may not be able to unlock it. If `claude` asks you to
log in again inside a remote session, run `security unlock-keychain` in that
session once.

## Host-specific notes (author's setup, for reference)

| Host | What was needed |
|---|---|
| Linux, AFS home, reachable over VPN | Option (b): password login plus ControlMaster. tmux 3.5a is on PATH. systemd default (`KillUserProcesses` not set). |
| macOS behind that Linux host (`ProxyJump`) | Option (b) via the jump host. `tmux_path = "/opt/homebrew/bin/tmux"`. |

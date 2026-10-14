import asyncio
import contextlib

import pytest

from muxroom.errors import MuxroomError
from muxroom.transport.command_channel import (
    ChannelBroken,
    ChannelPool,
    ChannelStreams,
    CommandChannel,
)


class LocalStreams(ChannelStreams):
    """Unslotted subclass so tests can reach the underlying process."""


async def start_sh(preamble: str = "") -> ChannelStreams:
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        f"{preamble}exec /bin/sh",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )

    async def close():
        with contextlib.suppress(Exception):
            proc.stdin.close()
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()

    async def stderr_text():
        return (await proc.stderr.read()).decode()

    streams = LocalStreams(proc.stdin, proc.stdout, proc.stderr, close, stderr_text)
    streams.proc = proc
    return streams


def fail(stderr: str) -> MuxroomError:
    return MuxroomError("ssh_auth_failed", stderr or "closed")


@pytest.fixture
async def channel():
    ch = await CommandChannel.open(start_sh, timeout=5, on_start_failure=fail)
    yield ch
    await ch.close()


async def test_status_stdout_stderr_are_separated(channel):
    r = await channel.run("echo out; echo err >&2; exit_code() { return 3; }; exit_code", timeout=5)
    assert (r.exit_status, r.stdout, r.stderr) == (3, "out\n", "err\n")
    r = await channel.run("printf 'no newline'", timeout=5)
    assert (r.exit_status, r.stdout, r.stderr) == (0, "no newline", "")


async def test_output_resembling_markers_is_preserved(channel):
    r = await channel.run("echo '@@ad:0:deadbeef@@'; echo '@@muxroom-ready@@'", timeout=5)
    assert r.stdout == "@@ad:0:deadbeef@@\n@@muxroom-ready@@\n"


async def test_large_stdout_and_stderr_together_do_not_deadlock(channel):
    r = await channel.run(
        "head -c 3000000 /dev/zero | tr '\\0' a; head -c 3000000 /dev/zero >&2", timeout=20
    )
    assert len(r.stdout) == 3_000_000 and len(r.stderr) == 3_000_000


async def test_command_reading_stdin_cannot_eat_the_protocol(channel):
    r = await channel.run("cat; echo after-cat", timeout=5)
    assert r.stdout == "after-cat\n"
    assert (await channel.run("echo still-alive", timeout=5)).stdout == "still-alive\n"


async def test_state_does_not_leak_between_commands_via_stdin(channel):
    await channel.run("read x; echo got:$x", timeout=5)
    assert (await channel.run("echo ok", timeout=5)).stdout == "ok\n"


async def test_timeout_breaks_channel(channel):
    with pytest.raises(MuxroomError) as err:
        await channel.run("sleep 5", timeout=0.3)
    assert err.value.code == "timeout"
    assert channel.broken
    with pytest.raises(ChannelBroken):
        await channel.run("echo x", timeout=1)


async def test_concurrent_commands_are_serialized(channel):
    results = await asyncio.gather(*(channel.run(f"echo {i}", timeout=5) for i in range(25)))
    assert [r.stdout for r in results] == [f"{i}\n" for i in range(25)]


async def test_login_noise_is_drained_before_first_command():
    ch = await CommandChannel.open(
        lambda: start_sh("echo motd; echo rc-warning >&2; "), timeout=5, on_start_failure=fail
    )
    r = await ch.run("echo first", timeout=5)
    assert (r.stdout, r.stderr) == ("first\n", "")
    await ch.close()


async def test_start_failure_is_classified():
    async def dead():
        return await start_sh("echo 'Permission denied (publickey,password).' >&2; exit 255; ")

    with pytest.raises(MuxroomError) as err:
        await CommandChannel.open(dead, timeout=5, on_start_failure=fail)
    assert err.value.code == "ssh_auth_failed"
    assert "Permission denied" in err.value.message


async def test_pool_reopens_after_remote_shell_died_while_idle():
    started = []

    async def opener():
        ch = await CommandChannel.open(start_sh, timeout=5, on_start_failure=fail)
        started.append(ch)
        return ch

    pool = ChannelPool(opener)
    assert (await pool.run("echo one", timeout=5)).stdout == "one\n"
    started[0]._s.proc.kill()  # type: ignore[attr-defined]  # e.g. network drop while idle
    await started[0]._s.proc.wait()  # type: ignore[attr-defined]
    assert (await pool.run("echo two", timeout=5)).stdout == "two\n"
    assert len(started) == 2
    await pool.close()


async def test_pool_does_not_retry_commands_that_may_have_run():
    async def opener():
        return await CommandChannel.open(start_sh, timeout=5, on_start_failure=fail)

    pool = ChannelPool(opener)
    with pytest.raises(ChannelBroken):
        # output was produced, then the shell died: must not be re-run
        await pool.run("echo side-effect; kill -9 $$", timeout=5)
    await pool.close()


async def test_pool_closes_idle_channel():
    async def opener():
        return await CommandChannel.open(start_sh, timeout=5, on_start_failure=fail)

    pool = ChannelPool(opener, idle_close=0.2)
    await pool.run("true", timeout=5)
    channel = pool._channel
    await asyncio.sleep(0.5)
    assert channel is not None and channel.broken and pool._channel is None


async def test_oversized_reply_breaks_channel_cleanly(monkeypatch):
    import muxroom.transport.command_channel as mod

    ch = await CommandChannel.open(start_sh, timeout=5, on_start_failure=fail)
    ch._out._limit = 100_000
    with pytest.raises(MuxroomError) as err:
        await ch.run("head -c 500000 /dev/zero | tr '\\0' x", timeout=10)
    assert "exceeded" in err.value.message
    assert ch.broken
    assert mod.MAX_REPLY_BYTES >= 16 * 2**20

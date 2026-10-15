import asyncio
import os
from pathlib import Path

import pytest

from muxroom.config import AppConfig, FileLimits, HostConfig, HostFiles
from muxroom.errors import MuxroomError
from muxroom.files.policy import containing_root, is_contained, parent_within
from muxroom.files.preview import kind_from_name, matches_magic, preview_headers
from muxroom.services.files import FileService
from muxroom.services.hosts import HostService
from muxroom.services.sessions import SessionService
from muxroom.transport.local_files import LocalFiles
from muxroom.transport.sftp_files import SftpFiles
from tests.fake_sftp import FakeSftpClient
from tests.fakes import FakeTmuxTransport

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


# ------------------------------------------------------------------ policy


def test_containment_uses_components_not_prefixes():
    roots = ["/home/a/proj", "/data"]
    assert containing_root("/home/a/proj/x", roots) == "/home/a/proj"
    assert not is_contained("/home/a/proj2", roots)
    assert not is_contained("/home/a", roots)
    assert is_contained("/data", roots)


def test_most_specific_root_and_parent():
    roots = ["/a", "/a/b"]
    assert containing_root("/a/b/c", roots) == "/a/b"
    assert parent_within("/a/b", roots) == "/a"
    assert parent_within("/a", roots) is None
    assert parent_within("/elsewhere", roots) is None


# ------------------------------------------------------------------ preview


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.PDF", "pdf"),
        ("b.png", "png"),
        ("c.jpeg", "jpeg"),
        ("d.jpg", "jpeg"),
        ("e.svg", "svg"),
        ("f.html", None),
        ("noext", None),
    ],
)
def test_kind_from_name(name, kind):
    assert kind_from_name(name) == kind


def test_magic():
    assert matches_magic("png", PNG)
    assert matches_magic("pdf", PDF)
    assert matches_magic("svg", SVG)
    assert matches_magic("jpeg", b"\xff\xd8\xff\xe0")
    assert not matches_magic("png", b"<html>")
    assert not matches_magic("svg", b"<html><body></body></html>")
    assert not matches_magic("pdf", b"MZ\x90")


def test_svg_headers_are_sandboxed():
    headers = preview_headers("svg", "plot ü.svg")
    assert headers["Content-Security-Policy"].startswith("sandbox")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "filename*=UTF-8''plot%20%C3%BC.svg" in headers["Content-Disposition"]
    assert headers["Content-Disposition"].startswith("inline")


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def tree(tmp_path) -> Path:
    root = Path(os.path.realpath(tmp_path))
    (root / "home" / "proj" / "plots").mkdir(parents=True)
    (root / "home" / "proj" / "incoming").mkdir()
    (root / "home" / "secret").mkdir()
    (root / "home" / "secret" / "key.txt").write_text("secret")
    (root / "home" / "proj2").mkdir()
    (root / "home" / "proj2" / "x.txt").write_text("x")
    plots = root / "home" / "proj" / "plots"
    (plots / "fig.png").write_bytes(PNG)
    (plots / "fig.pdf").write_bytes(PDF)
    (plots / "fig.svg").write_bytes(SVG)
    (plots / "fake.png").write_bytes(b"<html>not a png</html>")
    (plots / "notes.txt").write_text("hello")
    (plots / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    (plots / "escape").symlink_to(root / "home" / "secret")
    (plots / "escape.txt").symlink_to(root / "home" / "secret" / "key.txt")
    (plots / "inside").symlink_to(plots / "fig.png")
    (plots / "broken").symlink_to(root / "nope")
    (root / "home" / "proj" / "linked-root").symlink_to(plots)
    return root


def make_service(tree: Path, files, *, limits=None):
    fake = FakeTmuxTransport(home=str(tree / "home"), dirs=[str(tree / "home")], files=files)
    session = fake.add_session("s", str(tree / "home" / "proj"))
    host = HostConfig(
        id="local",
        allowed_roots=["~"],
        files=HostFiles(read_roots=["~/proj"], upload_roots=["~/proj/incoming"]),
    )
    config = AppConfig(
        hosts=[host], files=limits or FileLimits(preview_max_mb=1, download_max_mb=2)
    )
    hosts = HostService(config, lambda _h: fake)
    service = FileService(hosts, SessionService(hosts), config.files)
    return service, fake, session


@pytest.fixture(params=["local", "sftp"])
def env(request, tree):
    if request.param == "local":
        files = LocalFiles(home=str(tree / "home"))
        client = None
    else:
        client = FakeSftpClient(str(tree / "home"))

        async def factory():
            async def closer():
                client.exit()

            return client, closer

        files = SftpFiles("local", factory)
    service, _fake, session = make_service(tree, files)
    return service, tree, session, client


async def collect(chunks) -> bytes:
    return b"".join([c async for c in chunks])


# ------------------------------------------------------------------ service (both backends)


async def test_roots(env):
    service, tree, _, _ = env
    roots = await service.roots("local")
    assert [(r.path, r.writable) for r in roots] == [(str(tree / "home" / "proj"), False)]


async def test_list_flags_symlinks_and_sorts(env):
    service, tree, _, _ = env
    listing = await service.list("local", "~/proj/plots")
    assert listing.path == str(tree / "home" / "proj" / "plots")
    assert listing.parent == str(tree / "home" / "proj")
    by_name = {e.name: e for e in listing.entries}
    assert by_name["escape"].outside_roots and by_name["escape.txt"].outside_roots
    assert by_name["broken"].outside_roots
    assert not by_name["inside"].outside_roots and by_name["inside"].preview == "png"
    assert by_name["fig.pdf"].preview == "pdf" and by_name["notes.txt"].preview is None
    dirs_first = [e.kind for e in listing.entries]
    assert dirs_first.index("file") > dirs_first.index("dir")


async def test_parent_is_none_at_root(env):
    service, _, _, _ = env
    assert (await service.list("local", "~/proj")).parent is None


@pytest.mark.parametrize(
    "path",
    [
        "~",
        "~/proj/..",
        "~/proj2/x.txt",
        "~/proj/plots/escape",
        "~/proj/plots/escape.txt",
        "~/proj/plots/../../secret/key.txt",
        "/etc",
    ],
)
async def test_outside_roots_rejected(env, path):
    service, _, _, _ = env
    with pytest.raises(MuxroomError) as err:
        await service.stat("local", path)
    assert err.value.code in ("path_outside_roots", "file_not_found")
    if not path.startswith("/etc"):
        assert err.value.code == "path_outside_roots"


async def test_symlinked_directory_inside_roots_resolves(env):
    service, tree, _, _ = env
    info = await service.stat("local", "~/proj/linked-root/fig.png")
    assert info.path == str(tree / "home" / "proj" / "plots" / "fig.png")


async def test_resolve_relative_from_session_cwd(env):
    service, tree, session, _ = env
    cwd, info = await service.resolve_relative("local", session.id, "plots/fig.pdf")
    assert cwd == str(tree / "home" / "proj")
    assert info.preview == "pdf"
    with pytest.raises(MuxroomError) as err:
        await service.resolve_relative("local", session.id, "../secret/key.txt")
    assert err.value.code == "path_outside_roots"
    with pytest.raises(MuxroomError):
        await service.resolve_relative("local", session.id, "/etc/passwd")


async def test_open_inline_preview_and_magic(env):
    service, _, _, _ = env
    opened = await service.open_read("local", "~/proj/plots/fig.png", inline=True)
    assert opened.kind == "png"
    assert await collect(opened.chunks) == PNG
    with pytest.raises(MuxroomError) as err:
        await service.open_read("local", "~/proj/plots/fake.png", inline=True)
    assert err.value.code == "preview_unsupported"
    with pytest.raises(MuxroomError) as err:
        await service.open_read("local", "~/proj/plots/notes.txt", inline=True)
    assert err.value.code == "preview_unsupported"
    opened = await service.open_read("local", "~/proj/plots/fake.png", inline=False)
    assert await collect(opened.chunks) == b"<html>not a png</html>"


async def test_size_limits_checked_before_streaming(env):
    service, _, _, _ = env
    with pytest.raises(MuxroomError) as err:
        await service.open_read("local", "~/proj/plots/big.bin", inline=False)
    assert err.value.code == "file_too_large"


async def test_directory_is_not_a_file(env):
    service, _, _, _ = env
    with pytest.raises(MuxroomError) as err:
        await service.open_read("local", "~/proj/plots", inline=False)
    assert err.value.code == "not_a_file"
    with pytest.raises(MuxroomError) as err:
        await service.list("local", "~/proj/plots/notes.txt")
    assert err.value.code == "not_a_directory"


async def test_change_during_read_aborts(env):
    service, tree, _, _ = env
    target = tree / "home" / "proj" / "plots" / "grow.bin"
    target.write_bytes(b"a" * 600_000)
    opened = await service.open_read("local", "~/proj/plots/grow.bin", inline=False)
    with target.open("ab") as handle:
        handle.write(b"b" * 600_000)
    with pytest.raises(MuxroomError) as err:
        await collect(opened.chunks)
    assert err.value.code == "file_changed"


async def body(*parts: bytes):
    for part in parts:
        yield part


async def test_upload_atomic_and_overwrite(env):
    service, tree, _, _ = env
    incoming = tree / "home" / "proj" / "incoming"
    info = await service.upload(
        "local", "~/proj/incoming", "data.csv", body(b"a,b\n", b"1,2\n"), overwrite=False
    )
    assert (incoming / "data.csv").read_bytes() == b"a,b\n1,2\n"
    assert info.size == 8
    with pytest.raises(MuxroomError) as err:
        await service.upload("local", "~/proj/incoming", "data.csv", body(b"x"), overwrite=False)
    assert err.value.code == "file_exists"
    await service.upload("local", "~/proj/incoming", "data.csv", body(b"new"), overwrite=True)
    assert (incoming / "data.csv").read_bytes() == b"new"
    assert sorted(p.name for p in incoming.iterdir()) == ["data.csv"]


async def test_upload_too_large_leaves_nothing(env, tree):
    service, _, _, _ = env
    service.limits = FileLimits(upload_max_mb=1)
    with pytest.raises(MuxroomError) as err:
        await service.upload(
            "local",
            "~/proj/incoming",
            "big.bin",
            body(b"0" * 600_000, b"0" * 600_000),
            overwrite=False,
        )
    assert err.value.code == "file_too_large"
    assert list((tree / "home" / "proj" / "incoming").iterdir()) == []


async def test_upload_aborted_stream_leaves_nothing(env, tree):
    service, _, _, _ = env

    async def broken():
        yield b"partial"
        raise ConnectionResetError("client went away")

    with pytest.raises(ConnectionResetError):
        await service.upload("local", "~/proj/incoming", "x.bin", broken(), overwrite=False)
    assert list((tree / "home" / "proj" / "incoming").iterdir()) == []


@pytest.mark.parametrize(
    ("directory", "name", "code"),
    [
        ("~/proj/plots", "a.txt", "upload_not_allowed"),
        ("~/proj/incoming", "../a.txt", "invalid_name"),
        ("~/proj/incoming", ".muxroom-upload-x", "invalid_name"),
    ],
)
async def test_upload_rejections(env, directory, name, code):
    service, _, _, _ = env
    with pytest.raises(MuxroomError) as err:
        await service.upload("local", directory, name, body(b"x"), overwrite=False)
    assert err.value.code == code


# ------------------------------------------------------------------ SFTP specifics


async def test_sftp_permission_denied_and_handle_cleanup(tree):
    client = FakeSftpClient(str(tree / "home"))

    async def factory():
        async def closer():
            client.exit()

        return client, closer

    service, _, _ = make_service(tree, SftpFiles("local", factory))
    client.denied.add(str(tree / "home" / "proj" / "plots" / "notes.txt"))
    with pytest.raises(MuxroomError) as err:
        await service.stat("local", "~/proj/plots/notes.txt")
    assert err.value.code == "permission_denied"

    client.drop_after_reads = 1
    target = tree / "home" / "proj" / "plots" / "medium.bin"
    target.write_bytes(b"m" * 700_000)
    for _ in range(20):
        opened = await service.open_read("local", "~/proj/plots/medium.bin", inline=False)
        with pytest.raises(MuxroomError) as err:
            await collect(opened.chunks)
        assert err.value.code == "host_unreachable"
    assert client.open_handles == 0

    client.drop_after_reads = None
    for _ in range(20):
        opened = await service.open_read("local", "~/proj/plots/medium.bin", inline=False)
        await opened.chunks.__anext__()
        await opened.chunks.aclose()  # client disconnect mid-download
    assert client.open_handles == 0


async def test_sftp_reconnects_after_drop(tree):
    clients = []

    async def factory():
        client = FakeSftpClient(str(tree / "home"))
        clients.append(client)

        async def closer():
            client.exit()

        return client, closer

    files = SftpFiles("local", factory)
    assert await files.home() == str(tree / "home")

    original = clients[0].stat

    async def flaky(path):
        clients[0].stat = original
        raise asyncssh_lost()

    import asyncssh

    def asyncssh_lost():
        return asyncssh.SFTPConnectionLost("gone")

    clients[0].stat = flaky
    stat = await files.stat(str(tree / "home"))
    assert stat.kind == "dir"
    assert len(clients) == 2 and clients[0].exited


def test_no_event_loop_leak():
    assert asyncio.get_event_loop_policy() is not None


async def test_outside_roots_error_lists_allowed_folders(env):
    service, tree, _, _ = env
    with pytest.raises(MuxroomError) as err:
        await service.stat("local", "~/proj2/x.txt")
    message = err.value.message
    assert f"Allowed: ~/proj (→ {tree / 'home' / 'proj'})" in message
    assert "Reload config" in err.value.hint and "files.read_roots" in err.value.hint
    with pytest.raises(MuxroomError) as err:
        await service.upload("local", "~/proj/plots", "a.txt", body(b"x"), overwrite=False)
    assert (
        f"Allowed: ~/proj/incoming (→ {tree / 'home' / 'proj' / 'incoming'})" in err.value.message
    )


def test_describe_roots():
    from muxroom.files.policy import describe_roots

    assert describe_roots([]) == "none configured"
    assert describe_roots([("/a", "/a"), ("/temp/x", "/real/x"), ("~/gone", None)]) == (
        "/a, /temp/x (→ /real/x), ~/gone (does not exist)"
    )


async def test_completion_relative_to_session_directory(env):
    service, tree, session, _ = env
    result = await service.complete("local", "", session_id=session.id)
    assert result.cwd == str(tree / "home" / "proj")
    assert [i.value for i in result.items] == ["incoming/", "linked-root/", "plots/"]

    result = await service.complete("local", "plots/fi", session_id=session.id)
    assert [i.value for i in result.items] == ["plots/fig.pdf", "plots/fig.png", "plots/fig.svg"]
    assert {i.preview for i in result.items} == {"pdf", "png", "svg"}

    names = [
        i.name for i in (await service.complete("local", "plots/", session_id=session.id)).items
    ]
    assert "notes.txt" not in names and "big.bin" not in names  # not previewable
    assert "escape" not in names and "broken" not in names  # outside roots / dangling
    assert "inside" in names  # symlink to a PNG inside the roots


async def test_completion_absolute_and_home_paths(env):
    service, tree, _, _ = env
    result = await service.complete("local", "~/proj/pl", session_id=None)
    assert [i.value for i in result.items] == ["~/proj/plots/"]
    absolute = str(tree / "home" / "proj" / "plots") + "/FIG.P"
    result = await service.complete("local", absolute, session_id=None)
    assert [i.name for i in result.items] == ["fig.pdf", "fig.png"]  # case-insensitive


async def test_completion_hidden_files_only_when_asked(env):
    service, tree, session, _ = env
    (tree / "home" / "proj" / "plots" / ".draft.png").write_bytes(PNG)
    plain = await service.complete("local", "plots/", session_id=session.id)
    assert ".draft.png" not in [i.name for i in plain.items]
    dotted = await service.complete("local", "plots/.", session_id=session.id)
    assert [i.name for i in dotted.items] == [".draft.png"]


@pytest.mark.parametrize("text", ["../", "../secret/", "/etc/", "~/"])
async def test_completion_respects_roots(env, text):
    service, _, session, _ = env
    with pytest.raises(MuxroomError) as err:
        await service.complete("local", text, session_id=session.id)
    assert err.value.code in ("path_outside_roots", "file_not_found")


async def test_completion_relative_needs_session(env):
    service, _, _, _ = env
    with pytest.raises(MuxroomError) as err:
        await service.complete("local", "plots/", session_id=None)
    assert err.value.code == "invalid_path"

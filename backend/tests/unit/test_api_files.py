import os
from pathlib import Path

import httpx
import pytest

from muxroom.api.app import create_app
from muxroom.config import AppConfig, FileLimits, HostConfig, HostFiles
from muxroom.transport.local_files import LocalFiles
from tests.fakes import FakeTmuxTransport
from tests.unit.test_files import PDF, PNG, SVG

TOKEN = "t" * 43
HOST = "127.0.0.1:8765"
ORIGIN = f"http://{HOST}"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {"Origin": ORIGIN}


@pytest.fixture
def home(tmp_path) -> Path:
    home = Path(os.path.realpath(tmp_path)) / "home"
    plots = home / "proj" / "plots"
    plots.mkdir(parents=True)
    (home / "proj" / "incoming").mkdir()
    (plots / "fig.png").write_bytes(PNG)
    (plots / "fig.pdf").write_bytes(PDF)
    (plots / "evil.svg").write_bytes(SVG)
    (plots / "fake.svg").write_bytes(b"<html><script>alert(1)</script></html>")
    (plots / "page.html").write_bytes(b"<script>alert(1)</script>")
    (plots / "big.bin").write_bytes(b"0" * (1024 * 1024 + 1))
    return home


@pytest.fixture
def fake(home):
    # No tmux sessions and tmux not even installed: file ops must not care.
    return FakeTmuxTransport(
        home=str(home), dirs=[str(home)], tmux_installed=False, files=LocalFiles(home=str(home))
    )


@pytest.fixture
async def client(fake):
    host = HostConfig(
        id="local",
        allowed_roots=["~"],
        files=HostFiles(read_roots=["~/proj"], upload_roots=["~/proj/incoming"]),
    )
    config = AppConfig(
        hosts=[host], files=FileLimits(preview_max_mb=1, download_max_mb=1, upload_max_mb=1)
    )
    app = create_app(config, token=TOKEN, transport_factory=lambda _h: fake)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}", headers=AUTH) as c:
        yield c


async def test_roots_and_list(client, home):
    roots = (await client.get("/api/hosts/local/files/roots")).json()
    assert roots == [{"path": str(home / "proj"), "writable": False}]
    listing = (await client.get("/api/hosts/local/files/list", params={"path": "~/proj"})).json()
    assert listing["parent"] is None
    assert [e["name"] for e in listing["entries"]] == ["incoming", "plots"]


async def test_png_preview_headers(client):
    r = await client.get(
        "/api/hosts/local/files/content",
        params={"path": "~/proj/plots/fig.png", "disposition": "inline"},
    )
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["etag"].strip('"').endswith(f"-{len(PNG)}")
    assert r.headers["content-security-policy"].startswith("sandbox")


async def test_svg_is_sandboxed(client):
    r = await client.get(
        "/api/hosts/local/files/content",
        params={"path": "~/proj/plots/evil.svg", "disposition": "inline"},
    )
    assert r.headers["content-type"].startswith("image/svg+xml")
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "default-src 'none'" in csp
    assert "script-src" not in csp


async def test_pdf_preview(client):
    r = await client.get(
        "/api/hosts/local/files/content",
        params={"path": "~/proj/plots/fig.pdf", "disposition": "inline"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")


@pytest.mark.parametrize("name", ["fake.svg", "page.html"])
async def test_non_previewable_inline_rejected(client, name):
    r = await client.get(
        "/api/hosts/local/files/content",
        params={"path": f"~/proj/plots/{name}", "disposition": "inline"},
    )
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "preview_unsupported"


async def test_download_is_attachment(client):
    r = await client.get(
        "/api/hosts/local/files/content", params={"path": "~/proj/plots/page.html"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-security-policy"].startswith("sandbox")


async def test_too_large_is_413_before_body(client):
    r = await client.get("/api/hosts/local/files/content", params={"path": "~/proj/plots/big.bin"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "file_too_large"


async def test_outside_roots_403(client):
    r = await client.get("/api/hosts/local/files/list", params={"path": "~"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "path_outside_roots"


async def test_upload_roundtrip(client, home):
    params = {"dir": "~/proj/incoming", "name": "data.csv"}
    r = await client.put(
        "/api/hosts/local/files/content", params=params, content=b"a,b\n", headers=WRITE
    )
    assert r.status_code == 201, r.text
    assert (home / "proj" / "incoming" / "data.csv").read_bytes() == b"a,b\n"
    again = await client.put(
        "/api/hosts/local/files/content", params=params, content=b"x", headers=WRITE
    )
    assert again.status_code == 409
    over = await client.put(
        "/api/hosts/local/files/content",
        params={**params, "overwrite": "true"},
        content=b"x",
        headers=WRITE,
    )
    assert over.status_code == 201


async def test_upload_limits_and_roots(client):
    big = await client.put(
        "/api/hosts/local/files/content",
        params={"dir": "~/proj/incoming", "name": "big"},
        content=b"0" * (1024 * 1024 + 1),
        headers=WRITE,
    )
    assert big.status_code == 413
    outside = await client.put(
        "/api/hosts/local/files/content",
        params={"dir": "~/proj/plots", "name": "x"},
        content=b"x",
        headers=WRITE,
    )
    assert outside.status_code == 403
    assert outside.json()["error"]["code"] == "upload_not_allowed"


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.example"}])
async def test_upload_requires_origin_and_csrf(client, headers):
    r = await client.put(
        "/api/hosts/local/files/content",
        params={"dir": "~/proj/incoming", "name": "x"},
        content=b"x",
        headers=headers,
    )
    assert r.status_code == 403


async def test_file_endpoints_require_auth(client):
    client.headers.pop("Authorization")
    for url in ("/api/hosts/local/files/roots", "/api/hosts/local/files/content?path=~/proj"):
        assert (await client.get(url)).status_code == 401


async def test_listing_provides_signed_urls_usable_without_header(client):
    listing = (
        await client.get("/api/hosts/local/files/list", params={"path": "~/proj/plots"})
    ).json()
    by_name = {e["name"]: e for e in listing["entries"]}
    png = by_name["fig.png"]
    assert png["preview_url"] and png["download_url"]
    assert by_name["page.html"]["preview_url"] is None
    client.headers.pop("Authorization")
    r = await client.get(png["preview_url"])
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert (
        (await client.get(png["download_url"]))
        .headers["content-disposition"]
        .startswith("attachment")
    )


async def test_signed_urls_cannot_be_tampered_or_reused(client):
    info = (
        await client.get("/api/hosts/local/files/stat", params={"path": "~/proj/plots/fig.png"})
    ).json()
    url = info["preview_url"]
    client.headers.pop("Authorization")
    tampered = [
        url.replace("fig.png", "fig.pdf"),
        url.replace("disposition=inline", "disposition=attachment"),
        url.replace("sig=", "sig=0"),
        url.replace("/hosts/local/", "/hosts/other/"),
    ]
    for bad in tampered:
        assert (await client.get(bad)).status_code in (401, 404), bad
    # a content ticket must not unlock any other endpoint
    query = url.split("?", 1)[1]
    assert (await client.get(f"/api/hosts/local/files/list?{query}")).status_code == 401
    assert (
        await client.put(
            f"/api/hosts/local/files/content?{query}&dir=~/proj/incoming&name=x",
            content=b"x",
            headers={"Origin": ORIGIN},
        )
    ).status_code == 401


async def test_expired_signed_url_rejected(client, monkeypatch):
    import muxroom.api.security as sec

    info = (
        await client.get("/api/hosts/local/files/stat", params={"path": "~/proj/plots/fig.png"})
    ).json()
    client.headers.pop("Authorization")
    real_time = sec.time.time
    monkeypatch.setattr(sec.time, "time", lambda: real_time() + sec.TICKET_SECONDS + 5)
    assert (await client.get(info["preview_url"])).status_code == 401


async def test_pdf_preview_can_be_framed_by_the_ui_only(client):
    r = await client.get(
        "/api/hosts/local/files/content",
        params={"path": "~/proj/plots/fig.pdf", "disposition": "inline"},
    )
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["content-security-policy"] == "frame-ancestors 'self'"

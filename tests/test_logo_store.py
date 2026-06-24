"""Tests for the provider-logo cache (#2b).

The download path is guarded but not exercised against the network here; we test
the pure guards, the slug sanitization, and the main-thread normalize/cache,
which is where the real logic (and risk) lives. APPDATA is redirected to a temp
dir by conftest, so the cache is per-test isolated.
"""
import pytest

from logo_store import LogoStore, download_logo, _safe_slug


def _png_bytes(color="#ff0000", n=12):
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QBuffer, QByteArray
    img = QImage(n, n, QImage.Format.Format_ARGB32)
    img.fill(QColor(color))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
       b'<rect width="10" height="10" fill="#22cc55"/></svg>')


# ---- guards ----
def test_download_logo_rejects_non_http_schemes():
    # These return None at the scheme check BEFORE any network call.
    assert download_logo("file:///etc/passwd") is None
    assert download_logo("ftp://host/x.png") is None
    assert download_logo("") is None


def test_safe_slug_strips_path_separators_and_dots():
    for hostile in ("../../etc/passwd", "a/b\\c", "..", "////", "x/../../y", "."):
        s = _safe_slug(hostile)
        assert "/" not in s and "\\" not in s and s   # no traversal, non-empty
    # A pure-dots slug collapses to the safe fallback (can't become '..').
    assert _safe_slug("..") == "x"
    assert _safe_slug(".") == "x"


def test_download_logo_blocks_private_and_metadata_hosts():
    """SSRF guard: loopback / link-local (cloud-metadata) hosts are rejected
    BEFORE any socket is opened. IP literals need no DNS, so this is offline."""
    assert download_logo("http://127.0.0.1/x.png") is None
    assert download_logo("http://169.254.169.254/latest/meta-data/") is None
    assert download_logo("http://[::1]/x.png") is None
    assert download_logo("http://10.0.0.5/logo.png") is None


def test_host_is_public_classifies_ip_literals():
    from logo_store import _host_is_public
    assert _host_is_public("127.0.0.1") is False
    assert _host_is_public("169.254.169.254") is False
    assert _host_is_public("192.168.1.1") is False
    assert _host_is_public("8.8.8.8") is True       # public IP literal, no DNS


def test_receive_rejects_svg_with_dtd_entity(qapp):
    """Billion-laughs / XXE guard: SVG carrying an inline DTD entity is dropped."""
    store = LogoStore()
    bomb = (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY lol "lol">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')
    assert store.receive("bomb", bomb, True) is False
    assert store.tile_path("bomb") is None


# ---- normalize + cache ----
def test_receive_caches_png_tile_and_emits_ready(qapp):
    store = LogoStore()
    got = []
    store.ready.connect(got.append)
    assert store.receive("anthropic", _png_bytes(), False) is True
    assert got == ["anthropic"]
    assert store.tile_path("anthropic") is not None
    html = store.tile_html("anthropic", px=40)
    assert html.startswith("<img") and "file:///" in html and 'width="40"' in html


def test_receive_normalizes_svg(qapp):
    store = LogoStore()
    assert store.receive("z-ai", SVG, True) is True
    assert store.tile_path("z-ai") is not None


def test_receive_garbage_marks_failed_and_suppresses_retry(qapp):
    store = LogoStore()
    assert store.receive("bad", b"not an image at all", False) is False
    assert store.tile_path("bad") is None
    emitted = []
    store.needs_fetch.connect(lambda s, u: emitted.append(s))
    store.request("bad", "https://x/y.png")   # already failed → no re-request
    assert emitted == []


def test_cached_slug_does_not_escape_cache_dir(qapp):
    store = LogoStore()
    store.receive("../../evil", _png_bytes(), False)
    path = store.tile_path("../../evil")
    assert path is not None
    # The written file stays inside the cache directory.
    assert str(store._dir) in path


# ---- request lifecycle ----
def test_request_emits_once_then_suppresses_while_pending(qapp):
    store = LogoStore()
    emitted = []
    store.needs_fetch.connect(lambda s, u: emitted.append((s, u)))
    store.request("p", "https://x/p.png")
    store.request("p", "https://x/p.png")   # pending → suppressed
    assert emitted == [("p", "https://x/p.png")]


def test_request_ignores_empty_inputs(qapp):
    store = LogoStore()
    emitted = []
    store.needs_fetch.connect(lambda s, u: emitted.append(s))
    store.request("", "https://x")
    store.request("p", "")
    assert emitted == []


def test_request_suppressed_when_already_cached(qapp):
    store = LogoStore()
    store.receive("p", _png_bytes(), False)
    emitted = []
    store.needs_fetch.connect(lambda s, u: emitted.append(s))
    store.request("p", "https://x/p.png")
    assert emitted == []


def test_tile_html_none_when_not_cached(qapp):
    store = LogoStore()
    assert store.tile_html("never") is None
    assert store.tile_path("never") is None

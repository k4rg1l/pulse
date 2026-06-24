"""
OpenRouter Pulse — provider logo cache  (feature #2b)

Real provider logos for the Custody Dossier. The flow is split so no network
I/O ever touches the GUI thread and no Qt image op ever touches a worker thread:

* :func:`download_logo` (pure ``requests``, no Qt) runs on the API worker —
  it fetches the raw image bytes with conservative guards.
* :class:`LogoStore` (Qt, main thread) normalizes those bytes into a small,
  uniform PNG "app-icon" tile on a light background (so monochrome logos read
  on the dark dossier) and caches it to ``%APPDATA%/Pulse/provider_logos``.

Logos arrive in every format (svg / webp / png / external favicons); they're
all coerced to one cached PNG so the QLabel rich-text ``<img>`` only ever
references a PNG. A provider with no usable logo falls back to a monogram chip.
"""
import html
import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, Signal, QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QColor, QPainterPath, QBrush

from persistence import state_dir

log = logging.getLogger("pulse.logos")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Pulse/1.0"
ALLOWED_SCHEMES = ("http", "https")
MAX_LOGO_BYTES = 2_000_000          # hard cap on a downloaded logo (compressed)
MAX_DECODED_PIXELS = 25_000_000     # raster decompression-bomb guard (~5000²)
TILE_PX = 48
TILE_BG = "#eef0f6"                  # light tile so dark/monochrome logos show

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def _safe_slug(slug: str) -> str:
    """Sanitize a provider slug into a cache filename. Strict allowlist; also
    strips leading/trailing dots so a slug like '..' can't resolve to a parent
    directory — the result is always a single, in-directory filename stem."""
    return _UNSAFE.sub("_", (slug or "").lower()).strip("_.") or "x"


def _host_is_public(host: str) -> bool:
    """True only if EVERY address `host` resolves to is a public IP — blocks
    SSRF to loopback/private/link-local (incl. cloud-metadata 169.254.169.254).
    (DNS rebinding between this check and the socket connect is a known residual
    risk, accepted for a local desktop app fetching from OpenRouter's CDN.)"""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def download_logo(url: str, session: Optional[requests.Session] = None):
    """Worker-thread: fetch a logo's raw bytes. Returns ``(bytes, is_svg)`` or
    ``None``. NO Qt here. The logo url comes from OpenRouter's API but is treated
    as untrusted: http(s) only, public-host only, NO redirects (an SSRF guard),
    a hard size cap, a timeout, and an image content-type check."""
    try:
        parts = urlparse(url)
        if parts.scheme not in ALLOWED_SCHEMES or not parts.hostname:
            return None
        if not _host_is_public(parts.hostname):
            return None
        sess = session or requests.Session()
        # No redirects: a 3xx to http://169.254.169.254/… would otherwise slip
        # past the host check above. Every real provider logo serves 200 direct.
        r = sess.get(url, timeout=10, stream=True, allow_redirects=False,
                     headers={"User-Agent": USER_AGENT})
        if r.is_redirect or 300 <= r.status_code < 400:
            return None
        r.raise_for_status()
        ct = r.headers.get("content-type", "").lower()
        buf = bytearray()
        for chunk in r.iter_content(8192):
            buf += chunk
            if len(buf) > MAX_LOGO_BYTES:
                log.warning("logo over size cap, dropped: %s", url)
                return None
        if not buf:
            return None
        is_svg = url.lower().split("?")[0].endswith(".svg") or "svg" in ct
        if not (is_svg or ct.startswith("image/") or ct == ""):
            return None
        return bytes(buf), is_svg
    except Exception:
        log.warning("logo download failed: %s", url, exc_info=True)
        return None


class LogoStore(QObject):
    """Main-thread cache of normalized provider-logo tiles."""

    needs_fetch = Signal(str, str)   # (slug, url) — a download is wanted
    ready = Signal(str)              # slug — a normalized tile is now cached

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self._dir = state_dir() / "provider_logos"
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.exception("logo cache dir setup failed")
            self._dir = None
        self._pending = set()
        self._failed = set()

    def _path(self, slug):
        if self._dir is None:
            return None
        return self._dir / f"{_safe_slug(slug)}.png"

    def tile_path(self, slug) -> Optional[str]:
        p = self._path(slug)
        if p is not None and p.exists():
            return str(p)
        return None

    def tile_html(self, slug, px=40) -> Optional[str]:
        """An ``<img>`` for the cached tile, or None if not cached yet."""
        p = self.tile_path(slug)
        if not p:
            return None
        # html.escape + double-quote the src so an unusual cache path (e.g. a
        # Windows username with an apostrophe) can't break out of the attribute.
        url = html.escape("file:///" + str(p).replace("\\", "/"), quote=True)
        return f'<img src="{url}" width="{int(px)}" height="{int(px)}">'

    def request(self, slug, url):
        """Ask for a logo if we don't already have (or have given up on) it.
        Idempotent — safe to call from pre-warm and on dossier open."""
        if not slug or not url:
            return
        if self.tile_path(slug) or slug in self._pending or slug in self._failed:
            return
        self._pending.add(slug)
        self.needs_fetch.emit(slug, url)

    def receive(self, slug, data, is_svg) -> bool:
        """Main-thread: normalize raw bytes → tile PNG. Emits ``ready`` on
        success; remembers failures so we don't retry a broken logo forever."""
        self._pending.discard(slug)
        img = self._render_tile(bytes(data) if data else b"", bool(is_svg))
        p = self._path(slug)
        # Defense-in-depth: never write outside the cache dir even if _safe_slug
        # somehow let something through.
        if p is not None and self._dir is not None:
            try:
                p.resolve().relative_to(self._dir.resolve())
            except ValueError:
                p = None
        if img is None or p is None or not img.save(str(p), "PNG"):
            self._failed.add(slug)
            return False
        self.ready.emit(slug)
        return True

    def _render_tile(self, data, is_svg, size=TILE_PX, pad=7) -> Optional[QImage]:
        if not data:
            return None
        canvas = QImage(size, size, QImage.Format.Format_ARGB32)
        canvas.fill(0)
        p = QPainter(canvas)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            tile = QPainterPath()
            tile.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
            p.fillPath(tile, QBrush(QColor(TILE_BG)))
            inner = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
            if is_svg:
                # Reject inline DTD entities (billion-laughs / XXE vector) before
                # handing untrusted SVG to the parser.
                if b"<!entity" in data.lower():
                    return None
                from PySide6.QtSvg import QSvgRenderer
                rnd = QSvgRenderer(QByteArray(data))
                if not rnd.isValid():
                    return None
                rnd.render(p, inner)
            else:
                img = QImage.fromData(QByteArray(data))
                if img.isNull():
                    return None
                # Decompression-bomb guard: the byte cap bounds the *compressed*
                # size, not the decoded pixel buffer.
                if img.width() * img.height() > MAX_DECODED_PIXELS:
                    return None
                img = img.scaled(int(inner.width()), int(inner.height()),
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                p.drawImage(int(pad + (inner.width() - img.width()) / 2),
                            int(pad + (inner.height() - img.height()) / 2), img)
        except Exception:
            log.warning("logo normalize failed", exc_info=True)
            return None
        finally:
            p.end()
        return canvas

"""Deterministic render tests for The Ledger (per-provider Trust Seals).

These drive a headless (offscreen) QApplication and MEASURE the rendered
result — seal hit-targets, grade-letter fit, painted pixels, dossier content —
rather than eyeballing a screenshot. The pure grade logic is covered in
test_frontend_client.py; this proves the widget wiring + geometry.
"""
import json
from pathlib import Path

import pytest

from api_client import EndpointInfo, ModelEndpoints
from frontend_client import parse_all_providers, custody_score

FIX = Path(__file__).parent / "fixtures"


def _book():
    return parse_all_providers(json.loads(
        (FIX / "fe_all_providers.json").read_text(encoding="utf-8"))["data"])


def _ep(name, tag):
    return EndpointInfo(provider_name=name, tag=tag, latency_p50=900.0,
                        uptime_last_30m=100.0, throughput_p50=50.0,
                        pricing_prompt=1e-6, pricing_completion=5e-6)


def _card(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("anthropic/claude-opus-4.8")
    card.set_endpoints(ModelEndpoints(
        model_id="anthropic/claude-opus-4.8", model_name="Claude Opus 4.8",
        endpoints=[_ep("Anthropic", "anthropic"),
                   _ep("DeepSeek", "deepseek"),
                   _ep("Moonshot AI", "moonshotai")]))
    card.set_provider_trust(_book())
    card.resize(560, card.height())
    return card


def _render(card):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    img = QImage(card.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    card.render(p, QPoint(0, 0))
    p.end()
    return img


# ---------------------------------------------------------------------------
def test_each_graded_provider_gets_a_seal_hit_target(qapp):
    card = _card(qapp)
    _render(card)
    idents = [ident for _r, ident, _c in card._seal_hits]
    assert idents == ["anthropic", "deepseek", "moonshotai"]


def test_seal_colors_match_the_computed_grade(qapp):
    card = _card(qapp)
    _render(card)
    by_ident = {ident: color for _r, ident, color in card._seal_hits}
    book = _book()
    assert by_ident["anthropic"] == custody_score(book.lookup(slug="anthropic")).color
    assert by_ident["deepseek"] == custody_score(book.lookup(slug="deepseek")).color
    # DeepSeek trains → F → red; Moonshot is spotless → S → mint. Distinct.
    assert by_ident["deepseek"] != by_ident["moonshotai"]


def test_grade_letter_fits_inside_the_seal_box(qapp):
    """Font-metric guarantee: every grade glyph fits the 14px seal slot, so
    nothing clips at the app's default font."""
    from PySide6.QtGui import QFont, QFontMetrics
    from theme import Fonts
    from widgets import PinnedModelCard
    f = QFont(Fonts.tiny())
    f.setBold(True)
    f.setPointSize(8)
    fm = QFontMetrics(f)
    for letter in "SABCDF":
        assert fm.horizontalAdvance(letter) <= PinnedModelCard.SEAL_W


def test_seal_actually_paints_pixels(qapp):
    """The seal column is not blank: the shield tints the card background."""
    from theme import Colors
    card = _card(qapp)
    img = _render(card)
    # First provider row seal center (see PinnedModelCard row geometry).
    rect = card._seal_hits[0][0]
    cx, cy = int(rect.center().x()), int(rect.center().y())
    seal_px = img.pixelColor(cx, cy)
    bg = Colors.BG_CARD
    assert (seal_px.red(), seal_px.green(), seal_px.blue()) != (bg.red(), bg.green(), bg.blue())


def test_seal_hit_test_maps_click_to_provider(qapp):
    card = _card(qapp)
    _render(card)
    rect, ident, _c = card._seal_hits[1]   # DeepSeek row
    _r2, hit_ident = card._seal_at(rect.center())
    assert hit_ident == ident == "deepseek"


def test_dossier_html_is_an_auditable_rap_sheet(qapp):
    card = _card(qapp)
    _render(card)
    html = card.dossier_html("anthropic")
    assert "Custody Score" in html
    assert "82/100" in html or "82<" in html       # the computed score shows
    assert "Retains prompts 30 days" in html        # a real penalty line
    assert "-6" in html                              # its deduction
    assert "Never trains on your prompts" in html    # a green positive
    # Jurisdiction trail present.
    assert "HQ" in html and "US" in html


def test_dossier_for_a_trainer_shows_the_cardinal_sin(qapp):
    card = _card(qapp)
    _render(card)
    html = card.dossier_html("deepseek")
    assert "Trains on your prompts" in html
    assert "-45" in html
    g = custody_score(_book().lookup(slug="deepseek"))
    assert f"{g.score}/100 · F" in html


def test_dossier_accent_is_the_grade_color(qapp):
    card = _card(qapp)
    _render(card)
    book = _book()
    assert card.dossier_accent("moonshotai") == custody_score(book.lookup(slug="moonshotai")).color
    assert card.dossier_accent("nonexistent") == "#00d2ff"   # safe fallback


def test_info_popup_table_gains_a_trust_column(qapp):
    card = _card(qapp)
    _render(card)
    html = card.provider_html()
    assert "TRUST" in html
    # Anthropic's B and DeepSeek's F both appear as colored grade letters.
    assert ">B<" in html and ">F<" in html


def test_safe_color_whitelists_hex_only(qapp):
    from widgets import _safe_color
    assert _safe_color("#7cf5c4") == "#7cf5c4"
    assert _safe_color("#abc") == "#abc"
    assert _safe_color("red;'><script>") == "#a0a0c8"     # CSS-injection attempt
    assert _safe_color(None) == "#a0a0c8"


def test_dossier_sanitizes_a_hostile_grade(qapp):
    """Defense-in-depth: even if a CustodyGrade carried attacker-controlled
    color/grade strings, the dossier must not emit them raw into the QLabel."""
    from frontend_client import CustodyGrade
    card = _card(qapp)
    _render(card)
    p = _book().lookup(slug="anthropic")
    hostile = CustodyGrade(score=50, grade="<img src=x onerror=alert(1)>",
                           color="javascript:alert(1)", penalties=[], positives=[])
    card._trust_for_ep = lambda ep: (p, hostile)
    html = card.dossier_html("anthropic")
    assert "<img" not in html            # grade was html-escaped
    assert "javascript:" not in html     # color was rejected by the whitelist


def test_dossier_embeds_real_logo_when_cached(qapp):
    """#2b: the dossier header shows the cached logo tile; providers without a
    cached logo fall back cleanly to the monogram chip."""
    from logo_store import LogoStore
    from tests.test_logo_store import _png_bytes
    store = LogoStore()
    store.receive("anthropic", _png_bytes(), False)   # cache only anthropic
    card = _card(qapp)
    card.set_logo_store(store)
    _render(card)
    anthropic = card.dossier_html("anthropic")
    assert "<img" in anthropic and "file:///" in anthropic
    deepseek = card.dossier_html("deepseek")          # not cached → monogram
    assert "<img" not in deepseek


def test_logos_needed_lists_providers_with_icon_urls(qapp):
    card = _card(qapp)
    _render(card)
    needed = dict(card.logos_needed())
    assert "anthropic" in needed and needed["anthropic"].startswith("http")
    assert "deepseek" in needed


def test_provider_slug_for_ident_round_trips(qapp):
    card = _card(qapp)
    _render(card)
    assert card.provider_slug_for("deepseek") == "deepseek"
    assert card.provider_slug_for("nonexistent") is None


def test_no_seals_without_trust_data_keeps_classic_star(qapp):
    """When trust data is absent, the card falls back cleanly (no crash, no
    seals) — the board still works for every provider/model."""
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y",
                                      endpoints=[_ep("Anthropic", "anthropic")]))
    card.resize(560, card.height())
    _render(card)
    assert card._seal_hits == []

"""F1 — the widened EndpointInfo pricing parse (shared foundation, primary
consumer #6 THE WATERLINE).

Asserts the parse loop now CARRIES the full pricing object on each EndpointInfo
(cache r/w, web_search, image, audio, internal_reasoning, discount) instead of
dropping it, and — the load-bearing invariant — that the public route's SPARSE
omission is preserved: an ABSENT key stays absent (None / not-in-dict), it is
NOT coerced to 0.0. A 0.0 would falsely signal "this fee applies" to #6.

Pure (no Qt, no network): drives parse_model_endpoints against captured fixtures
(one full-multimodal, one sparse).
"""
import json
from pathlib import Path

from api_client import (
    ModelInfo, parse_model_endpoints, _parse_pricing_extra,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
def test_full_multimodal_carries_every_pricing_field():
    me = parse_model_endpoints("google/gemini-2.5-pro",
                               _load("endpoints_full_multimodal.json"))
    assert len(me.endpoints) == 2
    ep = me.endpoints[0]
    # prompt/completion still float()d into explicit fields
    assert ep.pricing_prompt == 1.25e-6
    assert ep.pricing_completion == 1e-5
    # every extended key retained AND float()d off its string
    px = ep.pricing_extra
    assert px["image"] == 1.25e-6
    assert px["audio"] == 1.25e-6
    assert px["input_audio_cache"] == 1.25e-7
    assert px["web_search"] == 0.014               # $/call
    assert px["internal_reasoning"] == 1e-5
    assert px["input_cache_read"] == 1.25e-7
    assert px["input_cache_write"] == 3.75e-7
    # discount is numeric upstream (not a string) and still parses
    assert px["discount"] == 0.0
    # the convenience accessors
    assert ep.fee("web_search") == 0.014
    assert ep.has_fee("web_search") is True
    assert ep.has_fee("input_cache_read") is True


def test_discount_value_is_carried_when_nonzero():
    me = parse_model_endpoints("google/gemini-2.5-pro",
                               _load("endpoints_full_multimodal.json"))
    vertex = me.endpoints[1]
    assert vertex.pricing_extra["discount"] == 0.25


def test_sparse_payload_leaves_absent_keys_absent_not_zero():
    """The load-bearing F1 invariant: the public route omits zero-value keys, so
    an omitted fee must be ABSENT (None), never silently 0.0 — else #6 would read
    a phantom fee."""
    me = parse_model_endpoints("openai/gpt-4o", _load("endpoints_sparse.json"))
    azure, openai = me.endpoints
    # Azure's pricing dict had only prompt/completion/discount → cache keys ABSENT
    assert "input_cache_read" not in azure.pricing_extra
    assert azure.fee("input_cache_read") is None        # absent, NOT 0.0
    assert azure.has_fee("input_cache_read") is False
    assert "web_search" not in azure.pricing_extra
    assert azure.fee("web_search") is None
    # OpenAI's pricing carried input_cache_read → present with a real value
    assert openai.fee("input_cache_read") == 1.25e-6
    assert openai.has_fee("input_cache_read") is True
    # neither sparse endpoint invents image/audio
    for ep in (azure, openai):
        assert ep.fee("image") is None
        assert ep.fee("audio") is None
        assert ep.fee("internal_reasoning") is None


def test_explicit_zero_fee_differs_from_absent_fee():
    """A present-but-zero fee parses to 0.0 (real explicit zero); an omitted key
    stays None. has_fee() distinguishes them (only >0 counts as 'applies')."""
    present_zero = _parse_pricing_extra({"web_search": "0", "discount": 0})
    assert present_zero["web_search"] == 0.0          # present, explicitly zero
    assert "image" not in present_zero                 # omitted → absent
    # has_fee semantics: zero present is NOT "applies"
    from api_client import EndpointInfo
    ep = EndpointInfo(pricing_extra=present_zero)
    assert ep.fee("web_search") == 0.0
    assert ep.has_fee("web_search") is False           # 0 is not > 0
    assert ep.has_fee("image") is False                # absent


def test_unparseable_fee_is_treated_as_absent():
    px = _parse_pricing_extra({"web_search": "not-a-number", "image": "0.001"})
    assert "web_search" not in px                      # junk → dropped (absent)
    assert px["image"] == 0.001


def test_empty_or_missing_pricing_is_empty_dict():
    assert _parse_pricing_extra({}) == {}
    assert _parse_pricing_extra(None) == {}


def test_dead_top_provider_field_removed():
    """The dead, never-read ModelInfo.top_provider field is gone (F1 cleanup)."""
    import dataclasses
    names = {f.name for f in dataclasses.fields(ModelInfo)}
    assert "top_provider" not in names

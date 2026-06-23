"""Unit tests for the Arena benchmark parser (api_client.parse_benchmarks).

Pure-logic tests against a crafted sample mirroring the real
/api/v1/benchmarks shape — never the live endpoint.
"""
from api_client import parse_benchmarks, _tier_for, _norm_model_name


def _da(name, cat, elo, win, ts=None):
    return {
        "source": "design-arena", "display_name": name, "model_permaslug": name,
        "arena": "models", "category": cat, "elo": elo, "win_rate": win,
        "avg_generation_time_ms": 1000,
        "tournament_stats": ts or {"first_place": 0, "second_place": 0,
                                   "third_place": 0, "total": 0},
        "pricing": {"prompt": "0.00001", "completion": "0.00005"},
    }


# Three models across two categories. "Alpha" tops svg; "Gamma" is last.
SAMPLE_DA = [
    _da("Alpha One", "svg", 1400, 70.0, {"first_place": 100, "second_place": 40,
                                          "third_place": 10, "total": 200}),
    _da("Beta Two", "svg", 1300, 60.0),
    _da("Gamma Three", "svg", 1200, 50.0),
    _da("Alpha One", "website", 1280, 55.0, {"first_place": 100, "second_place": 40,
                                              "third_place": 10, "total": 200}),
    _da("Beta Two", "website", 1350, 64.0),
    _da("Gamma Three", "website", 1210, 51.0),
]
SAMPLE_AA = [
    {"source": "artificial-analysis",
     "display_name": "Alpha One (Adaptive Reasoning, Max Effort)",
     "intelligence_index": 60.0, "coding_index": 75.0, "agentic_index": 50.0},
]


def test_ranks_are_computed_per_category():
    board = parse_benchmarks(SAMPLE_DA, SAMPLE_AA)
    alpha = board.lookup("vendor/alpha-one")
    assert alpha is not None
    svg = next(s for s in alpha.standings if s.category == "svg")
    assert (svg.rank, svg.field_size) == (1, 3)
    web = next(s for s in alpha.standings if s.category == "website")
    assert (web.rank, web.field_size) == (2, 3)   # Alpha #2 in website (behind Beta)


def test_signature_is_best_percentile():
    board = parse_benchmarks(SAMPLE_DA, SAMPLE_AA)
    # Beta is #2 svg and #1 website -> signature should be the #1 (website).
    beta = board.lookup("vendor/beta-two")
    assert beta.signature.category == "website"
    assert beta.signature.rank == 1


def test_champion_tier_for_rank_one():
    board = parse_benchmarks(SAMPLE_DA, SAMPLE_AA)
    alpha = board.lookup("vendor/alpha-one")   # #1 in svg
    assert alpha.tier[0] == "CHAMPION"
    assert alpha.is_elite


def test_tier_ladder_thresholds():
    assert _tier_for(1, 100)[0] == "CHAMPION"
    assert _tier_for(2, 100)[0] == "GRANDMASTER"   # 2% <= 3%
    assert _tier_for(6, 100)[0] == "MASTER"        # 6% <= 8%
    assert _tier_for(10, 100)[0] == "DIAMOND"      # 10% > 8%, <= 15%
    assert _tier_for(50, 100)[0] == "GOLD"         # 50% <= 50%
    assert _tier_for(90, 100)[0] == "BRONZE"


def test_medals_and_battles_from_tournament_stats():
    board = parse_benchmarks(SAMPLE_DA, SAMPLE_AA)
    alpha = board.lookup("vendor/alpha-one")
    assert (alpha.golds, alpha.silvers, alpha.bronzes, alpha.battles) == (100, 40, 10, 200)


def test_aa_base_stats_match_despite_paren_suffix():
    board = parse_benchmarks(SAMPLE_DA, SAMPLE_AA)
    alpha = board.lookup("vendor/alpha-one")
    assert alpha.intelligence == 60.0 and alpha.coding == 75.0 and alpha.agentic == 50.0


def test_lookup_by_id_and_by_display_name():
    board = parse_benchmarks(SAMPLE_DA, SAMPLE_AA)
    assert board.lookup("x/gamma-three") is board.lookup("ignored", "Gamma Three")
    assert board.lookup("x/nope") is None


def test_norm_collapses_author_prefix_and_punctuation():
    assert _norm_model_name("Anthropic: Claude Opus 4.8") == "claude opus 4 8"
    assert _norm_model_name("claude-opus-4.8") == "claude opus 4 8"


def test_empty_input_is_safe():
    board = parse_benchmarks([], [])
    assert len(board) == 0
    assert board.lookup("a/b") is None

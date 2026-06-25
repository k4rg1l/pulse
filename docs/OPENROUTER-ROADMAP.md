# OpenRouter Panel — Build Roadmap

The curated, deduped work order for deepening the OpenRouter source panel. Derived from [OPENROUTER-RESEARCH.md](OPENROUTER-RESEARCH.md) (the full capability map). We build **one feature at a time**, each: parser unit-tested against a captured sample → rendered → 20-point UI check → `/security-review` → commit.

> Status legend: ⬜ not started · 🟡 in progress · ✅ shipped. Update as we go.

> **Phase A COMPLETE** — all 18 features + F1–F4 shipped on branch `openrouter-roadmap` (599 tests green); left for review, not merged/released.

---

## Cross-cutting principles (apply to every item)

1. **Mgmt-key-optional, always graceful.** Two data classes:
   - **`noauth`/`user`** (the global field: benchmarks, speed, uptime, privacy, trends) → works for *every* user.
   - **`mgmt`** (your private numbers: real spend/token/latency attribution) → needs the management key (we already store one in `settings.json`). Every `mgmt` feature must show a tidy "add a management key to unlock" state and **never blank or error** when it's absent.
2. **All I/O on the worker thread**, results marshaled to the main thread via signals (AGENTS.md invariant). Heavy JSON parsing stays off the main thread.
3. **One pure parser per feature, unit-tested against a captured sample** in `tools/_probe_out/` — never against the live endpoint (Sources contract).
4. **Font-metric-driven rendering** (one `_build_ops()` feeding both paint and height) so nothing clips. Reuse existing widgets where possible (`TimelineChart`, `BurnRateBar`, sparkline/heatmap patterns, cards).
5. **Polite polling + caching.** Frontend `models/find` is huge (774 models) — fetch sparingly and cache. Analytics queries cache by (metrics, dims, range). Respect the existing multi-cadence timer model; add slow cadences for slow-moving data.
6. **Each feature ships behind a `show_*`/setting where it adds a row or a poll**, additive and backward-compatible (the Settings loader drops unknown keys).
7. **Re-verify the endpoint live immediately before building** (per AGENTS.md; the API drifts — e.g. `/credits` docs vs behavior).

## Shared foundations (built once, ride along with their first consumer)

| # | Foundation | What | First consumer |
|---|---|---|---|
| **F1** | ✅ **Pricing model + bug fix** | Extend the pricing dataclass to carry *all* fields (`input_cache_read/write`, `web_search`, `image`, `audio`, `internal_reasoning`, `request`, `discount`). *Shipped with #5 — pricing dataclass now carries all fields incl. `supports_implicit_caching`; the dead `top_provider` field was removed.* | #5/#6 |
| **F2** | ✅ **Frontend-v1 client + permaslug resolver** | Thin `requests` wrapper for `openrouter.ai/api/frontend/v1/*` (no auth); cache the `catalog/models` slug↔**permaslug** map (stats endpoints need the versioned permaslug). *Built in #2 (`frontend_client.py`: `PermaslugResolver`, `parse_all_providers`/`ProviderTrustBook`, `parse_performance`/`SpeedBoard`, `parse_endpoint_refs`, `parse_uptime_hourly`).* | #2 |
| **F3** | ✅ **Analytics client** | `GET /analytics/meta` + `POST /analytics/query` (mgmt key), with a cached query helper (metrics × dimensions × granularity × range). *Shipped with #9 — AnalyticsClient: `/analytics/meta` + cached `/analytics/query`, management key, the SPEND zone backbone; degrades to honest locked state without a mgmt key.* | #9 |
| **F4** | ✅ **Benchmarks client** | `GET /api/v1/benchmarks?source=design-arena\|artificial-analysis` (user key) → ELO/win_rate + **computed global ranks** + AA intelligence/coding/agentic indices. *Built in #1 (`api_client.parse_benchmarks`/`BenchmarkBoard`).* | #1 |

---

## Information architecture (where features land as they accumulate)

The OpenRouter panel evolves from one flat scroll into four clear zones:

- **① Balance** — the arc gauge + depletion forecast. *Keep as-is.*
- **② Spend** *(new — replaces the estimated "Usage"/"Burn Rate")* — real spend attribution, receipts, savings, budget, the spend-driven flexes. Powered by the Analytics API; shows the unlock-state without a mgmt key.
- **③ Models** — the pinned board, heavily enriched: ELO, speed percentile, uptime ribbon, cheapest-provider, hidden-cost badges, privacy, trending. Mostly no-auth.
- **④ Insights / Flex** — value index, task crown, "out-tokened X", model-of-the-week. Cheap garnish on data already fetched.

---

## The work order (ordered; build top-to-bottom)

Effort sizes assume one focused session each with an AI pair. `auth` = key needed.

### Wave 1 — Enrich the pinned Models board (low-risk, visible, mostly no-auth)

| # | Feature | What the user sees | Data (auth) | Builds | Effort | Status |
|---|---|---|---|---|---|---|
| 1 | ✅ **The Arena** (rank-crest) | Each pinned model wears a **living esports-style rank crest** — tier emblem (Bronze→Champion) + signature category + computed global rank + ELO ("◆ DIAMOND · #6 ASCIIART · 1299"), shimmering for elite tiers. Click → a **Fighter Card**: base stats (AA intelligence/coding/agentic), lifetime medal haul from `tournament_stats`, and the full category ladder with ELO bars. *Went beyond a "badge" per the wild-each-feature directive.* | `/api/v1/benchmarks` (**user**) | F4 | 0.5d→1d | ✅ |
| 2 | ✅ **The Ledger** (Trust Seals + Logos) | Per provider on the board: a painted **Trust Seal** carrying a computed **Custody Score** grade (S→F) with offense notches; click → a **Custody Dossier** (auditable rap sheet + jurisdiction trail + the provider's **real logo**). *Built: seal + dossier (`frontend_client.custody_score`, `widgets.PinnedModelCard._paint_trust_seal`/`dossier_html`); logos via `logo_store.LogoStore` (async download → normalized PNG tile, monogram fallback). NB: the frontend API bot-blocks the default `python-requests` UA — `FrontendClient` overrides it.* | `/api/frontend/all-providers` (**noauth**) | F2 | 1d | ✅ |
| 3 | ✅ **73-Hour Uptime Ribbon** | Replace the 30m/5m/1d dots with a GitHub-style 73-cell hourly heat-strip — spot the exact hour a provider had an outage. | `frontend/v1/stats/endpoint` → `uptime-hourly?id=` (**noauth**) | F2 (+permaslug) | 1d | ✅ |
| 4 | ✅ **Speed Percentile** | Per pinned model: "your endpoint is faster than 82% of the field" + names the fastest/cheapest provider. | `frontend/v1/rankings/performance` (**noauth**) | F2 | 0.5–1d | ✅ |
| 5 | ✅ **Cheapest Door** | "Switch provider → save X%" per model, flagged when it's cheaper *and* faster. | `frontend/v1/stats/endpoint` pricing+speed (**noauth**) | F1, F2 | 1d | ✅ |
| 6 | ✅ **Hidden-Cost Badges** | Surface the fees Pulse is blind to: cache read/write ratio, web-search $/call, reasoning-token billing, per-request fees; mark implicit-caching support. | full `pricing` + `supports_implicit_caching` (**user/noauth**) | F1 | 1–1.5d | ✅ |
| 7 | ✅ **Trending Arrow** | Per pinned model: "📈 +18% requests across OpenRouter this week — you picked a riser." | `frontend/v1/models/find` `analytics` (**noauth**) | F2 | 0.5d | ✅ |
| 8 | ✅ **Price-Drift Watcher** | Toast when a pinned model's price moves, a cheaper provider appears, or yours gets deranked. | pricing snapshot + diff (reuses the snapshot store) | F1 | 1d | ✅ |

### Wave 2 — The Spend section (mgmt key; the marquee value)

| # | Feature | What the user sees | Data (auth) | Builds | Effort | Status |
|---|---|---|---|---|---|---|
| 9 | ✅ **★ Spend X-Ray** | Replace estimated Today/Projected with **ground-truth** spend split by **model & provider**, with tokens & requests, any range, hourly. The headline feature. | `POST /analytics/query` (**mgmt**); fallback `/activity` | F3 | 2d | ✅ |
| 10 | ✅ **Per-Request Receipt** | "A typical Opus call costs you $0.021" — avg cost/call per model, split input/output/reasoning/cache. Catch a model whose per-call cost silently tripled. | `/analytics/query` ÷ requests (**mgmt**) | F3 | 1.5d | ✅ |
| 11 | ✅ **Spend Autopsy** | Click a spend spike → the exact model/provider rows that drained it. | `/analytics/query` hourly, clamped window (**mgmt**) | #9 | 1.5d | ✅ |
| 12 | ✅ **Cache & Reasoning Savings** | "Prompt caching saved you $0.83 this week · 41% hit rate" — completes #6 with *realized* numbers. | `/analytics/query` `cached_tokens, cache_hit_rate, usage_cache, reasoning_tokens` (**mgmt**) | F3 | 1d | ✅ |
| 13 | ✅ **Ghost Model Detector** | Surfaces models/providers that appeared or vanished week-over-week — catch a runaway agent hitting an expensive model you never picked. | `/analytics/query` `dimensions:[model,provider]` diff (**mgmt**) | F3 | 1d | ✅ |
| 14 | ✅ **Budget Burn-Down** | "82% of your $50/week budget burned · 3 days left" against a *real* configured budget, not just balance %. | `/workspaces/{id}/budgets` + `/analytics/query` (**mgmt**) | F3 | 1.5d | ✅ |

### Wave 3 — Value & Flex (cheap garnish on data already fetched)

| # | Feature | What the user sees | Data (auth) | Builds | Effort | Status |
|---|---|---|---|---|---|---|
| 15 | ✅ **Value Index** | Quality-per-dollar leaderboard for your pinned models (ELO or AA-index ÷ price), per category. "Am I overpaying for quality I don't need?" | `/api/v1/benchmarks` + pricing (**user**) | F4, F1 | 1.5d | ✅ |
| 16 | ✅ **Model of the Week** | "This week you're a Claude Opus 4.8 person" — top model by spend + how it shifted vs last week. | `/analytics/query` weekly (**mgmt**) | F3 | 1d | ✅ |
| 17 | ✅ **Token Odometer + Records** | Lifetime token ticker + "🔥 biggest spend day: $2.14 on Jun 12" + current daily-use streak. | `/analytics/query` daily, full range (**mgmt**) | F3 | 1d | ✅ |
| 18 | ✅ **Task Crown + "Out-tokened X"** | "👑 for agentic work you reach for Opus 4.8 — so does the rest of OpenRouter" and "you out-tokened the #40 app, Cline, this week." | `/classifications/task` + `frontend/v1/rankings/apps` + `/analytics/query` (**mgmt**+**noauth**) | F3, F2 | 1.5d | ✅ |

---

## Notes on sequencing

- **Why Wave 1 first:** every item is small, low-risk, and mostly no-auth (works for any user), and they incrementally stand up the Pricing model (F1), frontend client (F2), and Benchmarks client (F4). Visible polish lands immediately; we build the feature-delivery rhythm before the bigger backbone.
- **Why Spend X-Ray (#9) anchors Wave 2:** it builds the Analytics client (F3) that Waves 2 & 3 reuse, and it's the single highest-value change (real cost attribution replacing estimates). It also triggers the IA change (the new **Spend** zone).
- **Flexibility:** the order is a recommendation, not a contract — if you'd rather lead with the marquee Spend X-Ray, we just build F3 first. Pinned-board items (1–7) are independent and can reorder freely.
- **Possible release grouping:** Wave 1 → `v0.8` (richer Models board), Wave 2 → `v0.9` (Spend analytics), Wave 3 → `v0.10` (Insights). Loose, not binding.

---

## Dedup ledger (30 research ideas → 18 features)

- **Spend X-Ray** ⇐ forensics "Spend X-Ray" + optimizer "Spend X-Ray" + forensics "Model-Mix Treemap"
- **Speed Percentile** ⇐ forensics "Speed vs World ribbon" + optimizer "Speed Percentile" + delight "Are You Riding a Fast One"
- **73-Hour Uptime Ribbon** ⇐ forensics + optimizer + delight (all three)
- **Privacy Badges + Logos** ⇐ forensics "Privacy X-Ray" + optimizer "Privacy Price Tag" + delight "Privacy Badges"
- **Value Index** ⇐ optimizer "Value Index" + forensics "Cost-Efficiency Quadrant" (ELO flex split out as #1)
- **Hidden-Cost Badges** ⇐ optimizer "Cache Economics" (static) + optimizer "Surcharge Sentinel"
- **Cache & Reasoning Savings** ⇐ forensics "Token Forensics" + delight "Cache Savings Counter" (the *realized*-$ half)
- **Task Crown + Out-tokened X** ⇐ delight "Task Crown" + delight "Famous Company Energy"
- Kept distinct: Spend Autopsy, Per-Request Receipt, Ghost Model Detector, Price-Drift Watcher, Cheapest Door, Trending Arrow, Budget Burn-Down, ELO Flex Badge, Model of the Week, Token Odometer.
- **Dropped as out-of-reach:** anything needing `/generation` or the chat `usage` object (Pulse makes no inference calls); programmatic top-up (web-only).

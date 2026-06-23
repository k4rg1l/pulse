# OpenRouter Deep-Dive — what we can access & what to build

**Date:** 2026-06-23 · **Method:** live API probing (both the user key and the management key) + the official OpenAPI spec + reverse-engineering the website's own `/api/frontend/v1/*` calls. Every endpoint below was hit live and returned JSON unless marked otherwise. Probe scripts: `tools/or_probe.py`, `tools/or_probe_frontend.py`; raw captures in `tools/_probe_out/` (gitignored — they contain real account data).

> **Research only — no app code was changed.** This is the map; building is a separate decision.

---

## TL;DR

- The current OpenRouter panel uses **~5 endpoints and ~15 fields**. OpenRouter actually exposes **three whole tiers** of data we're barely touching.
- The single biggest unlock is the **Analytics API** (`POST /api/v1/analytics/query`, management key — *we already store it*): real **hourly**, **arbitrary-range** spend broken down by **model / provider / app / country**, with **your-own-traffic latency percentiles, cache-hit rate, reasoning tokens, and BYOK fees**. It makes the locally-estimated Today/Projected/Burn numbers obsolete and unlocks a dozen features.
- There's a completely **undocumented, no-auth** website API at **`/api/frontend/v1/*`** — model popularity/trend analytics, full latency p50→p99 curves, **73-hour uptime history**, speed leaderboards, "top apps using this model", per-provider **data-policy/privacy** posture and **logos**. Zero key budget, works today.
- Models already ship embedded **DesignArena ELO** + **Artificial Analysis** intelligence/coding/agentic indices, plus **cache, web-search, reasoning, image pricing** — all currently ignored.
- **Bug found:** `api_client.py` reads `top_provider["name"]`, which doesn't exist in the API → `ModelInfo.top_provider` is always `""`. (Cheap fix; several ideas below want clean provider names.)

---

## Part 1 — The current OpenRouter page: honest evaluation

| Section | What it shows | Verdict |
|---|---|---|
| **Credit Balance** (arc gauge) | balance %, $remaining/$total, depletion forecast, auto-top-up label | **Solid, keep.** The hero metric. The auto-top-up label is slightly weak (no API can *trigger* a top-up — it's web-only — so it's informational only). |
| **Usage** (24h timeline + Today / Projected-mo) | balance-over-time from *locally persisted snapshots*; today's spend; naive monthly projection | **Weak / reinventing the wheel.** All of this is *estimated* from balance deltas. OpenRouter has the **exact** numbers (`/analytics/query`, `/key.usage_*`). The timeline can't break down *where* money went. |
| **Burn Rate** (single % bar) | % used + $/hr·$/day | **Thin.** One number. No model/provider attribution; can't answer "what's burning it." |
| **Pinned Models** (health board) | per-provider latency p50/p90, uptime, throughput p50, price | **Good bones, shallow data.** Uses the *global* p50/p90 only; drops p75/p99, cache/web-search pricing, max-output-tokens, capacity, and the **73-hour uptime history** that exists. Shows no quality/benchmark signal. |
| **Quick Links** | Dashboard / Add Credits / Models | Fine. |

**Bottom line:** the panel is a competent **balance + provider-health** view, but it (a) *estimates* things OpenRouter reports exactly, (b) shows **zero cost attribution** (which model/provider ate the budget), and (c) ignores the entire **quality, privacy, caching, and trend** dimensions. It treats OpenRouter as a balance API when it's actually a rich analytics platform.

---

## Part 2 — The accessible surface (clean inventory)

Three tiers by auth. **Pricing values are strings = USD per token** (×1e6 for $/Mtok); latency in ms; throughput tok/s; uptime 0–100.

### Tier A — Public API, **user key** (`sk-or-v1-…`)

| Endpoint | Gives you | Used today? |
|---|---|---|
| `GET /api/v1/key` (`=/auth/key`) | usage, **usage_daily/weekly/monthly**, **byok_usage***, limit/limit_remaining, is_free_tier, expires_at | partial — weekly/monthly & all BYOK ignored |
| `GET /api/v1/credits` | total_credits, total_usage | yes |
| `GET /api/v1/models` | per-model: `pricing{prompt, completion, input_cache_read, input_cache_write, web_search, image, audio, internal_reasoning, request}`, `architecture{modality, input/output_modalities, tokenizer}`, `supported_parameters`, `reasoning{mandatory,…}`, `knowledge_cutoff`, `top_provider{context_length,max_completion_tokens,is_moderated}`, **`benchmarks.design_arena[]` (ELO/win_rate/rank × 21 categories)** | only id/name/context/prompt+completion price |
| `GET /api/v1/models?category=…` / `?supported_parameters=…` | server-side filtered catalog (also carries `benchmarks`) | no |
| `GET /api/v1/models/{id}/endpoints` | per-provider: full `pricing` (incl cache/web-search/discount), `uptime_last_30m/5m/1d`, `latency{p50,p75,p90,p99}`, `throughput{p50,p75,p90,p99}`, `max_completion_tokens`, `quantization`, `supports_implicit_caching`, `status` | only p50/p90 latency, p50 throughput, uptime, price |
| `GET /api/v1/benchmarks?source=artificial-analysis\|design-arena` | unified quality feed: AA `intelligence_index/coding_index/agentic_index`; DesignArena `elo/win_rate/rank`, `avg_generation_time_ms`, `pricing` | **no** (verified live) |
| `GET /api/v1/datasets/rankings-daily?start_date&end_date` | top models/day by tokens (≤30d) | **no** (verified live) |
| `GET /api/v1/datasets/app-rankings` | top apps by token usage | **no** (verified live) |
| `GET /api/v1/classifications/task?window=7d` | market-share of traffic by task type (code/search/…) | **no** (verified live) |
| `GET /api/v1/model/{author}/{slug}` | one model's full detail without the 200 KB list | **no** (verified live) |
| `GET /api/v1/parameters/{model}` | supported params per model | no (redundant w/ models) |
| `GET /api/v1/providers` | name, slug, status_page_url, headquarters, datacenters, policy urls | fetched, barely shown |

### Tier B — **Management key** (org-scoped; we already store one in `settings.json`)

| Endpoint | Gives you | Used today? |
|---|---|---|
| **`POST /api/v1/analytics/query`** ⭐ | **the crown jewel.** 35 metrics (`total_usage, request_count, tokens_*, reasoning_tokens, cached_tokens, cache_hit_rate, p50/p90/p99_latency & _throughput, byok_fees, usage_web/file/cache, …`) × 13 dimensions (`model, provider, app, country, finish_reason, api_key_id, …`) × granularity `minute→month`, **arbitrary time range (no 30-day cap)** | **no** |
| `GET /api/v1/analytics/meta` | the queryable vocabulary (metrics/dimensions/granularities/operators) | no |
| `GET /api/v1/activity` | per-day × model × endpoint × provider: usage$, requests, prompt/completion/reasoning tokens, byok | **no** (30-day rolling; the analytics API supersedes it) |
| `GET /api/v1/keys` (+`/{hash}`) | per-key spend windows, `workspace_id`, `created_at` | no |
| `GET /api/v1/workspaces/{id}/budgets` | **real configured spend budgets per interval** | no |
| `GET /api/v1/byok`, `/organization/members` | BYOK creds; user-id → name mapping | no |

### Tier C — Undocumented **website API, NO auth**: `https://openrouter.ai/api/frontend/v1/*`

> Reverse-engineered from the site's JS bundles. Works with a plain `requests` GET. **Gotcha:** the `/v1/` segment is required, and `stats/*` want the **versioned permaslug** (`anthropic/claude-4.8-opus-20260528`), not the public slug — resolve via `catalog/models`.

| Endpoint | Gives you |
|---|---|
| **`/models/find`** ⭐ | single richest payload on the site: full 774-model catalog **+ `analytics`** (per-model request/token volume per day) **+ `endpoint_perf`** (fleet-wide p50→p99 latency/throughput by endpoint) **+ `benchmarks`** + `modality_counts` |
| `/catalog/models` | slug↔**permaslug** Rosetta Stone + rpm/rpd limits, reasoning_config, default_system |
| `/stats/endpoint?permaslug=…&variant=standard` | per-provider stats **richer than the public endpoints call**: p50→p99 latency+throughput, `request_count`, `capacity_tpm`, tier breakdown, cache/web-search `display_pricing`, `is_deranked`, `data_policy` |
| `/stats/uptime-hourly?id=<endpoint-UUID>` | **73 hourly uptime points** — the real uptime-over-time chart |
| `/rankings/performance` | global speed leaderboard: per model `p50_latency/throughput`, `best_latency_provider/_price`, `best_throughput_provider/_price` |
| `/rankings/apps?permaslug=…` | **"top apps using THIS model"** (rank, tokens, app title/url/categories) |
| `/rankings/models`, `/rankings/benchmarks`, `/rankings/market-share`, `/rankings/task-spend` | leaderboards: token/tool/reasoning breakdown; AA+DesignArena indices w/ prices; author market-share over 52 weeks; which models win which job |
| `/all-providers` | per-provider **`dataPolicy{training, retainsPrompts, retentionDays, canPublish}`**, `byokEnabled`, `datacenters`, **`icon.url` (logos)**, `pricingStrategy` |

**Out of reach:** `GET /api/v1/generation?id=` and the chat `usage` object are rich (cost, native tokens, cache discount) but need a generation id from an inference call — Pulse makes none. `/api/frontend/v1/private/*` need the website session cookie. No programmatic credit top-up exists (web-only).

### The "richest ignored", ranked
1. **Analytics API** — exact spend/token/latency/cache attribution by model & provider (mgmt key). 2. **DesignArena + AA benchmarks** — model quality, free. 3. **Full pricing** — cache read/write, web-search, reasoning, image fees. 4. **frontend `/models/find` + `/rankings/*`** — trend, popularity, speed-vs-field, no auth. 5. **73-hour uptime history**. 6. **Provider data-policy/privacy + logos**. 7. **Budgets API**.

---

## Part 3 — Curated feature ideas

Filtered hard for *useful AND cool* — nothing the panel already does, nothing basic. Each cites a proven-accessible source. **Auth** = `noauth` / `user` / `mgmt`. Grouped by theme; a recommended build order follows.

### Theme 1 — Real cost attribution (replaces the estimated Usage/Burn sections)
- **★ Spend X-Ray** `mgmt` — ground-truth **per-model / per-provider** spend, tokens, requests for any range, hourly. Replaces snapshot-estimated Today/Projected and answers "what's eating my budget." `analytics/query` (fallback `/activity`). *~2d. The foundation everything else builds on.*
- **Spend Autopsy** `mgmt` — click a balance spike → the exact model/provider rows that drained it. *~2d, builds on X-Ray.*
- **Per-request Receipt** `mgmt` — derived "typical cost per call" per model, split input/output/reasoning/cache; catch a model whose per-call cost silently tripled. *~1.5d.*

### Theme 2 — Save money (the optimizer angle)
- **★ Cheapest Door** `noauth` — per pinned model, "switch provider → save X%", and flags when a provider is cheaper *and* faster. `stats/endpoint` pricing+speed. *~1d, no key needed.*
- **Cache Economics / "Caching saved you $X"** `user`+`mgmt` — cache-read vs fresh price ratio per provider (often 10×) + realized savings & hit-rate from analytics. Surfaces an economics dimension Pulse is blind to. *~1.5d.*
- **Surcharge Sentinel** `user`+`mgmt` — hidden-fee meter: web-search ($/call), reasoning-token billing, per-request flat fees, BYOK fees — so a "cheap" model isn't secretly expensive. *~1–2d.*
- **Price-Drift Watcher** `noauth` — snapshot pricing each poll; toast when a pinned model's price moves, a cheaper provider appears, or yours gets deranked. Reuses the existing snapshot store. *~1d.*

### Theme 3 — Quality & value (genuinely novel)
- **★ Value Index** `user`/`noauth` — quality-per-dollar leaderboard for your pinned models (ELO or AA-index ÷ price), per category (SVG/website/agentic…). The "am I overpaying for quality I don't need?" panel. `/benchmarks` or `rankings/benchmarks`. *~1.5d.*
- **Flex Bar (ELO badge)** `user` — each pinned model wears its DesignArena rank ("#3 in `uicomponent`, 1487 ELO"). Status-symbol, cheap. *~0.5d.*

### Theme 4 — Provider health & trust (upgrades the pinned board)
- **★ 73-Hour Uptime Ribbon** `noauth` — replace the 30m/5m/1d dots with a GitHub-style 73-cell hourly heat-strip; see the exact hour a provider had an outage. `stats/uptime-hourly`. *~1d. Biggest visual "wow."*
- **Speed Percentile** `noauth` — "your Sonnet endpoint is faster than 82% of the field" + names the best provider. `rankings/performance`. *~0.5–1d.*
- **Privacy Badges** `noauth` — 🛡️ "zero retention" / ⚠️ "trains on prompts, 30-day retention" per provider, plus real provider **logos**. Unique to Pulse, glanceable. `all-providers.dataPolicy` + `icon.url`. *~0.5–1d.*

### Theme 5 — Budget & alerting
- **Budget Burn-Down** `mgmt` — "you've burned 82% of your $50/week budget, 3 days left" against a *real* configured budget, not just balance %. `workspaces/{id}/budgets` + analytics. *~1.5d, gated on the user having set a budget.*

### Theme 6 — Delight / flex (cheap dopamine, mostly ≤1d)
- **Model of the Week** `mgmt` — "This week you're a Claude Opus 4.8 person" — top model by spend + how it shifted. *~1d.*
- **Token Odometer + Personal Records** `mgmt` — lifetime ticker + "🔥 biggest spend day: $2.14 on Jun 12" + daily streak. *~1d.*
- **"You out-tokened Notion this week"** `mgmt`+`noauth` — pit your weekly tokens against the nearest app on the leaderboard. *~0.5d.*
- **Trending arrow** `noauth` — "📈 +18% requests across OpenRouter this week — you picked a riser." `models/find.analytics`. *~0.5d.*
- **Task Crown** `mgmt`+`noauth` — "👑 for agentic work you reach for Opus 4.8 — and so does the rest of OpenRouter." Validates taste vs the world. *~1.5d.*

### Recommended build order (if we go ahead)
1. **Fix the `top_provider.name` bug** (5 min) — several ideas want clean provider names.
2. **Spend X-Ray** (`analytics/query`) — the foundational data layer; everything in Theme 1/5/6 reuses it. Build the `analytics/query` client once.
3. **73-Hour Uptime Ribbon + Speed Percentile + Privacy Badges** — all `noauth`, all upgrade the existing Pinned board, high visual payoff, low risk.
4. **Cheapest Door + Value Index** — the two highest-impact "save money / am I overpaying" insights.
5. Layer in delight flexes (Model of the Week, ELO badge, Trending) as cheap garnish on the data already fetched.

**A clean architecture split falls out naturally:** *your private numbers* come from the **management-key Analytics API**; *the global field* (benchmarks, speed/uptime/popularity, privacy) comes from the **no-auth frontend API** — so the "vs. the world" comparisons cost zero key budget and work even if the management key is absent.

---

## Appendix — verification & artifacts
- **Spot-verified live (2026-06-23):** `analytics/meta`, `analytics/query`, `frontend/v1/models/find`, `frontend/v1/rankings/performance`, `benchmarks?source=design-arena`, `classifications/task` — all 200 JSON.
- Probe scripts: `tools/or_probe.py` (v1 surface), `tools/or_probe_frontend.py` (frontend recon). Captures + `openapi.json` (1.5 MB authoritative spec) in `tools/_probe_out/` (gitignored).
- Per AGENTS.md: re-verify any endpoint immediately before building on it; the `/credits` docs↔behavior currently disagree (docs say mgmt-only, user key still works).

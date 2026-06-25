# PHASE A — VALIDATION PLAYBOOK

**A self-contained QA guide for a cold agent.** Phase A of the OpenRouter roadmap shipped on branch `openrouter-roadmap` (off `main`): **15 user-facing features (#3, #5–#18) + foundation F1/F3, 599 tests green.** This doc lets a fresh agent — with *no memory* of the build — validate every feature with deterministic evidence.

> **Scope:** This is a *validation* pass. Do **not** change feature code, commit, stage, or switch branches. If you find a real bug, document it with a measurable reproduction; the orchestrator decides the fix.

---

## 0. Tree-verification status (done 2026-06-24)

Every fact below was checked against the working tree before this doc was written:

- ✅ All 8 `show_*` toggles exist in `settings.py` exactly as named (lines 70–88): `show_trend`, `show_door`, `show_uptime`, `show_hidden_fees`, `show_drift`, `show_spend`, `show_insights`, `show_credit_burndown`. Plus `weekly_budget: float = 0.0` (L83), `management_api_key: str = ""` (L35).
- ✅ All 18 test files present in `tests/` (15 features + `test_pricing_model_f1.py`, `test_door_resolution.py`, `test_price_drift.py`).
- ✅ All 15 live-boot log markers found in source (`api_client.py`, `dashboard.py`, `widgets.py`).
- ✅ `python -m pytest -q` → **599 passed**.

**Discrepancies vs the brief (minor, non-blocking):**
1. **Runtime:** the suite ran in **5.74s** on this machine — slightly over the "sub-5s" estimate. Still fast; not a regression.
2. **Log-string wording** is *richer* than the per-feature shorthand below but semantically identical. Notable exact forms: the #14 default is `budget: no budget configured (source=none)`; #9 renders `spend board: N models, $X.XX over last 7 days, spike <date>`; #5 is `threshold: <m> SAVE N% green=<bool> (<prov> $X/Mtok -> <prov> $Y/Mtok)`; #6 is `waterline: <m>/<ident> classes={…} depth=N/5 implicit_cache=<bool>`. Each feature also emits a paired **degrade/locked** marker (e.g. `spend board: none (locked or no data)`, `title belt: no spend this week (empty)`) — seeing the degrade line on this account is **correct**, not a failure.

---

## 1. How to run / validate

### Rebuild + launch
```
powershell -ExecutionPolicy Bypass -File tools/rebuild.ps1
```
Clean-kills any stale single-instance Pulse, builds `dist/Pulse.exe`, and launches it. Everything in this playbook renders in the dashboard's **OpenRouter panel**. (`-NoRun` skips the launch.)

### Automated truth — the regression source of truth
```
python -m pytest -q          # expect 599 passed, ~5–6s
```
These deterministic `qapp` (offscreen Qt) / pure-math tests are the **source of truth** for regressions. Each feature is covered by *measured* tests (pixels, geometry, math) — not smoke. If a test fails, that is a real regression; investigate before any visual QA.

### Structured logs — confirm data landed on a live boot
- Path: `%APPDATA%/Pulse/logs/pulse.jsonl` (JSON-lines).
- Each feature logs a distinct **INFO** line on a live boot (the "log marker" column per-feature below). Search for it to confirm the feature's data actually flowed.
- `rg '"level":"ERROR"' "$APPDATA/Pulse/logs/pulse.jsonl"` → find crashes. (See §4 for the *known* benign WARNINGs.)

### Validation discipline (project rule — MEMORY-backed)
**Deterministic evidence > screenshot-clicking.** Remote GUI clicking on this multi-monitor setup is unreliable. **The USER does the visual QA**; agents prove behavior with the `qapp` tests + the log lines. **Never claim a fix or feature works without measurable evidence** — a passing test that asserts the exact condition, or the feature's INFO log line on a real boot.

### Auth tiers on this machine
- A **`management_api_key` IS present** here, so the mgmt-gated widgets (Wave 2 SPEND, Wave 3 #16/#17/#18) render **LIVE**.
- To validate the **LOCKED** path: temporarily blank `management_api_key` in `%APPDATA%/Pulse/settings.json`, reboot, verify the honesty contract (§3), then **restore it**.

---

## 2. Per-feature validation table

Legend — **Auth tier:** `noauth` = website frontend API · `user` = user API key · `mgmt` = management/Analytics key.

### WAVE 1 — pinned-model CARD enrichments (per-card / per-provider-row)

| # / Name | Where it renders | Toggle | Data + tier | What correct looks like | Honest / degrade states | Test | Live-boot log marker |
|---|---|---|---|---|---|---|---|
| **#3 THE PULSE** | A 36px uptime **cardiogram** in each provider row's uptime column (replaces the % chip) | `show_uptime` | frontend `stats/endpoint` → uptime-hourly · **noauth** | Healthy = calm green pulse; a dip plunges **red + scar + worst-dot**. Click a row → a painted **73-bar VITALS dossier** | Absent data → falls back to the **legacy % chip** | `tests/test_uptime_pulse.py` | `PULSE uptime landed for <model>: <n> endpoints with history` |
| **#5 THE THRESHOLD** | A 3rd header band — a perspective **DOOR** hinged on the rail: "SAVE N% · <provider>" | `show_door` | per-provider `EndpointInfo` pricing+speed · **local math, no fetch** | **EMERALD "green door"** when the cheaper provider is *also* faster. Click → **FROM→THROUGH** dossier with an honesty line. Also carries **F1** (pricing dataclass) | No cheaper option → **no band** | `tests/test_threshold_door.py`, `tests/test_door_resolution.py`, `tests/test_pricing_model_f1.py` | `threshold: <model> SAVE N% green=<bool> (…)` |
| **#6 THE WATERLINE** | Under each provider's price number — a steel-teal **"sea level" strip** submerged by hidden-fee depth, + a left implicit-caching **"buoy"** | `show_hidden_fees` | F1 `pricing_extra` (cache / web-search / reasoning / media classes) · local | Strip submerges by hidden-fee depth (0–5). Click → **"WHAT THE STICKER PRICE HIDES"** dossier | A **clean** row (prompt+completion-only) draws **nothing** | `tests/test_waterline.py` | `waterline: <model>/<ident> classes={…} depth=N/5 implicit_cache=<bool>` |
| **#7 THE TAPE** | A torn **ticker-tape** momentum stamp in the header right gutter ("+43%" / "+57x" / "~") | `show_trend` | frontend `rankings/models` `change` · **noauth**, ~20min cache | Amber riser / violet faller stamp. Click → **week-over-week** dossier | Unranked model → **nothing** | `tests/test_tape_trend.py` | `trend: <model> change=<x> stamp=<s>` |
| **#8 THE FAULT LINE** | A **seismograph crack** on the card's LEFT EDGE + per-row tremor ticks | `show_drift` | a **NEW `price_snaps.json`** store diffed vs current `EndpointInfo` · no fetch | Crack appears **only** when price/derank shifted since last snapshot. Click → **SEISMOGRAPH** dossier | Quiet → **literally zero pixels** | `tests/test_fault_line.py`, `tests/test_price_drift.py` | `drift: <model> mag=X dir=<adverse\|favorable> rows=N` |

> **#8 baseline note:** the **first run is silent by design** (no prior snapshot to diff). A drift only appears after a real price/rank change *between two snapshots*. Silence on first boot is **correct**, not a bug.

### WAVE 2 — the new SPEND section (`show_spend` gates the whole section; **mgmt** key)

Replaces the old *estimated* Usage / Burn-Rate. With the mgmt key present, this whole zone is live.

| # / Name | Where it renders | Data + tier | What correct looks like | Honest / degrade states | Test | Live-boot log marker |
|---|---|---|---|---|---|---|
| **#9 THE SPECTRUM** (+F3 `AnalyticsClient`) | The hero of the SPEND section | `POST /analytics/query` · **mgmt** | A stacked gradient **spend ribbon** (time X, model bands Y) + a count-up range **TOTAL** + a legend-spine + a glowing **spike** column | **Locked:** padlock + "add a management key to unlock ground-truth spend" + **ghost silhouette** (no fake $). **Empty:** "$0.00 · No spend in this range" | `tests/test_spend_spectrum.py` | `spend board: N models, $X over last 7 days, spike <date>` |
| **#10 THE TILL ROLL** | Per-model **receipt stubs** under the Spectrum | rides #9's cached query · **mgmt** | avg $/call + sparkline + a red **"x N PRICE UP"** stamp. Click → a full **thermal-receipt** pixmap | **Stamp only fires** on a ≥2×-vs-7d-median day with ≥10 reqs — **won't fire on this flat account** (proven by fixtures), expected | `tests/test_receipts.py` | `receipts: N models, top $/call=$X, stamped=K` |
| **#12 THE REBATE STUB** | A **perforated coupon** under the Spectrum | rides #9's query · **mgmt** | **GREEN** "CACHING REBATE · 7D $X" (= abs(usage_cache)) + a hit-rate **half-arc** + a **PURPLE** reasoning meter "tokens, not $" | Locked/empty → honesty state, no fake $ | `tests/test_rebate.py` | `savings: rebate=$X, hit=Y%, rsn=Z tok` |
| **#13 THE SÉANCE** | A **veil**: living (model,provider) sigils above a membrane, vanished sink below, appeared flare in | rides #9's query · **mgmt** | Sigils placed by lifecycle state | **YOUNG-ACCOUNT live state:** "watching — needs a 2nd full week to spot ghosts" — only 1 week of data → **NO false apparitions**. Expect `young=True` | `tests/test_seance.py` | `ghosts: living=N appeared=K vanished=M young=<bool>` |
| **#14 THE HOURGLASS** | A budget **burn-down hourglass** with a pace tick | local (credits/budget) | Hourglass drains; **RED** when ahead of pace before 100% | **LIVE DEFAULT = "Set a budget"** (no budget API exists; both new settings off). Default log: `budget: no budget configured (source=none)` | `tests/test_hourglass.py` | `budget: no budget configured (source=none)` (default) |
| **#11 THE AUTOPSY** | A **drag-to-lasso BEHAVIOR** on the #9 Spectrum chart | rides #9's query · **mgmt** | Press-drag across the chart → a selection band; release → a **forensic dossier** of the (model,provider) rows that drained that window | **GUI-only** — the user must drag to see it; the worker path is proven by tests + the log line | `tests/test_autopsy.py` | `autopsy: <window> N rows, top=<model>@<provider> $X (Y%)` (on a lasso) |

> **#14 to SEE the populated hourglass:** set `weekly_budget > 0` **OR** `show_credit_burndown: true` in `settings.json`. The credits fallback shows ~45% of $10 → ahead-of-pace → **RED**. Restore afterward.

### WAVE 3 — the new INSIGHTS section, below Models (`show_insights` gates the whole section)

`#15` is **user-key always-live**; `#16/#17/#18` are **mgmt**.

| # / Name | Where it renders | Auth | What correct looks like | Honest / degrade states | Test | Live-boot log marker |
|---|---|---|---|---|---|---|
| **#15 THE ASSAY** | Struck **COINS** sized by quality-per-dollar (AA index / cheapest $/Mtok) on a **log rail** | **user** (always live) | Top-value pick is **GOLD** + a hallmark + the "N.N×" multiple. Click a coin → a **3-category assay certificate**. **Real:** GLM-5.2 gold-hallmarked **~4.8×** over copper Opus | **0 pins → "Pin a model"**; no-benchmark → a hollow **"unassayable"** coin (ELO **never** on the rail) | `tests/test_value_assay.py` | `value assay: N models, top=<model> value=X x<MULT>` |
| **#16 THE TITLE BELT** | A championship **belt** engraved with your top-spend model this week | **mgmt** | Belt shows the week's spend champion | **YOUNG-ACCOUNT live state:** a muted **"WEEK 1 · NO PRIOR ROUND"** ribbon (no fake WoW delta). Champion logo is a **MONOGRAM disc** (champion isn't pinned) — expected | `tests/test_title_belt.py` | `title belt: champion=<model> share=N% week=1` |
| **#17 THE FLIGHT RECORDER** | A brass **odometer drum** (lifetime tokens count-up) + a black-box **record-day** strip + a runway-of-lights **streak** | **mgmt** | Odometer counts up; record day + streak runway | **Young-account real:** ~**6.70M** lifetime, record **Jun 22 $4.37**, a **"3-DAY RUN"** (today-absent → *last-active-run* label, **never** an ongoing claim) | `tests/test_flight_recorder.py` | `flight recorder: lifetime=N tok, record=<date> $X, run=K` |
| **#18 THE COURT & THE CLIMB** | **Top:** a 4-seat **WORLD task court** (gold crown per macro-category from global market-share) + your top model as an **EMBER "you reach for Y"** chip. **Bottom:** a log-scale apps **rope-ladder** with YOU as an ember marker in the **VALLEY ~10,000× below the floor** | **mgmt** | Top = taste-vs-world (NOT a fake personal task split). Bottom = the ladder + your ember in the valley | The **"out-tokened"** claim is **NEVER printed** (it's false). Favicon = a placeholder **gold dot** (expected) | `tests/test_court_climb.py` | `court & climb: crowns=4, apps floor=N tok, you=N tok (Xx below), ember=<bool>` |

---

## 3. Honesty contract to verify (the user cares about this)

- **Every mgmt-gated widget** must show a **tidy LOCKED state** when `management_api_key` is blanked: a **padlock** + "add a management key…", `TEXT_MUTED` styling, and **ZERO fabricated numbers** — never a fake "0" or an invented figure. (Validate by blanking the key, rebooting, eyeballing the SPEND + INSIGHTS zones, then restoring the key.)
- **The young-account states are REAL, not faked:** #13 "Week 1 / watching", #16 "WEEK 1 · NO PRIOR ROUND", #17 short run (last-active-run, not ongoing), #18 valley placement. These reflect a genuinely young account with ~1 week of data — they are the **honest** rendering.
- **#14** honestly shows **"Set a budget"** by default (no budget API exists).
- **#18 NEVER** claims **"out-tokened"** — that comparison is false and is deliberately not printed.

---

## 4. Known non-blocking items / gotchas (don't mis-file these as bugs)

- **Transient WARNING-level** frontend/analytics `ConnectionResetError` (`WinError 10054`) in the logs is a **known network blip**, handled by keep-last-good — **NOT** a feature failure.
- **Pre-existing OPEN cosmetic issues** (predate Phase A): the active nav-rail slot reads **too bright** for vivid accents (GPU/System/Settings); the dashboard **height doesn't shrink** when switching to a shorter tab.
- **Tuning guesses needing the user's eye:** #3's heartbeat **BEAT shape** + 1× **scar thinness**; the **desaturation thresholds**; the #14 **RED pace pinch**.
- **Pre-existing (NOT Phase A) hardening candidates** flagged by security reviews, low-risk: an **unescaped `model_id`** in `ProviderModel.provider_html`; and `set_accent` / `_apply_frame_style` interpolating an accent into a Qt stylesheet **without `_safe_color`** (bounded in practice by `QColor.name()`). Worth a future hardening pass — **not blockers**.
- The benign **COM `0x8001010d`** / `hotkey.py` **`RPC_E_WRONG_THREAD`** dump in `pulse-crash.log` is **pre-existing + isolated** (the app survives it).

---

## 5. Validation discipline + what's next

**Reminder:** deterministic evidence is the bar. Prove every feature with a passing measured test (`tests/test_*.py`) and/or its INFO log marker on a real boot. Do not claim success you cannot show. The user owns the *visual* QA; agents own the *measurable* proof.

**Next phase (NOT part of this pass):** **Phase B — the Claude deep-dive**, on a **fresh branch off `main`**, per [docs/ORCHESTRATOR.md](ORCHESTRATOR.md). Phase A on `openrouter-roadmap` is left for review — not merged, not pushed, not released.

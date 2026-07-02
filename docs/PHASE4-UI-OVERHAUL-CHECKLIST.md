# PHASE 4 — OpenRouter page UI overhaul: THE CHECKLIST

Owner-dictated 2026-07-01. This file is the plan of record for the overnight
autonomous run. Work the items IN ORDER (item 1 gates everything else). Tick
boxes by editing this file (`[ ]` -> `[x]`) as you complete them. Do not skip a
box. Do not mark a box done without the evidence it demands.

The owner has pre-authorized this run (2026-07-01): full autonomy, zero
questions, make every decision. Commits are pre-approved (the mechanical
security-review ritual still applies — see Operating Manual). NEVER push.

---

## IDEOLOGY (applies to every single box below)

- **Relative dimensions.** After item 1 lands, NOTHING new is absolute px.
  Every size, gap, margin, radius, and font size in touched code derives from
  the metrics module / theme scale. Touched code with a bare `12`, `560`,
  `setFixedWidth(N)` etc. is a defect unless it routes through the scale.
- **One palette.** Colors come from `theme.Colors` tokens or
  `spend_palette.model_color`. No new hex literals in touched paint code.
  Fewer colors per widget, not more. Heat-coloring only where it carries a
  real signal.
- **Consistent rhythm.** One spacing scale. Section gaps identical between
  sections. Padding symmetric unless there is a stated reason.
- **Language.** Plain direct English everywhere a user can read: no em-dashes,
  no jargon ("ground truth", "drained", "seance"), no cutesy AI slop, no
  metaphor-speak. Short labels. If a line needs a paragraph to explain, the
  design is wrong.
- **Useless things get removed, not polished.** If a widget/feature carries no
  information the owner can act on, delete it and its dead code. Output over
  effort.
- **DRY.** Repeated paint/layout patterns get factored (base helpers already
  exist: `PopupStrip`, `BaseCard`, `_alpha`, `_rounded`, `_strip_img_div`).
- **Deterministic proof.** Every geometry claim gets a `qapp` test. Every
  visual claim gets a live zoomed screenshot audited pixel-by-pixel BEFORE
  moving on (see the Validation Gate).

## THE VALIDATION GATE (run for EVERY widget and EVERY modal you touch)

Copy this list mentally into every sub-item; the boxes below say
"GATE: <surface>" to mean exactly this sequence:

1. `python -m pytest -q` green (full suite, no skips).
2. Restart the app (Operating Manual), open the dashboard.
3. Open/trigger the exact surface (widget state, hover state, modal, empty
   state, locked state where it exists).
4. Take a screenshot AND a hard zoom of the surface. Audit like a hostile
   reviewer: truncation, overlap, ellipses hiding data, misalignment, uneven
   gaps, text-on-fill collisions, color count, contrast, off-palette colors,
   absolute px in new code, AI language, em-dashes.
5. Fix every defect found, repeat the gate until a clean pass.
6. Only then tick the box and record one line in the Run Log (bottom).

## OPERATING MANUAL (facts so you do not rediscover them)

- Restart: kill `python.exe` whose CommandLine matches `*main.py*`
  (Get-CimInstance filter), then
  `Start-Process C:\Python314\python.exe main.py -WorkingDirectory <repo> -WindowStyle Hidden`,
  wait ~6s. Single-instance mutex: old instance must die first.
- Tray icon: visible tray, image coords ~(1316, 806) on the primary
  (XG32UCWMG, 2560x1440 logical). First click after a restart may hit the dead
  instance's ghost icon; click again. One click toggles open/close.
- Screenshot scale: full-screen capture is ~1456px wide for 2560 logical.
  Zoom regions reference the LAST full screenshot (never mid-batch).
- Dashboard opens bottom-right. OpenRouter tab is the default.
- Scroll with the scroll action over the dashboard body.
- Tests: `python -m pytest -q` from repo root; baseline 624 passed ~5.5s.
- Files: widgets.py (BOM+CRLF, ~10.5k lines), dashboard.py, theme.py,
  spend_model.py (pure builders), settings.py/settings_panel.py, main.py.
- Commit ritual per chunk: `git add <specific files>` -> spawn a Sonnet agent
  to security-review `git diff --staged` -> resolve -> re-stage ->
  `python tools/secreview_approve.py` -> `git commit` (separate call).
  Commit message style: short conventional prefix (feat/fix/refactor/docs).
  NEVER `--no-verify`, NEVER push, NEVER `git add -A` for mixed work.
- Hard invariants (AGENTS.md): auto GC stays disabled; no setWindowTitle;
  adjustSize never shrinks (resize(sizeHint()) after); never name a Qt
  Property after a QWidget builtin; font-metric-driven card geometry; no
  print() in app code.

---

## ITEM 1 — DISPLAY-RELATIVE DIMENSIONS (the gate; do first; nothing else starts until this is DONE)

Architecture (agreed with owner): a `metrics` module derives ONE base unit
from the active screen, configured once in `main.py` right after
`QApplication(sys.argv)` and BEFORE `Dashboard(...)` is built; a spacing /
type / radius scale in `theme.py` rides it. Proportional-to-screen, no user
resizing, height sizes to content.

- [ ] Create `metrics.py`: `configure(screen)` captures
      `screen.availableGeometry()` + `devicePixelRatio` + `logicalDotsPerInch`;
      `unit()` returns the base unit; helpers `px(n_units)` -> int px,
      `font_pt(role)` for the type scale. Base unit spec: derive from screen
      height so the dashboard fits ~92% of work-area height at its natural
      content size on 1440-logical; clamp so a 1080p and a 2160p screen both
      produce sane values. Document the formula in the module docstring.
- [ ] `metrics.configure()` called in `main.py` immediately after
      `QApplication` exists, before any widget import-time constant is
      consumed. Audit for import-time px constants that would bake in before
      configure (config.DASHBOARD_WIDTH is import-time — route it through
      metrics at Dashboard-build time instead).
- [ ] Fallback: `unit()` works (returns the 1440-logical default) if
      configure was never called, so headless tests never crash.
- [ ] theme.py: add the scale — `Spacing` (xs/sm/md/lg/xl as unit multiples),
      `Radius` (sm/md/lg), and route `Fonts.*` point sizes through
      `metrics.font_pt` roles. IDENTICAL visual output on the owner's monitor
      as today for anything not yet migrated (scale defaults match current
      px on 2560x1440 logical).
- [ ] Dashboard width becomes screen-relative: a fraction of work-area width
      clamped to [min, max] readable column (pick so the current monitor gets
      a WIDER, less cramped panel than 560 — owner hates the cramped column;
      decide a value, validate live, note it in the Run Log).
- [ ] Dashboard height: size-to-content per active tab (short tabs shrink —
      the void bug). `resize(sizeHint())` after content swap, per invariant.
- [ ] Tests: new `tests/test_metrics.py` — unit derivation for 1080/1440/2160
      logical heights; px() rounding; fallback-without-configure; scale
      monotonicity (xs < sm < md < lg < xl); a Dashboard test asserting width
      within the clamp for the offscreen screen and that GPU/System panel
      height < OpenRouter panel height after switching (the void fix).
- [ ] Migrate the OpenRouter page surfaces this checklist touches (items 2-8
      migrate their own widgets as they go; item 1 only has to migrate the
      dashboard shell: width, root margins, section gaps, nav rail width,
      header heights).
- [ ] GATE: dashboard shell on every tab (OpenRouter, Claude, GPU, System,
      Settings) — screenshot each, audit gaps/width/height-to-content.
- [ ] GATE: no regression on the 20-point AGENTS checklist basics (open,
      close, click-inside stays, click-outside per setting, scroll, tray
      toggle).
- [ ] Commit (ritual) before starting item 2.

## ITEM 2 — SPEND WIDGET: kill the graph, redesign the display, keep receipts

Decision from owner: the graph is gone (not worth fixing), the lasso/drag
breakdown modal attached to it is gone, receipts stay.

- [ ] Delete the chart: stacked-band painting, reveal animation, spike
      caret/glow, lasso (drag/press/release handlers, selection painting,
      `_x_to_bucket_index`, `_selection_*`), the axis lane, `spike_clicked` /
      `spike_selected` signals and their dashboard wiring.
- [ ] Delete the breakdown modal path end-to-end: `AutopsyStripWidget`,
      `build_autopsy_html`, `autopsy_accent_hex`, dashboard
      `show_autopsy` / `_on_spend_spike_*` / `fetch_autopsy_requested`
      plumbing, the worker fetch (`get_autopsy`) and `build_autopsy` +
      `AutopsyReport`/`AutopsyRow` IF no other feature consumes them (grep
      first; delete tests with them: test_autopsy.py widget+html parts; keep
      any pure helpers other code uses, e.g. `_parse_iso_utc` if reused).
- [ ] Reanalyze the spend data actually available per day/model:
      total_usage, request_count, prompt/completion/reasoning/cached tokens,
      usage_cache credits. Design a chartless spend display that answers, in
      order: how much this week, how is it trending (today vs the daily
      average or yesterday), which models cost what. Proposed shape (adjust
      with judgment): a compact stat row (7-day total, today, avg/day) +
      the merged model list (swatch, name, $/call, total, share) exactly as
      built — receipts stay the click-through.
- [ ] Whatever the design: no ellipsized data, aligned numeric columns,
      spacing from the theme scale, palette colors only, plain-English labels.
- [ ] Keep `receipt_for` lookup + receipt paper modal untouched (validated
      earlier today; re-GATE it anyway after the surgery).
- [ ] Remove now-dead spend_model code (spike fields if the display no longer
      uses them — check SpendSpectrumData consumers; keep builders the
      receipts/rebate/ghosts/budget still ride).
- [ ] Update tests: delete chart-geometry tests, keep/extend list+receipt
      tests, add tests for the new stat row values (pure math from fixture
      rows: total, today, avg/day, trend direction).
- [ ] GATE: spend widget populated state; empty state ($0 week); locked state
      (no mgmt key: padlock chrome, zero fake numbers).
- [ ] GATE: receipt modal opens from a row, paper audit (columns, barcode,
      even margins), popup hugs the paper.
- [ ] Commit (ritual).

## ITEM 3 — CACHING REBATE WIDGET: broken, truncated, unclear. Scratch redesign.

- [ ] FIRST: zoomed screenshot of the current widget. Document every defect
      seen (known already: header text truncates to "CACHING REBA"; the arc
      gauge is clipped mid-figure; "2.1K rsn tok / tokens, not $" is
      gibberish; "-$0.23" with a green arrow reads ambiguous). Save the
      before-shot path in the Run Log.
- [ ] Open its modal (click the strip). Zoomed screenshot. Audit + document.
- [ ] Reanalyze the cache data available: usage_cache ($ credited back),
      cached_tokens, cache_hit_rate, reasoning_tokens, per model and per day.
      Decide the ONE story worth telling (proposal: "caching saved you $X
      this week (N% of input tokens came from cache)" — one line, one number,
      one secondary stat; per-model detail lives in the modal).
- [ ] Scratch-rebuild the strip: no truncation at any width the panel can
      have (font-metric measure, elide only free text, never figures), theme
      scale spacing, palette colors (GREEN owns savings; nothing else green).
- [ ] Scratch-rebuild the modal: per-model cache savings table (model, cached
      tokens, hit %, $ saved), aligned columns, plain English title
      ("CACHE SAVINGS · LAST 7 DAYS"), no reasoning-token trivia unless it
      earns its line.
- [ ] Remove the torn-coupon skeuomorphism if it fights clarity (owner
      precedent: zigzag receipt edges were killed).
- [ ] Language sweep: no em-dashes, no "rebate stub", no "rsn tok".
- [ ] Tests: pure savings math from fixture rows (sum abs(usage_cache), hit
      rate weighting); widget measure-vs-paint no-clip test; locked/empty.
- [ ] GATE: strip populated/empty/locked; modal populated; before/after
      screenshots side by side in the Run Log.
- [ ] Commit (ritual).

## ITEM 4 — THE MODEL CHIPS UNDER THE REBATE ("materialized" pills): explain or remove

What it is (for the record): #13 "The Seance / GhostVeil" — chips per
(model, provider) pair that had spend this week; a "materialized" flare marks
newly appeared pairs, vanished pairs sink below a line. Clicking a chip opens
a per-pair ledger popup.

- [ ] Zoomed screenshot of the chips row as-is. Audit.
- [ ] Determine overlap with the spend model list (same models, same week,
      same click-through info?). If the chips add nothing beyond "this
      model+provider was active" -> DELETE the whole GhostVeil widget, its
      popup, its fetch (two week-queries in api_client), builders, and tests.
- [ ] If something is genuinely useful (e.g. "a NEW provider started serving
      your traffic this week" is an actionable routing signal), fold that ONE
      signal into an existing surface (a small "new" tag on the spend model
      list row, or one plain line under it) and still delete the standalone
      widget.
- [ ] Decision recorded in the Run Log with one-line rationale.
- [ ] Whatever remains: plain English, palette, scale, no dead settings keys.
- [ ] Tests updated (delete or refit).
- [ ] GATE on whichever surface survives.
- [ ] Commit (ritual, can share with item 5).

## ITEM 5 — "watching — needs a 2nd full week to spot ghosts": explain, make work, or remove

Same widget as item 4 (its young-account caption). The mechanic: it compares
this week's (model, provider) pairs against last week's to flag appeared /
vanished pairs, and needs two full weeks of history before it can say
anything, hence the caption.

- [ ] If item 4 deleted the GhostVeil, verify this line died with it and no
      orphan spacing remains -> tick and move on.
- [ ] If a fold-in survived: the young state must be silent (no placeholder
      line at all — absence of a "new" tag IS the calm state), or one plain
      line at most ("provider changes show after two weeks of history").
- [ ] No em-dashes, no "ghosts", no "watching".
- [ ] GATE + commit shared with item 4.

## ITEM 6 — BUDGET HOURGLASS + "Set a budget": make it real or make it gone

What it is (for the record): #14 — a sand-clock painting spend vs a weekly
budget with a pace tick; without a budget it shows the dashed "Set a budget"
placeholder; the caption tells the user to edit `weekly_budget` in
settings.json by hand, which is absurd.

- [ ] Zoomed screenshot of the empty state + the modal as-is. Audit both.
- [ ] Decide: is a weekly budget vs actual spend worth one widget? (Owner
      signal: probably yes IF it becomes intuitive; it has never worked for
      them.) If kept:
- [ ] Clicking "Set a budget" opens an inline input (a small QLineEdit +
      Save in the widget or a popup): dollar amount, validates > 0, writes
      `weekly_budget` to settings via the existing Settings save path,
      updates the widget immediately (no restart).
- [ ] A set budget can be edited/cleared the same way (click the widget).
- [ ] Replace the hourglass painting with the simplest honest display: one
      labeled progress bar — "BUDGET · $spent of $budget this week" with the
      pace point marked; red only when spend is ahead of pace.
- [ ] Empty state: one line "Set a weekly budget" + the input affordance.
      Kill "set weekly_budget to track burn-down".
- [ ] Modal: either delete it (the bar may say everything) or make it the
      simple math: budget, spent, remaining, days left, needed/day to stay
      under. Nothing else. Decide, record rationale.
- [ ] Language sweep (no "burn-down", no "hourglass", no em-dashes).
- [ ] Tests: pure pace math (existing budget_geometry tests refit), settings
      write round-trip, widget states (no budget / under pace / over pace).
- [ ] GATE: all three states + the input flow live (set a real test budget,
      verify the bar updates, then clear it).
- [ ] Commit (ritual).

## ITEM 7 — PINNED MODELS: keep the bones, fix the noise

Owner verdict: search/pin/fold/dropdown interactions are GOOD (do not touch
behavior). The card visual density, alignment, and the language are the
problem: too many bands/icons/numbers with no hierarchy, no whitespace,
misformatting, AI text. Modals were "good ideas" but need a reanalysis pass.

Card (PinnedModelCard) — work band by band, top to bottom:
- [ ] Inventory every band on the card as-built (crest/rank, speed, threshold
      door, waterline badges, tape/trend, fault line, uptime ribbon, provider
      rows, anything else). Screenshot + zoom EACH band; write one line each:
      what it says, is it legible, does it earn its space.
- [ ] Bands that don't earn their space: remove or demote into the model's
      dossier popup (the card shows the headline; the popup holds detail).
      Target: a card a stranger can read in 5 seconds — name, rank, price,
      speed, health, and the provider table.
- [ ] Enforce the shared left rail (`_icon_col_cx`/`_content_col_x`) and
      uniform BAND_GAP rhythm on whatever remains; scale-derived, not px.
- [ ] Provider rows: cap visible rows (e.g. top 5 + "N more" that expands or
      lives in the popup); align every numeric column; ONE accent for links;
      heat colors only where the number is actionable (uptime bad = red);
      kill rainbow-per-column coloring.
- [ ] Elision policy: model names elide, numbers NEVER elide.
- [ ] Language sweep across every painted string on the card.
- [ ] Modals reanalysis — for EACH popup reachable from the card (dossier /
      fighter card, uptime ribbon popup, threshold/door popup, waterline
      popup, drift/fault-line popup, trend popup, provider info table):
      - [ ] open it live, zoomed screenshot, audit (alignment, columns,
            spacing, truncation, color count, language)
      - [ ] fix format + language (keep the good structure; these were
            "good"), align to scale + palette
      - [ ] GATE it individually
- [ ] Tests: band inventory height formula tests updated; column alignment
      assertions for provider rows; popup smoke tests still green.
- [ ] GATE: full card (collapsed + expanded states), search dropdown overlay
      (unchanged behavior), one full pin/unpin cycle to prove no regression.
- [ ] Commit (ritual; may be several commits — one per band group is fine).

## ITEM 8 — INSIGHTS SECTION: the worst. Scratch where needed.

Work top to bottom exactly as the owner listed:

8a. THE ASSAY (the "base/sterling value" line — #15):
- [ ] Zoomed screenshot. Document the overlap/invisible text defects.
- [ ] Complete rework of the layout: decide what the assay actually says
      (value-for-money multiple of your pinned models vs a baseline). If the
      concept can't be made clear in one line + one small visual, simplify
      the concept (e.g. "MODEL VALUE · best $ per benchmark point:
      <model> (xN.N vs your average)").
- [ ] No overlapping paint. Every string measured, nothing collides at any
      panel width the scale allows.
- [ ] Language sweep ("sterling", "assay" -> plain words or the popup).
- [ ] Tests: measure/paint no-overlap assertions (rect intersections),
      value math already pure-tested (keep).
- [ ] GATE (populated + the no-data state).

8b. The three cards (week: Title Belt #16 / spend / tokens):
- [ ] Zoomed screenshot of the row. Document per-card defects (ellipsized
      data, uneven padding, "week 1 — no prior round" slop).
- [ ] Rebuild the three cards on ONE shared card template (BaseCard or a new
      small helper): identical padding, identical title style, identical
      number style, from the scale.
- [ ] Numbers never ellipsized: size the font role or shorten the format
      ($1.2k), never "...".
- [ ] "week 1 — no prior round" and siblings -> plain: "first week" or
      nothing.
- [ ] GATE (all three cards, plus their empty/young states).
8c. TOKENS ROUTED / FLIGHT RECORDER (#17):
- [ ] KEEP the per-digit odometer cards + "tok" (owner likes it). Do not
      touch its look.
- [ ] Fix the container around it: consistent line spacing from the scale,
      no truncation, plain-English labels for lifetime total / record day /
      current run; drop any stat that needs a sentence to explain.
- [ ] Language sweep.
- [ ] GATE.
8d. THE COURT (#18a) and 8e. THE CLIMB (#18b, the unreadable one):
- [ ] Zoomed screenshots first; document.
- [ ] SCRATCH both: delete the current painting wholesale.
- [ ] Rethink from the data: crowns = which task categories your models lead;
      climb = your token volume vs the app-store floor. Decide if either is
      genuinely interesting to the owner. If yes: one small, clean, readable
      card each (a plain ranked list beats a painted allegory). If no:
      remove the widget(s) and their fetches entirely. Record rationale.
- [ ] Whatever ships: scale, palette, aligned, zero truncation, plain
      English, popup only if it adds real detail.
- [ ] Tests refit for whatever survives.
- [ ] GATE each surviving surface (+ their modals if kept).
- [ ] Commit (ritual; one commit per 8a/8b/8c/8d+e is sensible).

## FINAL SWEEP (after item 8)

- [ ] Full-page scroll-through screenshots of the entire OpenRouter tab, top
      to bottom, zoom-audit every section boundary for rhythm consistency.
- [ ] Grep the touched files for leftover em-dashes in user-facing strings,
      absolute px, and off-palette hex literals; fix stragglers.
- [ ] Full suite green; run the GC stress script if any worker/paint code
      changed (TESTING.md).
- [ ] Update HANDOFF.md: what shipped, what was removed and why, what's
      deferred; keep AGENTS.md invariants intact.
- [ ] Update the memory files (phase4 direction + roadmap pointers).
- [ ] Final commit (ritual).

## RUN LOG (append one line per gate/decision: timestamp, surface, verdict)

<!-- e.g. 03:12 spend stat row GATE pass (screenshot: .../zoom_xxx.png); removed GhostVeil: duplicated spend list, no unique signal -->

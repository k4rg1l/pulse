"""
OpenRouter Pulse - User settings

User-editable JSON at %APPDATA%/OpenRouterPulse/settings.json.  Created with
sane defaults on first run.  The user can edit it by hand; the tray menu has
an "Open Settings File" entry that opens it in Notepad.

We deliberately keep this dataclass-y rather than a settings GUI for v1 — the
file is short and the user is technical.  A real dialog can come later.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path

from persistence import state_dir

log = logging.getLogger("pulse.settings")


def settings_path() -> Path:
    return state_dir() / "settings.json"


@dataclass
class Settings:
    # -- API key (optional; OPENROUTER_API_KEY env var takes precedence)
    api_key: str = ""

    # -- Management key (unlocks /api/v1/activity for v0.2 per-model spend)
    # Create one at openrouter.ai/settings/keys.  Keep this secret; it has
    # broader scope than a regular API key.
    management_api_key: str = ""

    # -- Auto top-up (set this if you've configured auto top-up on openrouter.ai)
    auto_topup_threshold: float = 0.0   # 0 disables; otherwise the trigger balance ($)
    auto_topup_amount: float = 0.0      # amount added per top-up ($)

    # -- Pinned models for per-provider health section
    # OpenRouter model IDs (e.g. "anthropic/claude-sonnet-4.5", "openai/gpt-4o").
    # Each one shows every provider serving it with live latency, uptime, price.
    tracked_models: list = field(default_factory=list)

    # -- Balance alerts (notifications)
    balance_warning: float = 5.0        # $ remaining → "Low credits" toast
    balance_critical: float = 1.0       # $ remaining → "Critical" toast

    # -- Refresh cadences (seconds)
    key_refresh_seconds: int = 60
    status_refresh_seconds: int = 120
    models_refresh_seconds: int = 1800

    # -- UI
    dismiss_on_focus_loss: bool = True  # close dashboard when user clicks elsewhere
    hotkey: str = "win+shift+o"         # global summon hotkey (empty string disables)

    # -- Sources. Pulse renders an ordered list of source section-groups; no
    # provider is privileged. `source_order` is the top-to-bottom order
    # (unknown/unlisted sources fall to the bottom). Auto-detected sources
    # appear when their data is present; set their flag False to hide them.
    source_order: list = field(default_factory=lambda: ["openrouter", "claude", "gpu", "system"])
    show_claude: bool = True            # show the Claude card if ~/.claude creds exist
    show_gpu: bool = True               # show the GPU card if an NVIDIA GPU is present
    show_system: bool = True            # show the System card (CPU/RAM/network)
    show_arena: bool = True             # show the model Arena rank-crest on pinned cards
    show_trust_seals: bool = True       # show per-provider privacy/trust seals on pinned cards
    show_speed: bool = True             # show the fleet-relative Speed Percentile band on pinned cards
    show_trend: bool = True             # show #7 THE TAPE — the week-over-week momentum cartouche in the header
    show_door: bool = True              # show #5 THE THRESHOLD — the "cheapest door" band on pinned cards
    show_uptime: bool = True            # show THE PULSE — the 73h uptime cardiogram on provider rows
    show_hidden_fees: bool = True       # show #6 THE WATERLINE — the hidden-cost iceberg under each price
    show_drift: bool = True             # show #8 THE FAULT LINE — the price-drift seismograph on the card edge
    show_spend: bool = True             # show the SPEND zone (Wave 2 #9 The Spectrum + ground-truth analytics)
    show_insights: bool = True          # show the INSIGHTS zone (Wave 3 #15 The Assay anchor + #16/#17/#18 mgmt widgets)

    # -- #14 THE HOURGLASS (budget burn-down) --
    # The budget DENOMINATOR. >0 = the real config path (a weekly spend budget,
    # in $). 0 (default) = no budget configured -> the tidy "Set a budget" state.
    # NEVER fabricate a denominator; only this field (or the credits fallback
    # below) supplies one.
    weekly_budget: float = 0.0
    # Opt-in CREDITS fallback: when weekly_budget==0 AND this is True, derive the
    # burn-down from GET /api/v1/credits (budget = total_credits, burned =
    # total_usage — the only live spend-cap signal). Off by default so a credit
    # balance is never silently presented as a "budget".
    show_credit_burndown: bool = False

    # -- UI overhaul (nav-rail command center) --
    default_source: str = "openrouter"  # which tab opens when the dashboard is shown
    enable_animations: bool = True      # tab transitions / count-ups / pulses (off = instant)

    @classmethod
    def load(cls) -> "Settings":
        path = settings_path()
        if not path.exists():
            inst = cls()
            inst.save()
            return inst
        try:
            # utf-8-sig tolerates a BOM (PowerShell 5.1 writes one with
            # `Set-Content -Encoding utf8`); falls back to plain UTF-8.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            log.warning("load error: %s; using defaults", e)
            return cls()
        # Tolerate unknown / missing keys gracefully
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)

    def save(self) -> None:
        path = settings_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------
    #  Convenience
    # ------------------------------------------------------------------

    @property
    def autotopup_enabled(self) -> bool:
        return self.auto_topup_threshold > 0 and self.auto_topup_amount > 0

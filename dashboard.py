"""
OpenRouter Pulse - Dashboard Window
The main popup panel that appears from the system tray.
"""
import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import webbrowser
import time
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QPushButton, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QSizePolicy, QApplication, QGridLayout, QStackedWidget,
)
from PySide6.QtCore import (
    Qt, QTimer, QEasingCurve, QPoint, QSize, Signal, QRectF, QEvent,
    QPropertyAnimation,
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPainterPath, QCursor,
    QLinearGradient, QIcon, QPixmap, QImage,
)

import theme_controller
from theme import Colors, Fonts, STYLESHEET, accent_for
from config import (
    DASHBOARD_WIDTH, DASHBOARD_MIN_HEIGHT, DASHBOARD_MAX_HEIGHT,
    APP_NAME, APP_VERSION, NAV_RAIL_WIDTH, logo_path,
    OPENROUTER_DASHBOARD_URL, OPENROUTER_CREDITS_URL, OPENROUTER_MODELS_URL,
)
from widgets import (
    ArcGauge, SectionHeader, GradientStrip,
    ErrorBanner, PinnedModelCard, PinnedColumnHeader,
    ModelPicker, ProviderPopup, SpendSpectrum,
    build_receipt_html, receipt_accent_hex,
    RebateStub, build_rebate_html, rebate_accent_hex,
    GhostVeil, build_seance_html, ghost_accent_hex,
    BudgetHourglass, build_budget_html, budget_accent_hex,
    build_autopsy_html, autopsy_accent_hex,
    ValueAssayWidget, build_assay_certificate_html, assay_accent_hex,
    ModelOfWeekBelt, build_week_dossier_html,
    TokenRecorder, build_recorder_dossier_html,
    TaskCourt, build_court_dossier_html, build_climb_dossier_html,
)
import value_assay
from nav_rail import NavRail
from source_panel import SourcePanel

log = logging.getLogger("pulse.dashboard")


def _fmt_duration(days):
    if days is None or days <= 0:
        return "--"
    if days < 1 / 24:
        return f"{int(days * 1440)} min"
    if days < 1:
        return f"{int(days * 24)} hr"
    if days < 30:
        return f"{days:.0f} day{'s' if days >= 1.5 else ''}"
    if days < 365:
        return f"{days / 30:.1f} mo"
    return f"{days / 365:.1f} yr"


def _fmt_money(x):
    if x is None:
        return "--"
    if x >= 1000:
        return f"${x:,.0f}"
    return f"${x:.2f}"


class LinkButton(QPushButton):
    def __init__(self, text, url, parent=None):
        super().__init__(text, parent)
        self._url = url
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("""
            QPushButton {
                background: #242442;
                color: #00d2ff;
                border: 1px solid #323250;
                border-radius: 6px;
                padding: 6px 12px;
                font-family: "Segoe UI";
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #2e2e52;
                border-color: #00d2ff;
                color: #7b2ff7;
            }
        """)
        self.clicked.connect(lambda: webbrowser.open(self._url))


class Dashboard(QWidget):
    """The main dashboard popup window."""

    refresh_requested = Signal()
    fetch_uptime_requested = Signal(str, str)   # (model_id, permaslug) — THE PULSE (#3)
    fetch_autopsy_requested = Signal(str, str)  # (t0_iso, t1_iso) — THE AUTOPSY (#11 lasso)

    def __init__(self, history=None, settings=None, parent=None):
        super().__init__(parent)
        self.setObjectName("DashboardWindow")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.setFixedWidth(DASHBOARD_WIDTH)
        self.setMinimumHeight(DASHBOARD_MIN_HEIGHT)
        self.setMaximumHeight(DASHBOARD_MAX_HEIGHT)

        self._tray_icon = None
        self._history = history
        self._settings = settings
        self._last_key_info = None

        # Provider info popup (lazy)
        self._provider_popup = None
        self._popup_model_id = None
        self._popup_just_hidden_at = 0.0

        # Arena (model benchmark standings), distributed to pinned cards
        self._benchmark_board = None
        # The Ledger (per-provider privacy/trust posture), distributed to cards
        self._provider_trust_book = None
        # #5 THE THRESHOLD gate (settings.show_door). Cached so cards created
        # later inherit it; #5 has no fetch, so this is its only gate point.
        self._show_door = bool(getattr(settings, "show_door", True)) if settings else True
        # #6 THE WATERLINE gate (settings.show_hidden_fees). Same idiom: #6 has no
        # fetch (rides the endpoints payload), so this is its only gate point.
        self._show_fees = bool(getattr(settings, "show_hidden_fees", True)) if settings else True
        # #8 THE FAULT LINE gate (settings.show_drift) + the persisted price store.
        # #8 rides the endpoints diff (no fetch); the dashboard OWNS the store
        # (the baseline-update policy is stateful — decision C says it lives in
        # the orchestration layer, NOT the card). Loaded once; persisted after
        # each observe()/acknowledge() (atomic, BOM-tolerant — mirrors history).
        self._show_drift = bool(getattr(settings, "show_drift", True)) if settings else True
        from price_drift import PriceSnapshotStore
        self._price_store = PriceSnapshotStore.load()
        self._drift_popup_ctx = None     # (model_id, anchor_y) or None
        # Speed Percentile (#4): the fleet performance board + the slug→permaslug
        # map needed to look a pinned model up in it. Both distributed to cards.
        self._speed_board = None
        self._permaslug_resolver = None
        # #7 THE TAPE: the week-over-week request-momentum board (TrendBoard),
        # permaslug-keyed; resolved per pinned card. Kept last-good.
        self._trend_board = None
        # THE PULSE (#3): per-model {ep_ident: UptimeHistory}, kept last-good so a
        # transient fetch failure never blanks a card's cardiogram.
        self._uptime_by_model = {}
        self._uptime_popup_ctx = None    # (model_id, ident, anchor_y) or None
        # Provider logos (#2b): the shared cache + the open-dossier context so a
        # logo that arrives after the dossier opens can refresh it in place.
        self._logo_store = None
        self._trust_popup_ctx = None     # (model_id, ident, anchor_y) or None
        # Wave 3 INSIGHTS zone: the keep-last-good board for the mgmt widgets
        # (#16/#17/#18) + the always-live #15 anchor. #15 rides _distribute_value
        # off self._benchmark_board + each card's endpoints (NO new fetch); the
        # board scaffold (set in _build_insights_section) carries the mgmt slots.
        self._insights_board = None
        self._value_assay = None         # #15 ValueAssayWidget (None if zone off)

        self._build_ui()

        # Click-outside-to-dismiss via foreground-window polling.  See
        # _check_outside_click for the why and how.
        self._dismiss_enabled = bool(
            self._settings and getattr(self._settings, "dismiss_on_focus_loss", False)
        )
        self._outside_click_timer = QTimer(self)
        self._outside_click_timer.timeout.connect(self._check_outside_click)
        self._show_foreground = None

    # ------------------------------------------------------------------
    #  UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        self._container = QFrame(self)
        self._container.setObjectName("DashboardWindow")
        self._container.setStyleSheet(STYLESHEET)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self._container.setGraphicsEffect(shadow)

        inner = QVBoxLayout(self._container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self.gradient_strip = GradientStrip(self._container)
        inner.addWidget(self.gradient_strip)

        # Command center: left nav-rail + switchable panel stack. Each source
        # is an equal peer on the rail with its own full panel in the stack.
        self._active_id = None
        self._panels = {}        # source_id -> SourcePanel
        self._source_cards = {}  # source_id -> card with render() (for update_source)
        self._tab_specs = []     # registration order: {id, name, accent, logo}

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.nav_rail = NavRail(self._container)
        self.nav_rail.source_selected.connect(self.set_active_source)
        self.nav_rail.refresh_clicked.connect(self._on_refresh)
        body.addWidget(self.nav_rail)

        self._stack = QStackedWidget(self._container)
        body.addWidget(self._stack, 1)
        inner.addLayout(body, 1)

        root.addWidget(self._container)

        # OpenRouter is registered like any source (no privilege).
        self._register_openrouter()

    # ------------------------------------------------------------------
    #  Tab registration + switching
    # ------------------------------------------------------------------

    def _register_openrouter(self):
        body = self._build_openrouter_group()
        accent = accent_for("openrouter")
        panel = SourcePanel("openrouter", "OpenRouter", accent,
                            logo_path("openrouter"), body, scrollable=True, parent=self)
        panel.refresh_clicked.connect(self._on_refresh)
        panel.close_clicked.connect(self.hide)
        panel.scrolled.connect(self._on_dashboard_scrolled)
        self._panels["openrouter"] = panel
        self._stack.addWidget(panel)
        self._tab_specs.append({"id": "openrouter", "name": "OpenRouter",
                                "accent": accent, "logo": logo_path("openrouter")})
        self._sync_rail()

    def register_source_tab(self, source_id, display_name, accent, card):
        """Add a peer source (Claude/GPU/System) as a tab. Main thread only."""
        panel = SourcePanel(source_id, display_name, accent,
                            logo_path(source_id), card, scrollable=False, parent=self)
        panel.refresh_clicked.connect(self._on_refresh)
        panel.close_clicked.connect(self.hide)
        self._panels[source_id] = panel
        self._source_cards[source_id] = card
        self._stack.addWidget(panel)
        self._tab_specs.append({"id": source_id, "name": display_name,
                                "accent": accent, "logo": logo_path(source_id)})
        self._sync_rail()

    def register_settings_tab(self):
        """Register Pulse's own Settings tab (the rail's bottom gear). Call
        after all source tabs exist so the default-tab picker is complete."""
        if "settings" in self._panels:
            return
        from settings_panel import SettingsPanel
        tab_options = [(s["id"], s["name"]) for s in self._tab_specs]
        handlers = {
            "animations": self._on_set_animations,
            "dismiss": self._on_set_dismiss,
            "default_source": lambda v: None,  # persisted; applies on next open
            "open_json": self._open_settings_file,
        }
        body = SettingsPanel(self._settings, tab_options, handlers, self)
        accent = accent_for("settings")
        panel = SourcePanel("settings", "Settings", accent,
                            logo_path("settings"), body, scrollable=True, parent=self)
        panel.refresh_clicked.connect(self._on_refresh)
        panel.close_clicked.connect(self.hide)
        self._panels["settings"] = panel
        self._stack.addWidget(panel)
        self.nav_rail._settings_accent = accent

    def _on_set_animations(self, value):
        import anim
        anim.set_enabled(bool(value))

    def _on_set_dismiss(self, value):
        self._dismiss_enabled = bool(value)
        # The poller's lifecycle must track the LIVE setting: leaving it
        # running after a toggle-off let it orphan across a close/reopen with
        # a stale foreground ref, insta-hiding every subsequent open.
        if not self._dismiss_enabled:
            self._outside_click_timer.stop()
            self._show_foreground = None
        elif self.isVisible():
            self._arm_outside_click_dismiss()

    def _sync_rail(self):
        order = self._source_order()
        by_id = {s["id"]: s for s in self._tab_specs}
        ordered = [by_id[i] for i in order if i in by_id]
        ordered += [s for s in self._tab_specs if s["id"] not in order]
        self.nav_rail.set_sources(ordered)
        if self._active_id is None and ordered:
            self.set_active_source(ordered[0]["id"], animate=False)

    def set_active_source(self, source_id, animate=True):
        panel = self._panels.get(source_id)
        if panel is None:
            return
        changed = (self._active_id != source_id)
        self._active_id = source_id
        self._stack.setCurrentWidget(panel)
        self.nav_rail.set_active(source_id, animate=animate)
        theme_controller.set_accent(accent_for(source_id), animate=animate)
        # leaving a panel: dismiss any OpenRouter overlays
        self._hide_provider_popup()
        try:
            if self.model_picker.is_open():
                self.model_picker._close()
        except Exception:
            pass
        if source_id == "openrouter":
            panel.reset_scroll()
        if animate and changed:
            self._animate_panel_in(panel)

    def _animate_panel_in(self, panel):
        from anim import ANIMATIONS_ON
        if not ANIMATIONS_ON:
            return
        try:
            eff = QGraphicsOpacityEffect(panel)
            panel.setGraphicsEffect(eff)
            a = QPropertyAnimation(eff, b"opacity", self)
            a.setDuration(190)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.finished.connect(lambda: panel.setGraphicsEffect(None))
            a.start()
            self._panel_anim = a  # keep a reference so it isn't GC'd mid-run
        except Exception:
            pass

    def set_source_status(self, source_id, severity):
        if getattr(self, "nav_rail", None) is not None:
            self.nav_rail.set_status(source_id, severity)

    def _open_settings_file(self):
        from settings import settings_path
        try:
            os.startfile(str(settings_path()))
        except Exception:
            try:
                webbrowser.open(str(settings_path()))
            except Exception:
                pass

    def _build_gauge_section(self):
        self._or_layout.addWidget(SectionHeader("Credit Balance"))

        gauge_row = QHBoxLayout()
        gauge_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gauge = ArcGauge(self)
        gauge_row.addWidget(self.gauge)
        self._or_layout.addLayout(gauge_row)

        self._autotopup_label = QLabel("")
        self._autotopup_label.setFont(Fonts.tiny())
        self._autotopup_label.setStyleSheet("color: #00d2ff;")
        self._autotopup_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._or_layout.addWidget(self._autotopup_label)
        self._update_autotopup_label()

    def _update_autotopup_label(self):
        if self._settings and self._settings.autotopup_enabled:
            t = self._settings.auto_topup_threshold
            a = self._settings.auto_topup_amount
            self._autotopup_label.setText(
                f"⚡  Auto top-up: +${a:g} when balance drops below ${t:g}"
            )
            self._autotopup_label.setVisible(True)
        else:
            self._autotopup_label.setVisible(False)

    def _build_spend_section(self):
        """Wave 2 SPEND zone — ground-truth spend from the analytics API,
        REPLACING the estimated Usage + Burn Rate sections. Hero is #9 The
        Spectrum; later Spend features (#10-14) attach beneath it inside the
        same container. A collapsible header (like Pinned Models) carries a live
        one-line headline in its right_label ("$X · 7d" / "locked")."""
        self._spend_header = SectionHeader("Spend")
        self._spend_header.set_collapsible(True)
        self._spend_header.clicked.connect(self._toggle_spend_collapsed)
        self._spend_collapsed = False
        self._or_layout.addWidget(self._spend_header)

        # One container holds the whole zone (so the header can collapse it and
        # later strips just addWidget below the Spectrum). spacing=10 per the IA.
        self._spend_container = QWidget()
        self._spend_container.setStyleSheet("background: transparent;")
        spend_layout = QVBoxLayout(self._spend_container)
        spend_layout.setContentsMargins(0, 0, 0, 0)
        spend_layout.setSpacing(10)

        # #9 + #10 share ONE card: the Spectrum's model list carries the till
        # roll's $/call + price-drift stamp per row (one list, one click target
        # per model -> the full thermal receipt).
        self.spend_spectrum = SpendSpectrum(self)
        self.spend_spectrum.band_clicked.connect(self._on_spend_band_clicked)
        self.spend_spectrum.spike_clicked.connect(self._on_spend_spike_clicked)
        # #11 THE AUTOPSY — a lasso release across the chart body fires the hourly
        # drill-down (interaction-fired, debounced dossier on return).
        self.spend_spectrum.spike_selected.connect(self._on_spend_spike_selected)
        spend_layout.addWidget(self.spend_spectrum)

        # #12 THE REBATE STUB — the torn money-back coupon directly below the
        # receipts (the discount line at the foot of the tape). Owns GREEN. The
        # whole strip opens the per-model rebate breakdown popup.
        self.spend_savings = RebateStub(self)
        self.spend_savings.rebate_clicked.connect(self._on_savings_clicked)
        spend_layout.addWidget(self.spend_savings)

        # #13 THE SÉANCE — the ghost-model veil directly below the rebate stub.
        # Living (model,provider) pairs glow above a membrane; vanished pairs
        # sink below; an appeared pair flares in with a materialize ring. Always
        # alive (a living roster + a calm caption even at zero ghosts). Click a
        # glyph -> the per-pair séance-ledger popup.
        self.spend_ghosts = GhostVeil(self)
        self.spend_ghosts.ghost_clicked.connect(self._on_ghost_clicked)
        spend_layout.addWidget(self.spend_ghosts)

        # #14 THE HOURGLASS — the sand-clock budget burn-down CLOSES the section,
        # rhyming UPWARD with the Credit Balance ArcGauge to bookend the panel.
        # The TOP bulb is remaining budget, the drained BOTTOM is spend, raced
        # against a pace tick; the pinch reddens when AHEAD OF PACE. The live
        # default (no weekly_budget + credits opt-in off) is the "Set a budget"
        # state. Click -> the burn-down dossier popup.
        self.spend_budget = BudgetHourglass(self)
        self.spend_budget.budget_clicked.connect(self._on_budget_clicked)
        spend_layout.addWidget(self.spend_budget)

        self._or_layout.addWidget(self._spend_container)

        # Keep-last-good store + initial state. On a keyless machine the worker
        # emits None and update_spend paints the locked state; here the live
        # fetch will replace it with real data.
        self._spend_board = None
        from api_client import AnalyticsClient
        # Cheap unlocked probe (no network): decides locked vs awaiting-data.
        try:
            self._spend_unlocked = bool(AnalyticsClient().unlocked)
        except Exception:
            self._spend_unlocked = False
        if not self._spend_unlocked:
            self.spend_spectrum.set_locked()
            self.spend_savings.set_locked()
            self.spend_ghosts.set_locked()
            self.spend_budget.set_locked()
            self._spend_header.right_label.setText("locked")

    def _toggle_spend_collapsed(self):
        self._spend_collapsed = not self._spend_collapsed
        self._spend_header.set_collapsed(self._spend_collapsed)
        self._spend_container.setVisible(not self._spend_collapsed)

    def _build_insights_section(self):
        """Wave 3 INSIGHTS zone — a NEW collapsible section mounted BETWEEN the
        Models board and Quick Links, built byte-for-byte on the Spend zone. Its
        first/top widget is #15 THE ASSAY (the always-live USER-key anchor that
        renders even when the mgmt features below are locked, so the zone is never
        blank). #16/#17/#18 addWidget below #15 LATER in this same container (the
        scaffold leaves their slots open). A collapsible header carries a live
        one-line headline in its right_label ("standard: <model> ×N.N" / "locked")."""
        self._insights_header = SectionHeader("Insights")
        self._insights_header.set_collapsible(True)
        self._insights_header.clicked.connect(self._toggle_insights_collapsed)
        self._insights_collapsed = False
        self._or_layout.addWidget(self._insights_header)

        # One container holds the whole zone (so the header can collapse it and
        # the later mgmt strips just addWidget below #15). spacing=10 per the IA.
        self._insights_container = QWidget()
        self._insights_container.setStyleSheet("background: transparent;")
        insights_layout = QVBoxLayout(self._insights_container)
        insights_layout.setContentsMargins(0, 0, 0, 0)
        insights_layout.setSpacing(10)

        # #15 THE ASSAY — the TOP widget, the always-on USER-key value anchor.
        # It rides _distribute_value() (no mgmt key, no new fetch), so it renders
        # live regardless of the mgmt-locked state of #16/#17/#18 below it.
        self._value_assay = ValueAssayWidget(self)
        self._value_assay.coin_clicked.connect(self._on_assay_clicked)
        self._value_assay.metric_cycled.connect(self._on_assay_metric_cycled)
        insights_layout.addWidget(self._value_assay)

        # #16 THE TITLE BELT — SECOND widget: the week's CHAMPION headline. Mgmt-
        # gated (rides fetch_insights -> board.week); update_insights below already
        # routes board.week -> set_data and the locked path -> set_locked (guarded
        # by getattr) so this is the only mount line needed. Wire the shared logo
        # store if it has already landed (else set_logo_store does it later).
        self._week_belt = ModelOfWeekBelt(self)
        self._week_belt.week_clicked.connect(self._on_week_clicked)
        if getattr(self, "_logo_store", None) is not None:
            self._week_belt.set_logo_store(self._logo_store)
        insights_layout.addWidget(self._week_belt)

        # #17 THE FLIGHT RECORDER — THIRD widget: the LIFETIME odometer + the
        # record "black-box" day + the active-day runway streak. Mgmt-gated (rides
        # fetch_insights -> board.recorder; update_insights below already routes
        # board.recorder -> set_data and the locked path -> set_locked). It is THE
        # Insights zone's count-up owner (the odometer roll); the warm brass lane
        # is distinct from #16's trophy gold.
        self._token_recorder = TokenRecorder(self)
        self._token_recorder.recorder_clicked.connect(self._on_recorder_clicked)
        insights_layout.addWidget(self._token_recorder)

        # #18 THE COURT & THE CLIMB — FOURTH widget, the wide CLOSER: the WORLD's
        # task crown (taste-vs-world) over an HONEST base-camp apps ladder (the
        # user pinned in the valley, never an 'out-tokened' claim). MIXED auth,
        # degrades per-half (the court half needs user/mgmt; the climb half is
        # noauth and always renders). Rides fetch_insights -> board.court;
        # update_insights below already routes board.court -> set_data and the
        # locked path -> set_locked (guarded by getattr). HERALDIC gold-on-indigo
        # + the single ember 'you' thread, separable from every sibling lane.
        self._task_court = TaskCourt(self)
        self._task_court.court_clicked.connect(self._on_court_clicked)
        self._task_court.climb_clicked.connect(self._on_climb_clicked)
        insights_layout.addWidget(self._task_court)

        self._or_layout.addWidget(self._insights_container)

        # Keep-last-good board + the cheap unlocked probe (mirrors Spend). #15 is
        # USER-key so it ignores this; the probe decides locked-vs-awaiting for
        # the mgmt features (#16/#17/#18) when they are added.
        from api_client import AnalyticsClient
        try:
            self._insights_unlocked = bool(AnalyticsClient().unlocked)
        except Exception:
            self._insights_unlocked = False
        # #15 renders as soon as its inputs land; push whatever is already in
        # memory (benchmarks/endpoints may have arrived before the section built).
        self._distribute_value()

    def _toggle_insights_collapsed(self):
        self._insights_collapsed = not self._insights_collapsed
        self._insights_header.set_collapsed(self._insights_collapsed)
        self._insights_container.setVisible(not self._insights_collapsed)

    def _on_spend_band_clicked(self, model_id, global_anchor):
        # #9's legend/band row is an entry point into #10's receipt for the same
        # model (shared IA — the band and the stub open the SAME thermal receipt).
        self._on_receipt_clicked(model_id, global_anchor)

    def _on_spend_spike_clicked(self, t0_iso, t1_iso):
        # #11: a TAP on the spike column = a single-bucket autopsy. Fires the same
        # interaction-fired hourly drill-down as a lasso (the worker clamps the
        # bucket label to its hours).
        log.debug("spend spike tapped: %s..%s", t0_iso, t1_iso)
        self.fetch_autopsy_requested.emit(t0_iso, t1_iso)

    def _on_spend_spike_selected(self, t0_iso, t1_iso):
        # #11: a lasso release across the chart body -> the windowed autopsy.
        log.debug("spend lasso: %s..%s", t0_iso, t1_iso)
        self.fetch_autopsy_requested.emit(t0_iso, t1_iso)

    def show_autopsy(self, token, report):
        """#11 THE AUTOPSY: the worker returned the forensic report for the
        lassoed/tapped window (or None on failure/locked). Render it into the
        shared ProviderPopup keyed 'autopsy:{token}', replicating the established
        just-closed/_popup_just_hidden_at debounce so the release that FIRED the
        query doesn't immediately fight the app-wide event-filter that opens the
        dossier. CRIMSON accent (the forensic role). None -> no popup."""
        if report is None:
            return
        popup = self._ensure_provider_popup()
        key = "autopsy:" + str(token)
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        html_str = build_autopsy_html(report)
        if not html_str:
            return
        # Anchor the dossier beside the Spectrum's spike caret height (a stable
        # point near the chart top) so it opens next to the interrogated chart.
        anchor_y = self._dashboard_global_rect().top() + 120
        sp = getattr(self, "spend_spectrum", None)
        if sp is not None:
            try:
                anchor_y = int(sp.mapToGlobal(sp.rect().center()).y())
            except Exception:
                pass
        popup.set_accent(autopsy_accent_hex(report))
        popup.show_beside(html_str, self._dashboard_global_rect(), int(anchor_y))
        self._popup_model_id = key

    def _on_receipt_clicked(self, model_id, global_anchor):
        """#10 THE TILL ROLL: a stub (or #9's legend row) was clicked -> render
        the full thermal RECEIPT to a pixmap and show it in the shared popup.
        Mirrors _on_uptime_clicked (keyed, debounced, toggle-hide). When locked /
        no receipt on file, the popup reads '— NO RECEIPT ON FILE —'."""
        popup = self._ensure_provider_popup()
        key = "receipt:" + model_id
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        receipt = None
        if getattr(self, "spend_spectrum", None) is not None:
            receipt = self.spend_spectrum.receipt_for(model_id)
        html_str = build_receipt_html(receipt)
        if not html_str:
            return
        anchor_y = int(global_anchor.y())
        popup.set_accent(receipt_accent_hex(receipt))
        popup.show_beside(html_str, self._dashboard_global_rect(), anchor_y)
        self._popup_model_id = key

    def _on_savings_clicked(self, global_anchor):
        """#12 THE REBATE STUB: the strip was clicked -> render the per-model
        rebate breakdown to a pixmap and show it in the shared popup. Mirrors
        _on_receipt_clicked (keyed, debounced, toggle-hide). GREEN accent (the
        savings role). No popup when locked / no cache rebate in range."""
        popup = self._ensure_provider_popup()
        key = "savings"
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        savings = None
        board = getattr(self, "_spend_board", None)
        if board is not None:
            savings = board.savings
        html_str = build_rebate_html(savings)
        if not html_str:
            return
        anchor_y = int(global_anchor.y())
        popup.set_accent(rebate_accent_hex(savings))
        popup.show_beside(html_str, self._dashboard_global_rect(), anchor_y)
        self._popup_model_id = key

    def _on_ghost_clicked(self, pair_key, global_anchor):
        """#13 THE SÉANCE: a veil glyph (a (model,provider) pair) was clicked ->
        render its séance-ledger to a pixmap and show it in the shared popup.
        Mirrors _on_savings_clicked (keyed, debounced, toggle-hide). The accent is
        the model's SHARED spectrum color (decision D — never crimson)."""
        popup = self._ensure_provider_popup()
        key = "ghost:" + "|".join(pair_key)
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        board = getattr(self, "_spend_board", None)
        diff = board.ghosts if board is not None else None
        entry = None
        if diff is not None:
            for e in (list(diff.living) + list(diff.vanished)
                      + list(diff.appeared)):
                if e.pair.key == tuple(pair_key):
                    entry = e
                    break
        html_str = build_seance_html(entry, diff)
        if not html_str:
            return
        anchor_y = int(global_anchor.y())
        # ghost_accent_hex(None) safely falls back to the panel accent.
        popup.set_accent(ghost_accent_hex(entry))
        popup.show_beside(html_str, self._dashboard_global_rect(), anchor_y)
        self._popup_model_id = key

    def _on_budget_clicked(self, global_anchor):
        """#14 THE HOURGLASS: the budget glass was clicked -> render the burn-down
        dossier (the 7-bar daily-spend column chart + the pace line + the
        projection math) to a pixmap and show it in the shared popup. Mirrors
        _on_ghost_clicked (keyed, debounced, toggle-hide). RED accent when over
        pace (decision C), else the panel accent. Shows a 'No budget set' card
        when there's no denominator (no fabricated numbers)."""
        popup = self._ensure_provider_popup()
        key = "budget"
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        board = getattr(self, "_spend_board", None)
        budget = board.budget if board is not None else None
        html_str = build_budget_html(budget)
        if not html_str:
            return
        anchor_y = int(global_anchor.y())
        # budget_accent_hex(None) safely falls back to the panel accent.
        popup.set_accent(budget_accent_hex(budget))
        popup.show_beside(html_str, self._dashboard_global_rect(), anchor_y)
        self._popup_model_id = key

    # ---- Wave 3 #15 THE ASSAY click-through ----

    def _on_assay_clicked(self, model_id, anchor_y):
        """#15 THE ASSAY: a coin was tapped -> render its 3-category assay
        CERTIFICATE to a pixmap and show it in the shared popup. Mirrors
        _on_savings_clicked (keyed 'insight:assay:'+model_id, debounced via the
        _popup_just_hidden_at<0.15 just-closed guard, toggle-hide). GOLD accent
        for the value STANDARD, else the model's shared Spend hue. No popup when
        there's no assay row for the coin."""
        popup = self._ensure_provider_popup()
        key = "insight:assay:" + model_id
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        w = getattr(self, "_value_assay", None)
        model = w.result_for(model_id) if w is not None else None
        result = w._result if w is not None else None
        html_str = build_assay_certificate_html(model, result)
        if not html_str:
            return
        popup.set_accent(assay_accent_hex(model))
        popup.show_beside(html_str, self._dashboard_global_rect(), int(anchor_y))
        self._popup_model_id = key

    def _on_assay_metric_cycled(self, next_metric):
        """#15: the metric label was tapped -> recompute the value standard on the
        next AA index (intelligence->coding->agentic) and re-mint the coins. The
        widget already holds the new active metric request; _distribute_value
        reads it via current_metric(), so we just set it and re-assay."""
        w = getattr(self, "_value_assay", None)
        if w is None:
            return
        w._metric = next_metric
        self._distribute_value()

    # ---- Wave 3 #16 THE TITLE BELT click-through ----

    def _on_week_clicked(self, anchor_y):
        """#16 THE TITLE BELT: the belt was tapped -> render the week dossier to a
        pixmap and show it in the shared popup. Mirrors _on_assay_clicked (keyed
        'insight:week', debounced via the _popup_just_hidden_at<0.15 just-closed
        guard, toggle-hide). GOLD accent (the championship lane). No popup when
        there's no champion this week."""
        popup = self._ensure_provider_popup()
        key = "insight:week"
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        w = getattr(self, "_week_belt", None)
        mow = getattr(w, "_mow", None) if w is not None else None
        html_str = build_week_dossier_html(mow)
        if not html_str or (mow is not None and mow.is_empty):
            return
        popup.set_accent("#E8C46A")          # championship gold
        popup.show_beside(html_str, self._dashboard_global_rect(), int(anchor_y))
        self._popup_model_id = key

    # ---- Wave 3 #17 THE FLIGHT RECORDER click-through ----

    def _on_recorder_clicked(self, anchor_y):
        """#17 THE FLIGHT RECORDER: the instrument was tapped -> render the flight
        dossier (the daily-spend timeline + the lifetime totals + the record day +
        the streak definition) to a pixmap and show it in the shared popup.
        Mirrors _on_week_clicked (keyed 'insight:recorder', debounced via the
        _popup_just_hidden_at<0.15 just-closed guard, toggle-hide). BRASS/AMBER
        accent (the flight-recorder lane). No popup when there's no traffic."""
        popup = self._ensure_provider_popup()
        key = "insight:recorder"
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        w = getattr(self, "_token_recorder", None)
        rec = getattr(w, "_rec", None) if w is not None else None
        html_str = build_recorder_dossier_html(rec)
        if not html_str or (rec is not None and rec.is_empty):
            return
        popup.set_accent("#E8A23D")          # brass/amber (decision E)
        popup.show_beside(html_str, self._dashboard_global_rect(), int(anchor_y))
        self._popup_model_id = key

    # ---- Wave 3 #18 THE COURT & THE CLIMB click-through ----

    def _on_court_clicked(self, anchor_y):
        """#18 THE COURT: the court band was tapped -> render the world-task
        dossier (top-3 world models per macro + the macro shares + the honest
        taste-vs-world ember line) to a pixmap and show it in the shared popup.
        Mirrors _on_recorder_clicked (keyed 'insight:court', debounced via the
        _popup_just_hidden_at<0.15 just-closed guard, toggle-hide). GOLD accent
        (the world's verdict). No popup when the world board is unavailable."""
        popup = self._ensure_provider_popup()
        key = "insight:court"
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        w = getattr(self, "_task_court", None)
        cc = getattr(w, "_cc", None) if w is not None else None
        if cc is None or not cc.court_available:
            return
        html_str = build_court_dossier_html(cc, getattr(cc, "task_board", None))
        if not html_str:
            return
        popup.set_accent("#E8C45A")          # heraldic gold (the world's verdict)
        popup.show_beside(html_str, self._dashboard_global_rect(), int(anchor_y))
        self._popup_model_id = key

    def _on_climb_clicked(self, anchor_y):
        """#18 THE CLIMB: the climb band was tapped -> render the full 20-app
        ladder dossier with the user's row highlighted in EMBER at the foot (the
        honest base-camp distance, NEVER an 'out-tokened' claim) to a pixmap and
        show it in the shared popup. Mirrors _on_court_clicked (keyed
        'insight:climb', debounced, toggle-hide). EMBER accent (the 'you' thread).
        No popup when the ladder is unavailable."""
        popup = self._ensure_provider_popup()
        key = "insight:climb"
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        w = getattr(self, "_task_court", None)
        cc = getattr(w, "_cc", None) if w is not None else None
        if cc is None or not cc.climb_available:
            return
        html_str = build_climb_dossier_html(cc, getattr(cc, "all_apps", None))
        if not html_str:
            return
        popup.set_accent("#FF7A3C")          # the single ember 'you' thread
        popup.show_beside(html_str, self._dashboard_global_rect(), int(anchor_y))
        self._popup_model_id = key

    def _build_pinned_models(self):
        self._pinned_header = SectionHeader("Pinned Models")
        self._pinned_header.set_collapsible(True)
        self._pinned_header.clicked.connect(self._toggle_pinned_collapsed)
        self._pinned_count_label = self._pinned_header.right_label
        self._pinned_collapsed = False
        self._or_layout.addWidget(self._pinned_header)

        # Search bar + picker dropdown
        self.model_picker = ModelPicker(self)
        self.model_picker.pin_toggled.connect(self._on_pin_toggled)
        self.model_picker.open_changed.connect(self._on_picker_open_changed)
        self._or_layout.addWidget(self.model_picker)
        # Reparent the dropdown to the dashboard so it overlays the cards
        # area instead of pushing them down within the layout. (Overlay is
        # positioned by the search bar's GLOBAL coords, so nesting the pinned
        # section inside a source group doesn't affect it.)
        self.model_picker.attach_overlay_to(self)

        # Column header (PROVIDER / LATENCY / UPTIME / PRICE) above cards
        self._pinned_col_header = PinnedColumnHeader(self)
        self._or_layout.addWidget(self._pinned_col_header)

        # Container that holds the per-model cards
        self._pinned_container = QWidget()
        self._pinned_container.setStyleSheet("background: transparent;")
        self._pinned_layout = QVBoxLayout(self._pinned_container)
        self._pinned_layout.setContentsMargins(0, 0, 0, 0)
        self._pinned_layout.setSpacing(8)
        self._or_layout.addWidget(self._pinned_container)

        # model_id -> PinnedModelCard
        self._pinned_cards = {}

        # Empty-state label (shown when no models pinned). Kept minimal
        # since the picker above already prompts users to search and pin.
        self._pinned_empty = QLabel("No models pinned.")
        self._pinned_empty.setFont(Fonts.body())
        self._pinned_empty.setStyleSheet("color: #64648c; padding: 16px;")
        self._pinned_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pinned_layout.addWidget(self._pinned_empty)

        initial = []
        if self._settings:
            initial = list(getattr(self._settings, "tracked_models", []) or [])
        self.set_tracked_models(initial)
        self.model_picker.set_pinned(initial)

    def set_tracked_models(self, model_ids):
        """Replace the pinned model list. Creates/removes cards as needed."""
        wanted = list(model_ids)

        # Remove cards for models no longer tracked
        for mid in list(self._pinned_cards.keys()):
            if mid not in wanted:
                card = self._pinned_cards.pop(mid)
                self._pinned_layout.removeWidget(card)
                card.deleteLater()

        # Add cards for new models, preserving order
        for mid in wanted:
            if mid not in self._pinned_cards:
                card = PinnedModelCard(mid, self._pinned_container)
                card.info_clicked.connect(self._on_info_clicked)
                card.arena_clicked.connect(self._on_arena_clicked)
                card.trust_clicked.connect(self._on_trust_clicked)
                card.speed_clicked.connect(self._on_speed_clicked)
                card.trend_clicked.connect(self._on_trend_clicked)
                card.door_clicked.connect(self._on_door_clicked)
                card.uptime_clicked.connect(self._on_uptime_clicked)
                card.fees_clicked.connect(self._on_fees_clicked)
                card.drift_clicked.connect(self._on_drift_clicked)
                card.set_show_door(self._show_door)   # #5 settings gate
                card.set_show_fees(self._show_fees)   # #6 settings gate
                card.set_show_drift(self._show_drift)  # #8 settings gate
                if self._logo_store is not None:
                    card.set_logo_store(self._logo_store)
                self._pinned_cards[mid] = card
                self._pinned_layout.addWidget(card)

        # Reorder if needed: detach and re-add in wanted order
        for mid in wanted:
            card = self._pinned_cards[mid]
            self._pinned_layout.removeWidget(card)
        for mid in wanted:
            self._pinned_layout.addWidget(self._pinned_cards[mid])

        self._pinned_empty.setVisible(len(wanted) == 0)
        # Hide the column header strip when there are no cards to label
        # (and respect the collapsed state if the section is hidden)
        self._pinned_col_header.setVisible(
            len(wanted) > 0 and not self._pinned_collapsed
        )
        self._pinned_count_label.setText(
            f"{len(wanted)} model{'' if len(wanted) == 1 else 's'}"
            if wanted else ""
        )
        self._distribute_benchmarks()
        self._distribute_provider_trust()
        self._distribute_speed()
        self._distribute_trend()
        self._distribute_uptime()
        # #15 THE ASSAY: re-assay when the pin set changes (a coin appears/leaves).
        # (_distribute_benchmarks already calls this too; cheap + idempotent.)
        self._distribute_value()

    def update_benchmarks(self, board):
        """Worker fetched the Arena board (or None). Hand each pinned card its
        own standings; cards keep their last-good crest if board is None."""
        if board is not None:
            self._benchmark_board = board
        self._distribute_benchmarks()

    # ---- Wave 2 SPEND zone (F3 / #9 The Spectrum) ----

    def update_spend(self, board):
        """Worker fetched the ground-truth Spend board (or None). Keep last-good
        (the zone never blanks); fan out to the Spend widgets. Distinguishes
        LOCKED (board None + no key + no last-good) from POPULATED-EMPTY ($0 in
        range) — never fakes numbers in either."""
        if getattr(self, "spend_spectrum", None) is None:
            return  # section not built (show_spend disabled)
        if board is not None:
            self._spend_board = board

        board = self._spend_board
        if board is None:
            # No data ever arrived. If no management key -> LOCKED; otherwise a
            # transient failure on an unlocked key -> show the awaiting/empty
            # chrome (still honest, no fake $).
            if not getattr(self, "_spend_unlocked", False):
                self.spend_spectrum.set_locked()
                if getattr(self, "spend_savings", None) is not None:
                    self.spend_savings.set_locked()
                if getattr(self, "spend_ghosts", None) is not None:
                    self.spend_ghosts.set_locked()
                if getattr(self, "spend_budget", None) is not None:
                    self.spend_budget.set_locked()
                self._spend_header.right_label.setText("locked")
            return

        # receipts BEFORE spectrum: set_data builds the row geometry, which
        # reads the receipts for the $/call column + stamp pills.
        self.spend_spectrum.set_receipts(board.receipts)
        self.spend_spectrum.set_data(board.spectrum)
        if getattr(self, "spend_savings", None) is not None:
            self.spend_savings.set_data(board.savings)
        if getattr(self, "spend_ghosts", None) is not None:
            # board.ghosts may be None (a transient ghost-query failure on an
            # unlocked key); set_data(None) keeps last-good, never blanks.
            self.spend_ghosts.set_data(board.ghosts)
        if getattr(self, "spend_budget", None) is not None:
            # board.budget carries the burn-down (or a "none"/Budget sentinel ->
            # the "Set a budget" state). set_data routes a no-denominator Budget
            # to set_no_budget internally; None keeps last-good.
            self.spend_budget.set_data(board.budget)
        # Headline in the (collapsible) section header's right_label.
        sp = board.spectrum
        if sp.is_empty:
            self._spend_header.right_label.setText("$0.00 · 7d")
        else:
            self._spend_header.right_label.setText(
                f"${sp.total:,.2f} · 7d"
            )

    def _distribute_benchmarks(self):
        board = self._benchmark_board
        if board is None:
            return
        for mid, card in self._pinned_cards.items():
            entry = board.lookup(mid, card.display_name())
            card.set_benchmark(entry)
        # #15 THE ASSAY rides the same benchmark board — re-assay whenever the
        # crest data lands (one of its two inputs changed).
        self._distribute_value()

    # ---- Wave 3 INSIGHTS zone (scaffold + #15 THE ASSAY) ----

    def update_insights(self, board):
        """Worker fetched the InsightsBoard for the mgmt features (or None). Keep
        last-good; route the mgmt slots to their widgets. Mirrors update_spend's
        locked/keep-last-good contract. #15 THE ASSAY is INDEPENDENT of `board`
        (it rides _distribute_value off the USER-key stores) so it renders
        regardless; this method mainly stands the zone up for #16/#17/#18.

        On a keyless machine with no last-good board, the mgmt widgets get
        set_locked() + the header reads 'locked' — but #15 stays live, so the
        zone is never fully blank."""
        if getattr(self, "_value_assay", None) is None and \
                getattr(self, "_insights_container", None) is None:
            return  # section not built (show_insights disabled)
        if board is not None:
            self._insights_board = board

        board = self._insights_board
        if board is None:
            # No data ever arrived. If no management key -> LOCKED the mgmt
            # widgets (none exist yet — guarded set_locked dispatch is ready for
            # #16/#17/#18). #15 is unaffected (USER key).
            if not getattr(self, "_insights_unlocked", False):
                for attr in ("_week_belt", "_token_recorder", "_task_court"):
                    w = getattr(self, attr, None)
                    if w is not None:
                        w.set_locked()
                if getattr(self, "_insights_header", None) is not None and \
                        not self._has_live_insight_headline():
                    self._insights_header.right_label.setText("locked")
            return

        # POPULATED: fan each mgmt slot to its widget (all None today — the
        # set_data calls are ready for #16/#17/#18, guarded by getattr).
        wk = getattr(self, "_week_belt", None)
        if wk is not None and board.week is not None:
            wk.set_data(board.week)
        rec = getattr(self, "_token_recorder", None)
        if rec is not None and board.recorder is not None:
            rec.set_data(board.recorder)
        ct = getattr(self, "_task_court", None)
        if ct is not None and board.court is not None:
            ct.set_data(board.court)

    def _has_live_insight_headline(self) -> bool:
        """True when #15 has already written a live 'standard:' headline, so the
        mgmt-locked path doesn't overwrite it with 'locked' (the anchor wins the
        header line while the mgmt widgets show their own locked chrome)."""
        hdr = getattr(self, "_insights_header", None)
        if hdr is None:
            return False
        return hdr.right_label.text().startswith("standard")

    def _distribute_value(self):
        """#15 THE ASSAY — recompute the value standard from the data ALREADY in
        memory on the USER key and hand it to the widget. Called from
        set_tracked_models, update_benchmarks (via _distribute_benchmarks), AND
        update_endpoints so it re-assays when either input (a benchmark crest or a
        priced endpoint) lands. Pure compute on the main thread — NO worker I/O.

        quality = BenchmarkEntry.{active metric} ÷ cheapest priced prompt $/Mtok
        (the SAME number the card's PRICE column shows — auditable). A model
        lacking the active AA index -> a hollow 'unassayable' coin (decision C),
        never dropped, never ELO-substituted on the rail."""
        w = getattr(self, "_value_assay", None)
        if w is None:
            return  # section not built (show_insights disabled)
        board = self._benchmark_board
        models = []
        for rank, (mid, card) in enumerate(self._pinned_cards.items()):
            entry = board.lookup(mid, card.display_name()) if board is not None else None
            eps = getattr(card, "_endpoints", None)
            models.append(value_assay.build_assay_model(
                mid, card.display_name(), entry, eps, spend_rank=rank))
        result = value_assay.value_rank(models, w.current_metric())
        w.set_data(result)
        self._update_insights_headline(result)
        # An INFO line so the live boot can confirm the value distributed (a top
        # model + its value + the × multiple — magnitudes only, never a key).
        win = result.winner
        if win is not None and win.value is not None:
            mult = result.top_multiple
            log.info("value assay: %d models, top=%s value=%.1f x%s",
                     len(result.assayable), win.model_id, win.value,
                     (f"{mult:.1f}" if mult is not None else "n/a"))

    def _update_insights_headline(self, result):
        """The collapsible header's live one-line headline from #15 (the anchor
        owns the header line; the mgmt-locked path defers to it). Empty/loading ->
        leave whatever's there (or 'locked' if the mgmt path set it)."""
        hdr = getattr(self, "_insights_header", None)
        if hdr is None or result is None:
            return
        win = result.winner
        if win is None or win.value is None:
            return
        from widgets import _coin_short_name
        name = _coin_short_name(win.display)
        if result.top_multiple is not None:
            hdr.right_label.setText(f"standard: {name} ×{result.top_multiple:.1f}")
        else:
            hdr.right_label.setText(f"standard: {name}")

    def update_provider_trust(self, book):
        """Worker fetched The Ledger (or None). Hand the book to every pinned
        card; cards keep their last-good seals if book is None."""
        if book is not None:
            self._provider_trust_book = book
        self._distribute_provider_trust()

    def _distribute_provider_trust(self):
        book = self._provider_trust_book
        if book is None:
            return
        for card in self._pinned_cards.values():
            card.set_provider_trust(book)
        self._prewarm_logos()

    # ---- #5 THE THRESHOLD (the "cheapest door" gate) ----

    def set_show_door(self, show: bool):
        """Apply the settings.show_door gate to every pinned card (and remember
        it for cards created later). #5 carries no fetch, so toggling this is how
        the band is shown/hidden."""
        self._show_door = bool(show)
        for card in self._pinned_cards.values():
            card.set_show_door(self._show_door)

    # ---- #6 THE WATERLINE (the hidden-fee gate) ----

    def set_show_fees(self, show: bool):
        """Apply the settings.show_hidden_fees gate to every pinned card (and
        remember it for cards created later). #6 carries no fetch, so toggling
        this is how the waterline is shown/hidden."""
        self._show_fees = bool(show)
        for card in self._pinned_cards.values():
            card.set_show_fees(self._show_fees)

    # ---- #8 THE FAULT LINE (the price-drift gate) ----

    def set_show_drift(self, show: bool):
        """Apply the settings.show_drift gate to every pinned card (and remember
        it for cards created later). #8 carries no fetch (it rides the endpoints
        diff), so toggling this is how the seismograph crack is shown/hidden."""
        self._show_drift = bool(show)
        for card in self._pinned_cards.values():
            card.set_show_drift(self._show_drift)

    # ---- Speed Percentile (#4) ----

    def update_speed_board(self, board):
        """Worker fetched the performance fleet (or None). Cards keep their
        last-good speed band if board is None."""
        if board is not None:
            self._speed_board = board
        self._distribute_speed()

    def update_permaslug_resolver(self, resolver):
        """Worker fetched the slug↔permaslug map (or None). Shared by Speed AND
        #7 THE TAPE (both look pinned models up by permaslug), so re-distribute
        both when it lands."""
        if resolver is not None:
            self._permaslug_resolver = resolver
        self._distribute_speed()
        self._distribute_trend()

    def _distribute_speed(self):
        """Resolve each pinned model's public slug → permaslug, look it up in the
        fleet, and hand the card a render-ready SpeedStanding (or None). Needs
        BOTH the board and the resolver before it can place anything."""
        board = self._speed_board
        resolver = self._permaslug_resolver
        if board is None or resolver is None:
            return
        for mid, card in self._pinned_cards.items():
            perma = resolver.permaslug(mid)
            standing = board.standing(perma) if perma else None
            card.set_speed(standing)

    # ---- #7 THE TAPE (week-over-week request momentum) ----

    def update_trend(self, board):
        """Worker fetched the rankings/models momentum board (or None). Cards
        keep their last-good tape if board is None."""
        if board is not None:
            self._trend_board = board
        self._distribute_trend()

    def _distribute_trend(self):
        """Resolve each pinned model's public slug → permaslug, read its change
        from the board, and hand the card the change float (or None). A
        resolver-miss OR a row-miss → None → silent no-op on the card. Needs
        BOTH the board and the resolver."""
        board = self._trend_board
        resolver = self._permaslug_resolver
        if board is None or resolver is None:
            return
        for mid, card in self._pinned_cards.items():
            perma = resolver.permaslug(mid)
            change = board.change(perma) if perma else None
            card.set_trend(change)
            if change is not None:
                # A single greppable INFO line on the dedicated openrouter logger
                # so the live-boot check has something deterministic to assert.
                logging.getLogger("pulse.openrouter").info(
                    "trend: %s change=%s stamp=%s", mid, change, card._trend_stamp)

    # ---- THE PULSE (#3 — per-endpoint 73h uptime) ----

    def request_uptime_fetch(self):
        """Resolve each pinned model's permaslug (we own the resolver) and ask
        the worker to fan out its per-endpoint uptime fetch. No-ops cleanly
        until the resolver has loaded — the slow timer will retry."""
        resolver = self._permaslug_resolver
        if resolver is None:
            return
        for mid in self._pinned_cards:
            perma = resolver.permaslug(mid)
            if perma:
                self.fetch_uptime_requested.emit(mid, perma)

    def update_uptime(self, model_id, histories):
        """Worker reported per-endpoint uptime for one model (a possibly-empty
        dict). Keep last-good so a transient failure never blanks the card."""
        if histories:
            self._uptime_by_model[model_id] = histories
        self._distribute_uptime(model_id)
        # Live-refresh an open Vitals dossier for this model in place.
        self._maybe_refresh_uptime_popup(model_id)

    def _distribute_uptime(self, only_model_id=None):
        """Hand each pinned card its {ep_ident: UptimeHistory} dict."""
        items = (self._uptime_by_model.items() if only_model_id is None
                 else [(only_model_id, self._uptime_by_model.get(only_model_id))])
        for mid, hists in items:
            card = self._pinned_cards.get(mid)
            if card is not None and hists is not None:
                card.set_uptime(hists)

    def _maybe_refresh_uptime_popup(self, model_id):
        ctx = self._uptime_popup_ctx
        if (ctx is None or self._provider_popup is None
                or not self._provider_popup.isVisible()):
            return
        m, ident, anchor_y = ctx
        if m != model_id or self._popup_model_id != "uptime:" + model_id + ":" + ident:
            return
        card = self._pinned_cards.get(model_id)
        if card is None:
            return
        html_str = card.uptime_html(ident)
        if not html_str:
            return
        self._provider_popup.set_accent(card.uptime_accent(ident))
        self._provider_popup.show_beside(
            html_str, self._dashboard_global_rect(), anchor_y)

    # ---- Provider logos (#2b) ----

    def set_logo_store(self, store):
        """Wire the shared logo cache in: hand it to every card and refresh an
        open dossier when a logo it was waiting on arrives."""
        self._logo_store = store
        if store is not None:
            store.ready.connect(self._on_logo_ready)
        for card in self._pinned_cards.values():
            card.set_logo_store(store)
        # #16 THE TITLE BELT shares the SAME logo store (it looks up the champion
        # provider's cached tile, else paints a monogram disc).
        belt = getattr(self, "_week_belt", None)
        if belt is not None:
            belt.set_logo_store(store)

    def _prewarm_logos(self):
        """Pre-fetch logos for every provider currently on the board so a tile
        is cached before the user opens its dossier. Idempotent + bounded."""
        if self._logo_store is None:
            return
        seen = set()
        for card in self._pinned_cards.values():
            for slug, url in card.logos_needed():
                if slug not in seen:
                    seen.add(slug)
                    self._logo_store.request(slug, url)

    def _on_logo_ready(self, slug):
        """A logo tile just cached — if the open dossier is for that provider,
        rebuild it in place so the real logo replaces the monogram live."""
        ctx = self._trust_popup_ctx
        if ctx is None or self._provider_popup is None or not self._provider_popup.isVisible():
            return
        model_id, ident, anchor_y = ctx
        # Only refresh if the visible popup is STILL this exact trust dossier
        # (the user may have since opened an info/arena popup).
        if self._popup_model_id != "trust:" + model_id + ":" + ident:
            return
        card = self._pinned_cards.get(model_id)
        if card is None or card.provider_slug_for(ident) != slug:
            return
        self._provider_popup.set_accent(card.dossier_accent(ident))
        self._provider_popup.show_beside(
            card.dossier_html(ident), self._dashboard_global_rect(), anchor_y)

    def update_endpoints(self, model_id, model_endpoints):
        """Worker reported new data (or None for failure) for one pinned model."""
        card = self._pinned_cards.get(model_id)
        if card is not None:
            card.set_endpoints(model_endpoints)
            self._prewarm_logos()
            self._apply_drift(model_id, model_endpoints)
            # #15 THE ASSAY rides the card's endpoints (the price denominator);
            # re-assay now that a priced prompt endpoint may have landed.
            self._distribute_value()

    def _apply_drift(self, model_id, model_endpoints):
        """#8 THE FAULT LINE — diff the just-landed endpoints vs the stored
        baseline, apply the baseline-update policy (decision C), persist the
        store, and push the result to the card. A failed fetch (None) or the
        gate being off is a no-op (the card keeps last-good). The store.observe()
        returns None for first-sight / quiet (the card then paints nothing)."""
        if not self._show_drift:
            return
        if model_endpoints is None or not getattr(model_endpoints, "endpoints", None):
            return                       # failed fetch → keep last-good crack
        card = self._pinned_cards.get(model_id)
        if card is None:
            return
        try:
            result = self._price_store.observe(model_id, model_endpoints.endpoints)
            self._price_store.save()     # persist baseline roll / fresh flag
        except Exception:
            logging.getLogger("pulse.drift").warning(
                "drift observe failed for %s", model_id, exc_info=True)
            return
        card.set_drift(result)
        # Live-refresh an open Seismograph dossier for this model in place.
        self._maybe_refresh_drift_popup(model_id)

    def _maybe_refresh_drift_popup(self, model_id):
        ctx = self._drift_popup_ctx
        if (ctx is None or self._provider_popup is None
                or not self._provider_popup.isVisible()):
            return
        m, anchor_y = ctx
        if m != model_id or self._popup_model_id != "drift:" + model_id:
            return
        card = self._pinned_cards.get(model_id)
        if card is None:
            return
        html_str = card.drift_html()
        if not html_str:
            return
        self._provider_popup.set_accent(card.drift_accent())
        self._provider_popup.show_beside(
            html_str, self._dashboard_global_rect(), anchor_y)

    def update_model_catalog(self, models):
        """Worker fetched the full /api/v1/models list; feed it to the picker."""
        self.model_picker.set_catalog(models)

    def tracked_models(self):
        return list(self._pinned_cards.keys())

    # ---- Pin/unpin from the search picker ----
    refresh_endpoint_requested = Signal(str)

    def _on_pin_toggled(self, model_id, is_pinned_after):
        """User toggled pin on a row. Update settings + cards + persist."""
        if self._settings is None:
            return
        current = list(getattr(self._settings, "tracked_models", []) or [])
        if is_pinned_after and model_id not in current:
            current.append(model_id)
        elif not is_pinned_after and model_id in current:
            current.remove(model_id)
        else:
            return
        self._settings.tracked_models = current
        try:
            self._settings.save()
        except Exception:
            log.exception("settings save failed")
        self.set_tracked_models(current)
        # Kick off an endpoints fetch for a newly-pinned model so the
        # user sees data without waiting for the 5-min polling cycle.
        if is_pinned_after:
            self.refresh_endpoint_requested.emit(model_id)

    def _on_picker_open_changed(self, is_open):
        """No-op: the dropdown is now a top-level floating window that
        overlays the cards, so cards stay visible underneath. Kept as a
        connection point for future state changes if needed."""
        pass

    def _on_dashboard_scrolled(self):
        """Close any floating child (picker dropdown, info popup) when
        the dashboard scrolls. Otherwise they stay pinned to their
        original spot while the anchor moves underneath them."""
        try:
            if self.model_picker.is_open():
                self.model_picker.search.clearFocus()
                self.model_picker._close()
        except Exception:
            pass
        self._hide_provider_popup()

    # ---- Section collapse ----

    def _toggle_pinned_collapsed(self):
        self._pinned_collapsed = not self._pinned_collapsed
        self._pinned_header.set_collapsed(self._pinned_collapsed)
        collapsed = self._pinned_collapsed
        has_cards = len(self._pinned_cards) > 0
        self.model_picker.setVisible(not collapsed)
        self._pinned_col_header.setVisible((not collapsed) and has_cards)
        self._pinned_container.setVisible(not collapsed)
        # Also close the picker dropdown if it was open
        if collapsed:
            try:
                self.model_picker.search.clearFocus()
            except Exception:
                pass
            self._hide_provider_popup()

    # ---- Provider info popup (click-to-toggle on the card's (i) icon) ----

    def _ensure_provider_popup(self):
        if self._provider_popup is None:
            self._provider_popup = ProviderPopup()
            self._provider_popup.hidden.connect(self._on_popup_hidden)
        return self._provider_popup

    def _on_popup_hidden(self):
        self._popup_just_hidden_at = time.monotonic()

    def _hide_provider_popup(self):
        if self._provider_popup is not None and self._provider_popup.isVisible():
            self._provider_popup.hide()

    def _on_info_clicked(self, model_id, global_anchor):
        popup = self._ensure_provider_popup()

        # Race: when the popup is open and the user clicks the SAME icon,
        # the app-wide event filter on the popup closes it first, then this
        # handler runs. Without a debounce we'd reopen the popup we just
        # closed. 150ms window catches that case for the same model id.
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == model_id
        )
        if just_closed:
            self._popup_model_id = None
            return

        # Same icon while open → toggle off (defensive: usually the event
        # filter has hidden it by this point).
        if popup.isVisible() and self._popup_model_id == model_id:
            popup.hide()
            self._popup_model_id = None
            return

        card = self._pinned_cards.get(model_id)
        if card is None:
            return
        # Anchor against the dashboard's screen rect so the popup sits
        # entirely OUTSIDE the dashboard (left side by default).
        popup.set_accent("#00d2ff")
        popup.show_beside(
            card.provider_html(),
            self._dashboard_global_rect(),
            int(global_anchor.y()),
        )
        self._popup_model_id = model_id

    def _dashboard_global_rect(self):
        """The dashboard window's rect in global screen coords (for anchoring
        floating popups entirely outside it)."""
        from PySide6.QtCore import QRect
        tl = self.mapToGlobal(QPoint(0, 0))
        r = self.frameGeometry()
        return QRect(tl.x(), tl.y(), r.width(), r.height())

    def _on_arena_clicked(self, model_id, global_anchor):
        """Crest band clicked -> show the model's Fighter Card (tier-accented)."""
        card = self._pinned_cards.get(model_id)
        if card is None or not card.has_benchmark():
            return
        popup = self._ensure_provider_popup()
        key = "arena:" + model_id
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        popup.set_accent(card.arena_accent())
        popup.show_beside(
            card.arena_html(),
            self._dashboard_global_rect(),
            int(global_anchor.y()),
        )
        self._popup_model_id = key

    def _on_speed_clicked(self, model_id, global_anchor):
        """The Speed band was clicked -> show the model's Velocity dossier."""
        card = self._pinned_cards.get(model_id)
        if card is None or not card.has_speed():
            return
        popup = self._ensure_provider_popup()
        key = "speed:" + model_id
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        popup.set_accent(card.speed_accent())
        popup.show_beside(
            card.speed_html(),
            self._dashboard_global_rect(),
            int(global_anchor.y()),
        )
        self._popup_model_id = key

    def _on_trend_clicked(self, model_id, global_anchor):
        """#7 THE TAPE was clicked -> show the week-over-week momentum dossier."""
        card = self._pinned_cards.get(model_id)
        if card is None or not card.has_trend():
            return
        popup = self._ensure_provider_popup()
        key = "trend:" + model_id
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        popup.set_accent(card.trend_accent())
        popup.show_beside(
            card.trend_html(),
            self._dashboard_global_rect(),
            int(global_anchor.y()),
        )
        self._popup_model_id = key

    def _on_door_clicked(self, model_id, global_anchor):
        """#5 THE THRESHOLD band was clicked -> show the FROM→THROUGH dossier
        (amber, or emerald for the green door)."""
        card = self._pinned_cards.get(model_id)
        if card is None or not card.has_door():
            return
        popup = self._ensure_provider_popup()
        key = "door:" + model_id
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        popup.set_accent(card.door_accent())
        popup.show_beside(
            card.door_html(),
            self._dashboard_global_rect(),
            int(global_anchor.y()),
        )
        self._popup_model_id = key

    def _on_drift_clicked(self, model_id, global_anchor):
        """#8 THE FAULT LINE was clicked -> show the SEISMOGRAPH dossier AND
        ACKNOWLEDGE the drift (decision C iv): write current as the new baseline
        + clear fresh, then PERSIST so the same drift never re-fires. The crack
        clears on the next refresh (current-vs-current -> quiet -> set_drift
        None). Acknowledge fires only on the SHOW path (not toggle-hide / a
        debounced reopen) so a quick double-click doesn't ack-then-reopen-empty."""
        card = self._pinned_cards.get(model_id)
        if card is None or not card.has_drift():
            return
        popup = self._ensure_provider_popup()
        key = "drift:" + model_id
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        html_str = card.drift_html()
        if not html_str:
            return
        popup.set_accent(card.drift_accent())
        popup.show_beside(html_str, self._dashboard_global_rect(),
                          int(global_anchor.y()))
        self._popup_model_id = key
        self._drift_popup_ctx = (model_id, int(global_anchor.y()))
        # ACKNOWLEDGE (durable): write current as the new baseline + clear the
        # fresh flag, persist to disk. The card stops the fresh shimmer now; the
        # crack itself persists until the next quiet re-diff clears it.
        eps = (card._endpoints.endpoints
               if getattr(card, "_endpoints", None) is not None else [])
        try:
            self._price_store.acknowledge(model_id, eps)
            self._price_store.save()
        except Exception:
            logging.getLogger("pulse.drift").warning(
                "drift acknowledge failed for %s", model_id, exc_info=True)
        card.acknowledge()

    def _on_trust_clicked(self, model_id, provider_ident, global_anchor):
        """A provider's Trust Seal was clicked -> show its Custody dossier."""
        card = self._pinned_cards.get(model_id)
        if card is None:
            return
        html_str = card.dossier_html(provider_ident)
        if not html_str:
            return
        popup = self._ensure_provider_popup()
        key = "trust:" + model_id + ":" + provider_ident
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        # Lazily fetch this provider's logo in case pre-warm missed it; the
        # dossier will refresh in place when it lands.
        card.request_logo(provider_ident)
        anchor_y = int(global_anchor.y())
        self._trust_popup_ctx = (model_id, provider_ident, anchor_y)
        popup.set_accent(card.dossier_accent(provider_ident))
        popup.show_beside(html_str, self._dashboard_global_rect(), anchor_y)
        self._popup_model_id = key

    def _on_uptime_clicked(self, model_id, ep_ident, global_anchor):
        """A row's uptime cardiogram was clicked -> show its Vitals dossier
        (the painted 73-bar strip). Mirrors _on_trust_clicked exactly."""
        card = self._pinned_cards.get(model_id)
        if card is None:
            return
        html_str = card.uptime_html(ep_ident)
        if not html_str:
            return
        popup = self._ensure_provider_popup()
        key = "uptime:" + model_id + ":" + ep_ident
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        anchor_y = int(global_anchor.y())
        self._uptime_popup_ctx = (model_id, ep_ident, anchor_y)
        popup.set_accent(card.uptime_accent(ep_ident))
        popup.show_beside(html_str, self._dashboard_global_rect(), anchor_y)
        self._popup_model_id = key

    def _on_fees_clicked(self, model_id, ep_ident, global_anchor):
        """#6 THE WATERLINE: a row's price-cell iceberg was clicked -> show the
        'WHAT THE STICKER PRICE HIDES' decode dossier. Mirrors _on_uptime_clicked
        exactly (per-row, keyed by ident, debounced toggle-hide)."""
        card = self._pinned_cards.get(model_id)
        if card is None:
            return
        html_str = card.fees_html(ep_ident)
        if not html_str:
            return
        popup = self._ensure_provider_popup()
        key = "fees:" + model_id + ":" + ep_ident
        just_closed = (
            time.monotonic() - self._popup_just_hidden_at < 0.15
            and self._popup_model_id == key
        )
        if just_closed:
            self._popup_model_id = None
            return
        if popup.isVisible() and self._popup_model_id == key:
            popup.hide()
            self._popup_model_id = None
            return
        anchor_y = int(global_anchor.y())
        popup.set_accent(card.fees_accent(ep_ident))
        popup.show_beside(html_str, self._dashboard_global_rect(), anchor_y)
        self._popup_model_id = key

    # ------------------------------------------------------------------
    #  Source TABS (OpenRouter, Claude, …) — equal peers on the nav-rail;
    #  each gets a full SourcePanel in the stack. No provider is privileged.
    # ------------------------------------------------------------------

    def _build_openrouter_group(self):
        """Build OpenRouter's content (error banner, gauge, usage, burn rate,
        pinned models, quick links) as the OpenRouter panel body."""
        group = QWidget()
        group.setStyleSheet("background: transparent;")
        self._or_layout = QVBoxLayout(group)
        self._or_layout.setContentsMargins(0, 0, 0, 0)
        self._or_layout.setSpacing(12)
        self.error_banner = ErrorBanner(self)
        self._or_layout.addWidget(self.error_banner)
        self._build_gauge_section()
        # Wave 2: the ground-truth SPEND zone (#9 The Spectrum + foundation F3)
        # REPLACES the estimated Usage + Burn Rate sections. Order stays
        # Balance → Spend → Models. The TimelineChart/BurnRateBar CLASSES are
        # kept in widgets.py (the Spectrum echoes the gradient-area idiom); only
        # these two SECTION builders are dropped.
        if bool(getattr(self._settings, "show_spend", True)) if self._settings else True:
            self._build_spend_section()
        self._build_pinned_models()
        # Wave 3: the INSIGHTS zone (derived garnish about your models/usage) —
        # mounts BETWEEN the Models board it comments on and Quick Links. Its
        # anchor #15 THE ASSAY is always-live (USER key); #16/#17/#18 attach
        # below it later. Guarded by show_insights (mirrors show_spend — when off
        # the section isn't built at all).
        if bool(getattr(self._settings, "show_insights", True)) if self._settings else True:
            self._build_insights_section()
        self._build_quick_links()
        return group

    def _source_order(self):
        default = ["openrouter", "claude", "gpu", "system"]
        if self._settings is not None:
            return list(getattr(self._settings, "source_order", None) or default)
        return default

    def update_source(self, source_id, data):
        """Deliver fresh poll data to a source's card (main thread)."""
        card = self._source_cards.get(source_id)
        if card is not None:
            card.render(data)

    def _build_quick_links(self):
        self._or_layout.addWidget(SectionHeader("Quick Links"))

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(LinkButton("Dashboard", OPENROUTER_DASHBOARD_URL))
        row.addWidget(LinkButton("Add Credits", OPENROUTER_CREDITS_URL))
        row.addWidget(LinkButton("Models", OPENROUTER_MODELS_URL))
        self._or_layout.addLayout(row)

    # ------------------------------------------------------------------
    #  Data updates
    # ------------------------------------------------------------------

    def update_key_info(self, key_info):
        self._last_key_info = key_info

        remaining = key_info.remaining
        total = key_info.total_credits
        percent = key_info.credit_percent

        if remaining is not None:
            amount_text = f"${remaining:.2f}"
            total_text = f"/ ${total:.2f}" if total > 0 else ""
        else:
            amount_text = "N/A"
            total_text = f"Used: ${key_info.usage:.2f}"
            percent = 1.0

        rate_hourly, rate_daily, monthly_proj, forecast, rate_source = (
            self._smart_forecast(key_info)
        )
        self.gauge.set_value(percent, amount_text, total_text, forecast)

        self._update_autotopup_label()

        orp = self._panels.get("openrouter")
        if orp is not None:
            orp.set_meta(forecast if (forecast and forecast != "--") else "")

        # The forecast tooltip still annotates the Credit Balance gauge (the
        # gauge + its forecast subtitle stay). The estimated Usage timeline /
        # KPI StatCards / Burn Rate bar were Wave-2-replaced by the ground-truth
        # SPEND zone, so their feeders are intentionally gone — ground-truth
        # spend is routed through update_spend(board), not here.
        tip = self._build_forecast_tooltip(
            key_info, rate_hourly, rate_daily, monthly_proj, rate_source
        )
        self.gauge.setToolTip(tip)
        self._autotopup_label.setToolTip(tip)

    def _smart_forecast(self, key_info):
        """Returns (rate_hourly, rate_daily, monthly_proj, forecast_text, rate_source)."""
        rate_hourly = None
        rate_source = None
        if self._history is not None:
            rate_hourly = self._history.burn_rate_per_hour(3600)
            if rate_hourly is not None:
                rate_source = "last 1h of history"
            else:
                rate_hourly = self._history.burn_rate_per_hour(86400)
                if rate_hourly is not None:
                    rate_source = "last 24h of history"

        if rate_hourly is None or rate_hourly == 0:
            rate_hourly = key_info.burn_rate_hourly
            rate_source = "today's API total" if rate_hourly > 0 else None

        rate_daily = rate_hourly * 24 if rate_hourly else 0
        monthly_proj = rate_daily * 30 if rate_daily else None
        remaining = key_info.remaining

        forecast = "--"
        if self._settings and self._settings.autotopup_enabled:
            thr = self._settings.auto_topup_threshold
            amt = self._settings.auto_topup_amount
            if remaining is None:
                forecast = f"Auto-top-up at ${thr:g}"
            elif remaining <= thr:
                forecast = f"Top-up pending (+${amt:g})"
            elif rate_daily > 0:
                days_to_topup = (remaining - thr) / rate_daily
                forecast = f"Next top-up in {_fmt_duration(days_to_topup)}"
            else:
                forecast = f"Auto-top-up at ${thr:g}"
        else:
            if remaining is not None and rate_daily > 0:
                days = remaining / rate_daily
                forecast = f"Depletes in {_fmt_duration(days)}"
            elif rate_daily == 0:
                forecast = "No recent usage"

        return rate_hourly, rate_daily, monthly_proj, forecast, rate_source

    def _build_forecast_tooltip(self, key_info, rate_hourly, rate_daily,
                                 monthly_proj, rate_source):
        """Human-readable HTML explanation of how the forecast was computed."""
        rem = key_info.remaining
        autotopup = self._settings and self._settings.autotopup_enabled
        thr = self._settings.auto_topup_threshold if self._settings else 0
        amt = self._settings.auto_topup_amount if self._settings else 0

        # Empty-data shortcut
        if not rate_hourly or rate_hourly == 0:
            return (
                "<b>Not enough data yet</b><br>"
                "<span style='color:#a0a0c8;'>Pulse needs at least an hour "
                "of activity before it can estimate your burn rate.</span><br>"
                "<br>"
                "<span style='color:#a0a0c8;'>Once OpenRouter usage shows up, "
                "this will show your spend rate, a 30-day projection, and "
                "when your next auto top-up will trigger.</span>"
            )

        title = ("How <b style='color:#00d2ff;'>“next top-up”</b> is computed"
                 if autotopup else
                 "How <b style='color:#00d2ff;'>“depletes in X”</b> is computed")

        bal_str = f"${rem:.2f}" if rem is not None else "unknown"

        rate_row = (
            f"<tr><td><span style='color:#a0a0c8;'>Burn rate</span></td>"
            f"<td style='padding-left:14px;'><b>${rate_daily:.2f}/day</b> "
            f"<span style='color:#64648c;'>({rate_source})</span></td></tr>"
        )
        bal_row = (
            f"<tr><td><span style='color:#a0a0c8;'>Balance</span></td>"
            f"<td style='padding-left:14px;'>{bal_str}</td></tr>"
        )
        proj_row = ""
        if monthly_proj:
            proj_row = (
                f"<tr><td><span style='color:#a0a0c8;'>30-day projection</span></td>"
                f"<td style='padding-left:14px;'>${monthly_proj:.2f}</td></tr>"
            )

        topup_row = ""
        if autotopup:
            topup_row = (
                f"<tr><td><span style='color:#a0a0c8;'>Top-up at</span></td>"
                f"<td style='padding-left:14px;'>${thr:g} <span style='color:#64648c;'>"
                f"→ adds ${amt:g}</span></td></tr>"
            )

        if autotopup and rem is not None and rate_daily > 0:
            days = max(0, (rem - thr) / rate_daily)
            conclusion = (
                f"At this rate, your balance hits ${thr:g} in "
                f"<b style='color:#00d2ff;'>{_fmt_duration(days)}</b>. "
                f"That's when your next top-up fires."
            )
        elif rem is not None and rate_daily > 0:
            days = rem / rate_daily
            conclusion = (
                f"At this rate, you'll run out in "
                f"<b style='color:#00d2ff;'>{_fmt_duration(days)}</b>."
            )
        else:
            conclusion = "—"

        footnote = (
            "<span style='color:#64648c;'><i>Edit auto top-up in "
            "settings.json (tray menu → Open Settings File).</i></span>"
            if autotopup else
            "<span style='color:#64648c;'><i>Set up auto top-up at "
            "openrouter.ai/credits, then add the threshold and amount "
            "to settings.json.</i></span>"
        )

        return (
            f"{title}<br>"
            f"<br>"
            f"<table cellpadding='2' cellspacing='0'>"
            f"{rate_row}{bal_row}{proj_row}{topup_row}"
            f"</table>"
            f"<br>"
            f"{conclusion}<br>"
            f"<br>"
            f"{footnote}"
        )

    # ------------------------------------------------------------------
    #  Error banner
    # ------------------------------------------------------------------

    def show_error(self, message):
        self.error_banner.set_message(message)

    def clear_error(self):
        self.error_banner.set_message("")

    # ------------------------------------------------------------------
    #  Positioning and show/hide
    # ------------------------------------------------------------------

    def set_tray_icon(self, tray_icon):
        self._tray_icon = tray_icon

    def show_near_tray(self):
        # Open to the configured default tab, scrolled to the top — a fresh,
        # predictable view on every open rather than wherever you last left off.
        default = "openrouter"
        if self._settings is not None:
            default = getattr(self._settings, "default_source", "openrouter")
        if default not in self._panels:
            default = self._active_id or (
                self._tab_specs[0]["id"] if self._tab_specs else "openrouter")
        self.set_active_source(default, animate=False)

        screen = QApplication.primaryScreen()
        if not screen:
            self._show_no_activate()
            return

        avail = screen.availableGeometry()
        margin = 12

        popup_w = self.width()
        popup_h = max(self.minimumHeight(), self.sizeHint().height())
        popup_h = min(popup_h, avail.height() - 2 * margin)
        self.setFixedHeight(popup_h)

        icon_rect = None
        if self._tray_icon is not None:
            icon_rect = self._tray_icon.geometry()
            if (icon_rect.isNull() or not icon_rect.isValid()
                    or icon_rect.width() == 0 or icon_rect.height() == 0):
                icon_rect = None

        if icon_rect is not None:
            tray_screen = QApplication.screenAt(icon_rect.center())
            if tray_screen is not None:
                avail = tray_screen.availableGeometry()
            x = icon_rect.left()
            y = icon_rect.top() - popup_h
            if x + popup_w > avail.right():
                x = avail.right() - popup_w + 1
            if x < avail.left():
                x = avail.left()
            if y < avail.top():
                y = icon_rect.bottom()
                if y + popup_h > avail.bottom():
                    y = avail.top()
        else:
            cursor_pos = QCursor.pos()
            target_screen = QApplication.screenAt(cursor_pos)
            if target_screen:
                avail = target_screen.availableGeometry()
            x = avail.right() - popup_w - margin + 1
            y = avail.bottom() - popup_h - margin + 1

        self.move(x, y)
        self._show_no_activate()

    def _show_no_activate(self):
        self.setVisible(True)
        hwnd = int(self.winId())
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        try:
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass
        HWND_TOPMOST = -1
        SWP_NOACTIVATE = 0x0010
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        SWP_FRAMECHANGED = 0x0020
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_FRAMECHANGED,
        )

    def toggle(self):
        if self.isVisible():
            self.hide()  # hideEvent stops the poller (as on every hide path)
        else:
            self.show_near_tray()
            if self._dismiss_enabled:
                self._arm_outside_click_dismiss()

    # ------------------------------------------------------------------
    #  Click-outside-to-dismiss
    # ------------------------------------------------------------------

    def _arm_outside_click_dismiss(self):
        """Start the outside-click poller after a grace period, so the click
        that just opened the dashboard doesn't count as 'outside'."""
        self._show_foreground = None
        QTimer.singleShot(250, self._start_outside_click_poll)

    def _start_outside_click_poll(self):
        # Re-check at fire time: the setting can be toggled off (or the
        # dashboard closed again) inside the grace window.
        if self._dismiss_enabled and self.isVisible():
            self._outside_click_timer.start(150)

    def _check_outside_click(self):
        if not self.isVisible():
            return
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return
        if self._show_foreground is None:
            self._show_foreground = hwnd
            return
        if hwnd == self._show_foreground:
            return
        try:
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(
                wintypes.HWND(hwnd), ctypes.byref(pid)
            )
            if pid.value == os.getpid():
                self._show_foreground = hwnd
                return
        except Exception:
            pass
        self.hide()

    # ------------------------------------------------------------------
    #  Refresh
    # ------------------------------------------------------------------

    def _on_refresh(self):
        self.refresh_requested.emit()

    def hideEvent(self, event):
        # Stop the outside-click poller on EVERY hide path (tray toggle, the
        # ✕ button, the poller itself) so it can never orphan across a reopen.
        self._outside_click_timer.stop()
        self._show_foreground = None
        # When dashboard hides, dismiss any floating children
        # (info popup + picker dropdown) so they don't get orphaned.
        self._hide_provider_popup()
        try:
            if self.model_picker.is_open():
                self.model_picker._close()
        except Exception:
            pass
        super().hideEvent(event)

    def paintEvent(self, event):
        pass

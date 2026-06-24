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
    ArcGauge, StatCard, SectionHeader, BurnRateBar, GradientStrip,
    ErrorBanner, TimelineChart, PinnedModelCard, PinnedColumnHeader,
    ModelPicker, ProviderPopup,
)
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


class IconButton(QPushButton):
    """Small icon-style button."""
    def __init__(self, text, tooltip="", parent=None):
        super().__init__(text, parent)
        self.setFixedSize(28, 28)
        self.setToolTip(tooltip)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("""
            QPushButton {
                background: #242442;
                color: #a0a0c8;
                border: 1px solid #323250;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #2e2e52;
                color: #00d2ff;
                border-color: #00d2ff;
            }
            QPushButton:pressed {
                background: #1a1a30;
            }
        """)


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


class CardFrame(QWidget):
    """A card-style container with rounded background."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QBrush(Colors.BG_CARD))
        pen = QPen(Colors.BORDER, 1)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class Dashboard(QWidget):
    """The main dashboard popup window."""

    refresh_requested = Signal()
    fetch_uptime_requested = Signal(str, str)   # (model_id, permaslug) — THE PULSE (#3)

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
        # Speed Percentile (#4): the fleet performance board + the slug→permaslug
        # map needed to look a pinned model up in it. Both distributed to cards.
        self._speed_board = None
        self._permaslug_resolver = None
        # THE PULSE (#3): per-model {ep_ident: UptimeHistory}, kept last-good so a
        # transient fetch failure never blanks a card's cardiogram.
        self._uptime_by_model = {}
        self._uptime_popup_ctx = None    # (model_id, ident, anchor_y) or None
        # Provider logos (#2b): the shared cache + the open-dossier context so a
        # logo that arrives after the dossier opens can refresh it in place.
        self._logo_store = None
        self._trust_popup_ctx = None     # (model_id, ident, anchor_y) or None

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

    def _build_usage_section(self):
        self._or_layout.addWidget(SectionHeader("Usage"))

        self.timeline = TimelineChart(self)
        topup_thr = self._settings.auto_topup_threshold if self._settings else 0.0
        self.timeline.set_data([], [], topup_thr, "last 24h")
        self._or_layout.addWidget(self.timeline)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.kpi_today = StatCard("Today")
        self.kpi_monthly = StatCard("Projected / mo")
        grid.addWidget(self.kpi_today, 0, 0)
        grid.addWidget(self.kpi_monthly, 0, 1)
        self._or_layout.addLayout(grid)

    def _build_burn_rate(self):
        self._or_layout.addWidget(SectionHeader("Burn Rate"))

        burn_card = CardFrame(self)
        # 50px bar + symmetric 6/6 vertical padding = 62 (was 60, which squeezed
        # the bar's bottom 'used/remaining' labels).
        burn_card.setFixedHeight(62)
        burn_layout = QVBoxLayout(burn_card)
        burn_layout.setContentsMargins(14, 6, 14, 6)

        self.burn_rate_bar = BurnRateBar(self)
        burn_layout.addWidget(self.burn_rate_bar)

        self._or_layout.addWidget(burn_card)

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
                card.door_clicked.connect(self._on_door_clicked)
                card.uptime_clicked.connect(self._on_uptime_clicked)
                card.fees_clicked.connect(self._on_fees_clicked)
                card.set_show_door(self._show_door)   # #5 settings gate
                card.set_show_fees(self._show_fees)   # #6 settings gate
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
        self._distribute_uptime()

    def update_benchmarks(self, board):
        """Worker fetched the Arena board (or None). Hand each pinned card its
        own standings; cards keep their last-good crest if board is None."""
        if board is not None:
            self._benchmark_board = board
        self._distribute_benchmarks()

    def _distribute_benchmarks(self):
        board = self._benchmark_board
        if board is None:
            return
        for mid, card in self._pinned_cards.items():
            entry = board.lookup(mid, card.display_name())
            card.set_benchmark(entry)

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

    # ---- Speed Percentile (#4) ----

    def update_speed_board(self, board):
        """Worker fetched the performance fleet (or None). Cards keep their
        last-good speed band if board is None."""
        if board is not None:
            self._speed_board = board
        self._distribute_speed()

    def update_permaslug_resolver(self, resolver):
        """Worker fetched the slug↔permaslug map (or None)."""
        if resolver is not None:
            self._permaslug_resolver = resolver
        self._distribute_speed()

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
        self._build_usage_section()
        self._build_burn_rate()
        self._build_pinned_models()
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

        tip = self._build_forecast_tooltip(
            key_info, rate_hourly, rate_daily, monthly_proj, rate_source
        )
        self.gauge.setToolTip(tip)
        self.burn_rate_bar.setToolTip(tip)
        self._autotopup_label.setToolTip(tip)

        if self._history is not None:
            window_seconds = 24 * 3600
            series = self._history.balance_series(window_seconds)
            topups = self._history.topup_events(window_seconds)
            topup_thr = self._settings.auto_topup_threshold if self._settings else 0.0
            self.timeline.set_data(series, topups, topup_thr, "last 24h")

        self.kpi_today.set_value(f"${key_info.usage_daily:.2f}", "spent today")
        if monthly_proj is not None and monthly_proj > 0:
            self.kpi_monthly.set_value(
                _fmt_money(monthly_proj),
                "based on recent burn",
            )
        else:
            self.kpi_monthly.set_value(
                _fmt_money(key_info.usage_monthly),
                "this month so far",
            )

        pct_used = 1.0 - percent if remaining is not None and total > 0 else 0.0
        if rate_hourly is not None and rate_hourly > 0:
            rate_text = f"${rate_hourly:.3f}/hr · ${rate_daily:.2f}/day"
        else:
            rate_text = "Insufficient data"
        self.burn_rate_bar.set_data(pct_used, forecast, rate_text)

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
            self.hide()
            if self._dismiss_enabled:
                self._outside_click_timer.stop()
                self._show_foreground = None
        else:
            self.show_near_tray()
            if self._dismiss_enabled:
                self._show_foreground = None
                QTimer.singleShot(
                    250, lambda: self._outside_click_timer.start(150)
                )

    # ------------------------------------------------------------------
    #  Click-outside-to-dismiss
    # ------------------------------------------------------------------

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

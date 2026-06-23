"""
OpenRouter Pulse - Theme and Styling
"""
from PySide6.QtGui import QColor, QFont, QLinearGradient, QRadialGradient
from PySide6.QtCore import Qt


class Colors:
    BG_DARK = QColor(18, 18, 32)
    BG_CARD = QColor(28, 28, 50)
    BG_CARD_HOVER = QColor(35, 35, 62)
    BG_SURFACE = QColor(22, 22, 40)
    BG_INPUT = QColor(15, 15, 28)

    CYAN = QColor(0, 210, 255)
    CYAN_DIM = QColor(0, 210, 255, 80)
    MAGENTA = QColor(123, 47, 247)
    MAGENTA_DIM = QColor(123, 47, 247, 80)
    PURPLE = QColor(155, 89, 255)
    TEAL = QColor(0, 230, 200)

    GREEN = QColor(46, 213, 115)
    GREEN_DIM = QColor(46, 213, 115, 60)
    YELLOW = QColor(255, 199, 0)
    YELLOW_DIM = QColor(255, 199, 0, 60)
    RED = QColor(255, 71, 87)
    RED_DIM = QColor(255, 71, 87, 60)
    ORANGE = QColor(255, 165, 2)

    TEXT_PRIMARY = QColor(240, 240, 255)
    TEXT_SECONDARY = QColor(160, 160, 200)
    TEXT_MUTED = QColor(100, 100, 140)
    TEXT_ACCENT = QColor(0, 210, 255)

    BORDER = QColor(50, 50, 80)
    BORDER_ACCENT = QColor(0, 210, 255, 40)
    BORDER_GLOW = QColor(0, 210, 255, 25)

    OVERLAY = QColor(0, 0, 0, 120)
    SHADOW = QColor(0, 0, 0, 80)

    # -- Command-center neutrals (UI overhaul) --
    WINDOW = QColor(14, 14, 22)         # #0E0E16 base fill (under acrylic)
    RAIL_BG = QColor(10, 10, 18)        # #0A0A12 nav-rail
    PANEL_BG = QColor(18, 18, 30)       # #12121E panel area
    SURFACE = QColor(22, 22, 42)        # #16162A raised card
    ELEVATED = QColor(30, 30, 56)       # #1E1E38
    HAIRLINE = QColor(32, 32, 58)       # #20203A
    BORDER_SUBTLE = QColor(38, 38, 63)  # #26263F
    TEXT_BRIGHT = QColor(236, 236, 247) # #ECECF7

    @staticmethod
    def credit_color(percent):
        if percent > 0.5:
            return Colors.GREEN
        elif percent > 0.2:
            return Colors.YELLOW
        elif percent > 0.05:
            return Colors.ORANGE
        else:
            return Colors.RED

    @staticmethod
    def credit_color_dim(percent):
        if percent > 0.5:
            return Colors.GREEN_DIM
        elif percent > 0.2:
            return Colors.YELLOW_DIM
        else:
            return Colors.RED_DIM

    @staticmethod
    def severity_color(severity: str):
        """Map a "normal"|"warning"|"critical" severity to its dot/flag color."""
        if severity == "critical":
            return Colors.RED
        if severity == "warning":
            return Colors.YELLOW
        return Colors.GREEN


# Per-source accent identities (UI overhaul). base = primary stroke/text on
# dark; bright = a more legible fill/glow variant on dark.
SOURCE_ACCENTS = {
    "openrouter": {"base": "#7C83FF", "bright": "#9AA0FF"},
    "claude":     {"base": "#D97757", "bright": "#E8916F"},
    "gpu":        {"base": "#66A22B", "bright": "#83BF3D"},
    "system":     {"base": "#2DD4BF", "bright": "#5FE6D4"},
    # Settings is the app's own panel (not a source) — Pulse brand cyan.
    "settings":   {"base": "#00D2FF", "bright": "#5BE3FF"},
}


def accent_for(source_id: str, variant: str = "base") -> str:
    a = SOURCE_ACCENTS.get(source_id) or SOURCE_ACCENTS["openrouter"]
    return a.get(variant, a["base"])


class Fonts:
    @staticmethod
    def heading():
        f = QFont("Segoe UI", 13)
        f.setWeight(QFont.Weight.Bold)
        return f

    @staticmethod
    def subheading():
        f = QFont("Segoe UI", 10)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    @staticmethod
    def body():
        return QFont("Segoe UI", 9)

    @staticmethod
    def mono_large():
        f = QFont("Cascadia Code", 22)
        f.setWeight(QFont.Weight.Bold)
        return f

    @staticmethod
    def mono_medium():
        f = QFont("Cascadia Code", 14)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    @staticmethod
    def mono_small():
        return QFont("Cascadia Code", 9)

    @staticmethod
    def tiny():
        return QFont("Segoe UI", 8)

    @staticmethod
    def label():
        f = QFont("Segoe UI", 8)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        return f

    # -- Command-center type scale (UI overhaul) --
    @staticmethod
    def panel_title():
        f = QFont("Segoe UI", 13)
        f.setWeight(QFont.Weight.Medium)
        return f

    @staticmethod
    def hero_number():
        f = QFont("Cascadia Code", 22)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    @staticmethod
    def metric():
        f = QFont("Cascadia Code", 11)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    @staticmethod
    def meta():
        return QFont("Segoe UI", 10)


STYLESHEET = """
QWidget#DashboardWindow {
    background-color: #121220;
    border: 1px solid #2a2a48;
    border-radius: 12px;
}

QScrollArea {
    background: transparent;
    border: none;
}

QScrollBar:vertical {
    background: #121220;
    width: 6px;
    margin: 4px 1px 4px 1px;
    border-radius: 3px;
}

QScrollBar::handle:vertical {
    background: #3a3a60;
    border-radius: 3px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: #00d2ff;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: none;
}

QLineEdit {
    background-color: #0f0f1c;
    color: #f0f0ff;
    border: 1px solid #323250;
    border-radius: 6px;
    padding: 6px 10px;
    font-family: "Segoe UI";
    font-size: 9pt;
    selection-background-color: #00d2ff;
    selection-color: #121220;
}

QLineEdit:focus {
    border: 1px solid #00d2ff;
}

QToolTip {
    background-color: #1c1c32;
    color: #f0f0ff;
    border: 1px solid #00d2ff;
    border-radius: 8px;
    padding: 10px 14px;
    font-family: "Segoe UI";
    font-size: 9pt;
    opacity: 240;
}
"""

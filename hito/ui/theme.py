"""Colour tokens and the Qt style sheet for the dark (default) and light themes."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase

DARK = {
    "bg": "#111115", "sidebar": "#15151B", "surface": "#1B1B22", "card": "#1F1F27", "card_hover": "#262631",
    "input": "#17171D", "border": "#2C2C38", "border_strong": "#3A3A48",
    "text": "#ECECF2", "muted": "#9C9CB0", "faint": "#6E6E82",
    "accent": "#8B5CF6", "accent_hi": "#A78BFA", "accent_lo": "#6D28D9", "accent_soft": "rgba(139, 92, 246, 0.16)",
    "grad_a": "#7C3AED", "grad_b": "#B03AE8",
    "ok": "#22C55E", "warn": "#F59E0B", "danger": "#EF4444", "info": "#38BDF8",
    "track": "#2A2A35", "selection": "rgba(139, 92, 246, 0.28)",
}
LIGHT = {
    "bg": "#F4F4F8", "sidebar": "#ECECF3", "surface": "#FFFFFF", "card": "#FFFFFF", "card_hover": "#F5F3FF",
    "input": "#FFFFFF", "border": "#E2E2EC", "border_strong": "#CFCFDC",
    "text": "#16161D", "muted": "#5F5F72", "faint": "#8A8A9C",
    "accent": "#7C3AED", "accent_hi": "#8B5CF6", "accent_lo": "#5B21B6", "accent_soft": "rgba(124, 58, 237, 0.11)",
    "grad_a": "#7C3AED", "grad_b": "#A21CAF",
    "ok": "#16A34A", "warn": "#D97706", "danger": "#DC2626", "info": "#0284C7",
    "track": "#E6E6EF", "selection": "rgba(124, 58, 237, 0.2)",
}

_current = dict(DARK)
_name = "dark"
_check_image = ""


def ensure_assets():
    """Render the checkbox tick once (needs a QGuiApplication)."""
    global _check_image
    import os
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QPainter, QPen, QPixmap
    from hito.settings import sub_dir
    path = os.path.join(sub_dir("ui"), "check.png")
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#FFFFFF"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline([QPointF(8, 16.5), QPointF(13.5, 22), QPointF(24, 10)])
    p.end()
    if pm.save(path, "PNG"):
        _check_image = path.replace("\\", "/")


def current() -> dict:
    return _current


def name() -> str:
    return _name


def color(key: str) -> QColor:
    return QColor(_current[key])


def set_theme(theme: str):
    global _name
    _name = "light" if theme == "light" else "dark"
    _current.clear()
    _current.update(LIGHT if _name == "light" else DARK)


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def qcolor(key_or_hex: str, alpha: float = 1.0) -> QColor:
    c = QColor(_current.get(key_or_hex, key_or_hex))
    c.setAlphaF(alpha)
    return c


def ui_font_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"):
        if f in fams:
            return f
    return QFont().defaultFamily()


def icon_font_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if f in fams:
            return f
    return ""


def stylesheet() -> str:
    c = _current
    f = ui_font_family()
    tick = f"image: url({_check_image});" if _check_image else "image: none;"
    return f"""
* {{ font-family: "{f}"; font-size: 10pt; color: {c['text']}; outline: 0; }}
QMainWindow, #Root {{ background: {c['bg']}; }}
QWidget#Page {{ background: {c['bg']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border_strong']};
            padding: 6px 8px; border-radius: 6px; }}

#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
#Brand {{ font-size: 13pt; font-weight: 700; }}
#BrandSub {{ color: {c['muted']}; font-size: 8.5pt; }}
QPushButton#NavButton {{ text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
                         background: transparent; color: {c['muted']}; font-size: 10pt; }}
QPushButton#NavButton:hover {{ background: {c['card']}; color: {c['text']}; }}
QPushButton#NavButton:checked {{ background: {c['accent_soft']}; color: {c['text']}; font-weight: 600; }}

QFrame#Card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#Card[hover="true"]:hover {{ background: {c['card_hover']}; border-color: {c['border_strong']}; }}
QFrame#HeroCard {{ border-radius: 14px; border: none;
                  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['grad_a']}, stop:1 {c['grad_b']}); }}
QFrame#HeroCard QLabel {{ color: white; background: transparent; }}
QFrame#Divider {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}

QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 19pt; font-weight: 700; }}
QLabel#H2 {{ font-size: 12.5pt; font-weight: 650; }}
QLabel#H3 {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#Muted {{ color: {c['muted']}; }}
QLabel#Faint {{ color: {c['faint']}; font-size: 9pt; }}
QLabel#Big {{ font-size: 22pt; font-weight: 700; }}
QLabel#Stat {{ font-size: 15pt; font-weight: 700; }}
QLabel#Glyph {{ color: {c['accent_hi']}; }}
QLabel[badge="safe"] {{ background: {rgba(c['ok'], 0.15)}; color: {c['ok']}; border-radius: 9px; padding: 2px 8px; font-size: 8.5pt; font-weight: 600; }}
QLabel[badge="review"] {{ background: {rgba(c['warn'], 0.15)}; color: {c['warn']}; border-radius: 9px; padding: 2px 8px; font-size: 8.5pt; font-weight: 600; }}
QLabel[badge="advanced"] {{ background: {rgba(c['danger'], 0.15)}; color: {c['danger']}; border-radius: 9px; padding: 2px 8px; font-size: 8.5pt; font-weight: 600; }}
QLabel[badge="info"] {{ background: {c['accent_soft']}; color: {c['accent_hi']}; border-radius: 9px; padding: 2px 8px; font-size: 8.5pt; font-weight: 600; }}
QLabel[badge="muted"] {{ background: {c['track']}; color: {c['muted']}; border-radius: 9px; padding: 2px 8px; font-size: 8.5pt; }}

QPushButton {{ background: {c['card']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: {c['card_hover']}; border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['surface']}; }}
QPushButton:disabled {{ color: {c['faint']}; border-color: {c['border']}; background: {c['surface']}; }}
QPushButton[kind="primary"] {{ border: none; color: white; font-weight: 600;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['grad_a']}, stop:1 {c['grad_b']}); }}
QPushButton[kind="primary"]:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['accent_hi']}, stop:1 {c['grad_b']}); }}
QPushButton[kind="primary"]:disabled {{ background: {c['track']}; color: {c['faint']}; }}
QPushButton[kind="danger"] {{ background: {c['danger']}; border: none; color: white; font-weight: 600; }}
QPushButton[kind="danger"]:hover {{ background: {rgba(c['danger'], 0.85)}; }}
QPushButton[kind="danger"]:disabled {{ background: {c['track']}; color: {c['faint']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {c['accent_hi']}; padding: 4px 6px; }}
QPushButton[kind="ghost"]:hover {{ color: {c['text']}; }}
QPushButton[kind="chip"] {{ border-radius: 14px; padding: 4px 12px; background: {c['surface']}; }}
QPushButton[kind="chip"]:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; color: {c['text']}; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {c['input']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 6px 9px;
    selection-background-color: {c['accent']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border_strong']};
    selection-background-color: {c['accent_soft']}; padding: 4px; }}

QTableView, QTreeView, QListView, QTreeWidget, QTableWidget {{
    background: {c['card']}; alternate-background-color: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 10px; gridline-color: transparent; selection-background-color: {c['selection']};
    selection-color: {c['text']}; }}
QTableView::item, QTreeView::item {{ padding: 4px 6px; border: none; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 7px 8px; font-weight: 600; font-size: 9pt; }}
QHeaderView {{ background: transparent; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QProgressBar {{ background: {c['track']}; border: none; border-radius: 4px; height: 8px; text-align: center;
               color: transparent; max-height: 8px; }}
QProgressBar::chunk {{ border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['grad_a']}, stop:1 {c['grad_b']}); }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QTreeView::indicator, QTableView::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {c['border_strong']}; background: {c['input']}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QTableView::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']}; {tick} }}
QCheckBox::indicator:indeterminate, QTreeView::indicator:indeterminate {{ background: {c['accent_lo']}; border-color: {c['accent']}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid {c['border_strong']}; background: {c['input']}; }}
QRadioButton::indicator:checked {{ background: {c['accent']}; border: 3px solid {c['input']}; outline: 1px solid {c['accent']}; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 8px 16px; margin-right: 4px;
               border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar::tab:hover {{ color: {c['text']}; }}

QMenu {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
QDialog {{ background: {c['bg']}; }}
QSplitter::handle {{ background: transparent; }}
"""

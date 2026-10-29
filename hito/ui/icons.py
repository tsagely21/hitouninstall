"""Icons drawn from the Windows icon font (Segoe Fluent Icons on Windows 11, MDL2 on 10)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from hito.ui import theme

GLYPHS = {
    "home": "", "pie": "", "broom": "", "apps": "", "uninstall": "",
    "copy": "", "settings": "", "history": "", "search": "", "folder": "",
    "refresh": "", "drive": "", "game": "", "shield": "", "warning": "",
    "chevron": "", "up": "", "open": "", "cancel": "", "play": "",
    "check": "", "info": "", "back": "", "filter": "", "admin": "",
    "delete": "", "file": "", "grid": "", "chart": "", "store": "",
    "registry": "", "link": "", "sparkle": "", "bolt": "", "sort": "",
    "large": "", "restore": "", "theme": "", "export": "", "list": "",
    "treemap": "", "stop": "", "steam": "", "package": "", "clock": "",
}

_cache: dict[tuple, QIcon] = {}


def glyph(name: str) -> str:
    return GLYPHS.get(name, "")


def icon_font(px: int) -> QFont:
    f = QFont(theme.icon_font_family() or theme.ui_font_family())
    f.setPixelSize(px)
    return f


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    col = color or theme.current()["muted"]
    key = (name, col, size, theme.name())
    if key in _cache:
        return _cache[key]
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(col))
    p.setFont(icon_font(int(size * 1.5)))
    p.drawText(QRectF(0, 0, size * 2, size * 2), Qt.AlignCenter, glyph(name))
    p.end()
    pm.setDevicePixelRatio(2.0)
    ic = QIcon(pm)
    _cache[key] = ic
    return ic


def nav_icon(name: str) -> QIcon:
    ic = QIcon()
    c = theme.current()
    for mode, col in ((QIcon.Normal, c["muted"]), (QIcon.Active, c["text"]), (QIcon.Selected, c["accent_hi"])):
        pm = icon(name, col, 18).pixmap(18, 18)
        ic.addPixmap(pm, mode, QIcon.Off)
    ic.addPixmap(icon(name, c["accent_hi"], 18).pixmap(18, 18), QIcon.Normal, QIcon.On)
    return ic


def clear_cache():
    _cache.clear()


def app_icon() -> QIcon:
    """Window icon: the packaged .ico when present, otherwise drawn."""
    import os
    import sys
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, "assets", "hito.ico"), os.path.join(os.path.dirname(base), "assets", "hito.ico")):
        if os.path.isfile(cand):
            return QIcon(cand)
    return icon("pie", "#A78BFA", 32)

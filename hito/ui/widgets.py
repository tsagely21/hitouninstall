"""Reusable widgets: cards, usage bars, donut chart, treemap, progress panel."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QSizePolicy, QToolTip, QVBoxLayout, QWidget)

from hito.core.fsutil import human_size
from hito.ui import icons, theme


# --- small factories -----------------------------------------------------------

def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setObjectName(role)
    lb.setWordWrap(wrap)
    return lb


def button(text: str, kind: str | None = None, icon_name: str | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text.replace("&", "&&"))
    if kind:
        b.setProperty("kind", kind)
    if icon_name:
        col = "#FFFFFF" if kind in ("primary", "danger") else theme.current()["accent_hi"]
        b.setIcon(icons.icon(icon_name, col, 16))
        b.setIconSize(QSize(16, 16))
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def badge(text: str, kind: str = "info") -> QLabel:
    lb = QLabel(text)
    lb.setProperty("badge", kind)
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lb


def glyph_label(name: str, px: int = 20, color: str | None = None) -> QLabel:
    lb = QLabel(icons.glyph(name))
    lb.setFont(icons.icon_font(px))
    lb.setObjectName("Glyph")
    if color:
        lb.setStyleSheet(f"color: {color};")
    return lb


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    return f


class Card(QFrame):
    def __init__(self, parent=None, hover: bool = False, padding: int = 18, spacing: int = 10, shadow: bool = False):
        super().__init__(parent)
        self.setObjectName("Card")
        if hover:
            self.setProperty("hover", "true")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(spacing)
        if shadow:
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(24)
            eff.setOffset(0, 4)
            eff.setColor(QColor(0, 0, 0, 70))
            self.setGraphicsEffect(eff)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, "H1"))
        self.sub = label(subtitle, "Muted", wrap=True)
        if subtitle:
            col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        lay.addLayout(self.actions)


# --- usage bar -------------------------------------------------------------------

class UsageBar(QWidget):
    def __init__(self, fraction: float = 0.0, height: int = 10, parent=None, alert: bool = True):
        super().__init__(parent)
        self.alert = alert  # colour the bar amber/red when nearly full (disk usage)
        self.fraction = max(0.0, min(1.0, fraction))
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_fraction(self, f: float):
        self.fraction = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(theme.color("track"))
        p.drawRoundedRect(r, rad, rad)
        if self.fraction > 0:
            w = max(r.height(), r.width() * self.fraction)
            fill = QRectF(r.x(), r.y(), w, r.height())
            g = QLinearGradient(fill.topLeft(), fill.topRight())
            c = theme.current()
            if self.alert and self.fraction >= 0.95:
                g.setColorAt(0, QColor(c["danger"]))
                g.setColorAt(1, QColor(c["danger"]).lighter(120))
            elif self.alert and self.fraction >= 0.85:
                g.setColorAt(0, QColor(c["warn"]))
                g.setColorAt(1, QColor(c["danger"]))
            else:
                g.setColorAt(0, QColor(c["grad_a"]))
                g.setColorAt(1, QColor(c["grad_b"]))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(fill, rad, rad)


class DriveCard(Card):
    scanRequested = Signal(str)

    def __init__(self, drive, parent=None):
        super().__init__(parent, hover=True, padding=16, spacing=8)
        self.drive = drive
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(glyph_label("drive", 22))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(drive.title, "H3"))
        col.addWidget(label(f"{drive.kind} - {drive.filesystem or 'unknown'}", "Faint"))
        top.addLayout(col, 1)
        pct = label(f"{drive.percent:.0f}%", "Stat")
        top.addWidget(pct)
        self.lay.addLayout(top)
        self.lay.addWidget(UsageBar(drive.used / drive.total if drive.total else 0))
        row = QHBoxLayout()
        row.addWidget(label(f"{human_size(drive.free)} free of {human_size(drive.total)}", "Muted"), 1)
        row.addWidget(label(f"{human_size(drive.used)} used", "Faint"))
        self.lay.addLayout(row)
        b = button("Scan", "ghost", "pie", "Analyze what is using space on this drive")
        b.clicked.connect(lambda: self.scanRequested.emit(drive.root))
        row2 = QHBoxLayout()
        row2.addStretch(1)
        row2.addWidget(b)
        self.lay.addLayout(row2)


class StatTile(Card):
    def __init__(self, title: str, value: str = "-", sub: str = "", glyph: str = "info", parent=None):
        super().__init__(parent, padding=16, spacing=4)
        top = QHBoxLayout()
        top.addWidget(label(title, "Muted"), 1)
        top.addWidget(glyph_label(glyph, 18))
        self.lay.addLayout(top)
        self.value = label(value, "Big")
        self.lay.addWidget(self.value)
        self.sub = label(sub, "Faint", wrap=True)
        self.lay.addWidget(self.sub)

    def set(self, value: str, sub: str | None = None):
        self.value.setText(value)
        if sub is not None:
            self.sub.setText(sub)


# --- progress panel --------------------------------------------------------------

class ProgressPanel(Card):
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, padding=14, spacing=6)
        row = QHBoxLayout()
        self.title = label("Working...", "H3")
        row.addWidget(self.title, 1)
        self.cancel_btn = button("Cancel", None, "stop")
        self.cancel_btn.clicked.connect(self.cancelRequested.emit)
        row.addWidget(self.cancel_btn)
        self.lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.lay.addWidget(self.bar)
        self.detail = label("", "Faint")
        self.detail.setTextInteractionFlags(Qt.NoTextInteraction)
        self.lay.addWidget(self.detail)
        self.hide()

    def start(self, title: str, determinate: bool = False):
        self.title.setText(title)
        self.bar.setRange(0, 100 if determinate else 0)
        self.bar.setValue(0)
        self.detail.setText("")
        self.cancel_btn.setEnabled(True)
        self.show()

    def update_progress(self, text: str, percent: float | None = None):
        fm = QFontMetrics(self.detail.font())
        self.detail.setText(fm.elidedText(text, Qt.ElideMiddle, max(200, self.width() - 40)))
        if percent is not None:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 100)
            self.bar.setValue(int(percent))

    def finish(self):
        self.hide()


# --- donut chart -----------------------------------------------------------------

class DonutChart(QWidget):
    segmentClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[tuple[str, str, float, str]] = []  # key, label, value, color
        self.center_title = ""
        self.center_sub = ""
        self.hover = -1
        self.selected: str | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, segments, title: str = "", sub: str = ""):
        self.segments = [s for s in segments if s[2] > 0]
        self.center_title, self.center_sub = title, sub
        self.hover = -1
        self.update()

    def sizeHint(self):
        return QSize(260, 260)

    def _geometry(self):
        side = min(self.width(), self.height()) - 16
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        return r, side * 0.17

    def _index_at(self, pos) -> int:
        r, thick = self._geometry()
        c = r.center()
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        dist = math.hypot(dx, dy)
        outer = r.width() / 2
        if dist > outer + 6 or dist < outer - thick - 6:
            return -1
        ang = (90 - math.degrees(math.atan2(-dy, dx))) % 360  # clockwise from 12 o'clock
        total = sum(s[2] for s in self.segments) or 1
        acc = 0.0
        for i, s in enumerate(self.segments):
            span = s[2] / total * 360
            if acc <= ang < acc + span:
                return i
            acc += span
        return -1

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position())
        if i != self.hover:
            self.hover = i
            self.update()
        if i >= 0:
            k, lbl, v, _ = self.segments[i]
            total = sum(s[2] for s in self.segments) or 1
            QToolTip.showText(e.globalPosition().toPoint(), f"<b>{lbl}</b><br>{human_size(v)} - {v / total * 100:.1f}%", self)
            self.setCursor(Qt.PointingHandCursor)
        else:
            QToolTip.hideText()
            self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, _):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._index_at(e.position())
        if i >= 0:
            key = self.segments[i][0]
            self.selected = None if self.selected == key else key
            self.segmentClicked.emit(self.selected or "")
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r, thick = self._geometry()
        total = sum(s[2] for s in self.segments)
        if total <= 0:
            pen = QPen(theme.color("track"), thick, Qt.SolidLine, Qt.FlatCap)
            p.setPen(pen)
            inner = r.adjusted(thick / 2, thick / 2, -thick / 2, -thick / 2)
            p.drawArc(inner, 0, 360 * 16)
        else:
            start = 90.0
            gap = 1.2 if len(self.segments) > 1 else 0
            for i, (key, _lbl, val, col) in enumerate(self.segments):
                span = val / total * 360
                grow = 6 if (i == self.hover or key == self.selected) else 0
                dim = self.selected is not None and key != self.selected
                rr = r.adjusted(thick / 2 - grow / 2, thick / 2 - grow / 2, -thick / 2 + grow / 2, -thick / 2 + grow / 2)
                c = QColor(col)
                if dim:
                    c.setAlphaF(0.35)
                pen = QPen(c, thick + grow, Qt.SolidLine, Qt.FlatCap)
                p.setPen(pen)
                draw_span = max(0.0, span - gap)
                if draw_span > 0:
                    # Qt angles run counter-clockwise from 3 o'clock; a negative span draws clockwise.
                    p.drawArc(rr, int(start * 16), int(-draw_span * 16))
                start -= span
        p.setPen(theme.color("text"))
        f = QFont(theme.ui_font_family())
        f.setPixelSize(max(14, int(r.width() * 0.105)))
        f.setBold(True)
        p.setFont(f)
        center = r.center()
        p.drawText(QRectF(r.x(), center.y() - r.height() * 0.13, r.width(), r.height() * 0.16), Qt.AlignCenter, self.center_title)
        f2 = QFont(theme.ui_font_family())
        f2.setPixelSize(max(10, int(r.width() * 0.05)))
        p.setFont(f2)
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(r.x(), center.y() + r.height() * 0.03, r.width(), r.height() * 0.1), Qt.AlignCenter, self.center_sub)


class Legend(QWidget):
    itemClicked = Signal(str)

    def __init__(self, parent=None, max_rows: int = 14):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(2)
        self.max_rows = max_rows

    def set_data(self, segments, total: float):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for key, lbl, val, col in segments[: self.max_rows]:
            row = QPushButton()
            row.setProperty("kind", "ghost")
            row.setCursor(Qt.PointingHandCursor)
            row.setStyleSheet("QPushButton { text-align: left; padding: 5px 6px; border-radius: 6px; }"
                              f"QPushButton:hover {{ background: {theme.current()['card_hover']}; }}")
            h = QHBoxLayout(row)
            h.setContentsMargins(6, 2, 6, 2)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {col}; border-radius: 5px;")
            h.addWidget(dot)
            name = label(lbl)
            h.addWidget(name, 1)
            pct = label(f"{(val / total * 100 if total else 0):.1f}%", "Faint")
            h.addWidget(pct)
            sz = label(human_size(val))
            sz.setMinimumWidth(70)
            sz.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(sz)
            row.setMinimumHeight(30)
            row.clicked.connect(lambda _=False, k=key: self.itemClicked.emit(k))
            self.lay.addWidget(row)
        self.lay.addStretch(1)


# --- treemap ---------------------------------------------------------------------

def _worst(row: list[float], short: float) -> float:
    s = sum(row)
    if s <= 0 or short <= 0:
        return float("inf")
    return max(short * short * max(row) / (s * s), (s * s) / (short * short * min(row)))


def squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap layout (Bruls et al.). values must be sorted descending and > 0."""
    total = sum(values)
    if total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0, 0) for _ in values]
    scale = w * h / total
    sizes = [v * scale for v in values]
    out: list[tuple[float, float, float, float]] = []
    i = 0
    while i < len(sizes):
        short = min(w, h)
        row = [sizes[i]]
        i += 1
        while i < len(sizes) and _worst(row + [sizes[i]], short) <= _worst(row, short):
            row.append(sizes[i])
            i += 1
        s = sum(row)
        if w >= h:
            rw = s / h if h else 0
            yy = y
            for v in row:
                rh = v / rw if rw else 0
                out.append((x, yy, rw, rh))
                yy += rh
            x += rw
            w -= rw
        else:
            rh = s / w if w else 0
            xx = x
            for v in row:
                rw = v / rh if rh else 0
                out.append((xx, y, rw, rh))
                xx += rw
            y += rh
            h -= rh
    return out


class TreemapItem:
    __slots__ = ("label", "size", "color", "payload", "is_dir", "rect", "children", "path")

    def __init__(self, label, size, color, payload, is_dir, path=""):
        self.label = label
        self.size = size
        self.color = color
        self.payload = payload
        self.is_dir = is_dir
        self.rect = QRectF()
        self.children: list[TreemapItem] = []
        self.path = path


class Treemap(QWidget):
    """Two-level squarified treemap. Emits payloads of clicked items."""
    itemActivated = Signal(object)            # dir payload to drill into (or file payload)
    contextRequested = Signal(object, object)  # payload, global QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[TreemapItem] = []
        self.hover: TreemapItem | None = None
        self.total = 0
        self.setMouseTracking(True)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._child_provider = None
        self._dirty = True

    def set_items(self, items: list[TreemapItem], child_provider=None):
        """child_provider(item) -> list[TreemapItem] for the nested level (dirs only)."""
        self.items = [i for i in items if i.size > 0]
        self.items.sort(key=lambda i: i.size, reverse=True)
        self.total = sum(i.size for i in self.items)
        self._child_provider = child_provider
        self.hover = None
        self._dirty = True
        self.update()

    def resizeEvent(self, e):
        self._dirty = True
        super().resizeEvent(e)

    def _layout(self):
        self._dirty = False
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        rects = squarify([i.size for i in self.items], r.x(), r.y(), r.width(), r.height())
        for it, (x, y, w, h) in zip(self.items, rects):
            it.rect = QRectF(x, y, w, h)
            it.children = []
            if it.is_dir and self._child_provider and w > 90 and h > 60:
                kids = [k for k in self._child_provider(it) if k.size > 0]
                kids.sort(key=lambda k: k.size, reverse=True)
                inner = it.rect.adjusted(3, 20, -3, -3)
                sub = squarify([k.size for k in kids], inner.x(), inner.y(), inner.width(), inner.height())
                for k, (kx, ky, kw, kh) in zip(kids, sub):
                    k.rect = QRectF(kx, ky, kw, kh)
                it.children = kids

    def _hit(self, pos) -> TreemapItem | None:
        for it in self.items:
            if it.rect.contains(pos):
                for k in it.children:
                    if k.rect.contains(pos):
                        return k
                return it
        return None

    def mouseMoveEvent(self, e):
        hit = self._hit(e.position())
        if hit is not self.hover:
            self.hover = hit
            self.update()
        if hit:
            pct = hit.size / self.total * 100 if self.total else 0
            kind = "Folder" if hit.is_dir else "Files"
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"<b>{hit.label}</b><br>{kind} - {human_size(hit.size)} ({pct:.1f}% of view)"
                              + (f"<br><span style='color:gray'>{hit.path}</span>" if hit.path else "")
                              + ("<br><i>Click to open</i>" if hit.is_dir else ""), self)
            self.setCursor(Qt.PointingHandCursor if hit.is_dir else Qt.ArrowCursor)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        hit = self._hit(e.position())
        if not hit:
            return
        if e.button() == Qt.RightButton:
            self.contextRequested.emit(hit.payload, e.globalPosition().toPoint())
        elif e.button() == Qt.LeftButton:
            self.itemActivated.emit(hit.payload)

    def paintEvent(self, _):
        if self._dirty:
            self._layout()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.items:
            p.setPen(theme.color("muted"))
            p.drawText(self.rect(), Qt.AlignCenter, "Nothing to show here")
            return
        small = QFont(theme.ui_font_family())
        small.setPixelSize(11)
        bold = QFont(small)
        bold.setBold(True)
        for it in self.items:
            self._draw_box(p, it, top=True, small=small, bold=bold)
            for k in it.children:
                self._draw_box(p, k, top=False, small=small, bold=bold)
        if self.hover is not None:
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self.hover.rect.adjusted(1, 1, -1, -1), 5, 5)

    def _draw_box(self, p: QPainter, it: TreemapItem, top: bool, small: QFont, bold: QFont):
        r = it.rect.adjusted(1, 1, -1, -1)
        if r.width() < 2 or r.height() < 2:
            return
        base = QColor(it.color)
        if not top:
            base = base.lighter(115)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, base.lighter(112))
        g.setColorAt(1, base.darker(135))
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(r, 5 if top else 3, 5 if top else 3)
        if top and it.children:
            p.setBrush(QColor(0, 0, 0, 70))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r.adjusted(2, 20, -2, -2), 3, 3)
        if r.width() > 46 and r.height() > 18:
            p.setPen(QColor(255, 255, 255, 235))
            p.setFont(bold if top else small)
            fm = QFontMetrics(p.font())
            text = f"{it.label}  {human_size(it.size)}"
            text = fm.elidedText(text, Qt.ElideRight, int(r.width() - 10))
            p.drawText(QRectF(r.x() + 6, r.y() + 2, r.width() - 10, 17), Qt.AlignLeft | Qt.AlignVCenter, text)

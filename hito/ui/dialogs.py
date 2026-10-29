"""Confirmation and result dialogs. Every destructive action goes through ConfirmDialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout)

from hito.ui.widgets import button, glyph_label, label


class ConfirmDialog(QDialog):
    def __init__(self, parent, title: str, summary: str, lines: list[str], confirm_text: str = "Continue",
                 danger: bool = True, require_ack: str | None = None, options: list[tuple[str, bool]] | None = None,
                 glyph: str = "warning"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(620, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(glyph_label(glyph, 26))
        col = QVBoxLayout()
        col.addWidget(label(title, "H2"))
        s = label(summary, "Muted", wrap=True)
        s.setTextFormat(Qt.RichText)
        col.addWidget(s)
        head.addLayout(col, 1)
        lay.addLayout(head)
        lay.addWidget(label("Exactly what will happen:", "H3"))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText("\n".join(lines))
        lay.addWidget(self.text, 1)
        self.option_boxes: list[QCheckBox] = []
        for text, checked in options or []:
            cb = QCheckBox(text)
            cb.setChecked(checked)
            self.option_boxes.append(cb)
            lay.addWidget(cb)
        self.ack = None
        if require_ack:
            self.ack = QCheckBox(require_ack)
            lay.addWidget(self.ack)
        row = QHBoxLayout()
        exp = button("Save list...", None, "export", "Save this list of actions to a text file")
        exp.clicked.connect(self._export)
        row.addWidget(exp)
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok = button(confirm_text, "danger" if danger else "primary")
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        lay.addLayout(row)
        if self.ack:
            self.ok.setEnabled(False)
            self.ack.toggled.connect(self.ok.setEnabled)
        cancel.setFocus()

    def option(self, i: int) -> bool:
        return self.option_boxes[i].isChecked()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save action list", "actions.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text.toPlainText())


class InfoDialog(QDialog):
    def __init__(self, parent, title: str, summary: str, details: str = "", glyph: str = "info"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(glyph_label(glyph, 26))
        col = QVBoxLayout()
        col.addWidget(label(title, "H2"))
        s = QLabel(summary)
        s.setWordWrap(True)
        s.setObjectName("Muted")
        s.setTextFormat(Qt.RichText)
        col.addWidget(s)
        head.addLayout(col, 1)
        lay.addLayout(head)
        if details:
            t = QPlainTextEdit()
            t.setReadOnly(True)
            t.setPlainText(details)
            t.setMinimumHeight(180)
            lay.addWidget(t, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = button("OK", "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)


def report_details(report) -> str:
    lines = []
    if report.skipped:
        lines.append(f"Skipped ({len(report.skipped)}):")
        lines += [f"  {p}\n      {why}" for p, why in report.skipped[:500]]
    if report.errors:
        lines.append(f"\nCould not remove ({len(report.errors)}):")
        lines += [f"  {p}\n      {why}" for p, why in report.errors[:500]]
    return "\n".join(lines)

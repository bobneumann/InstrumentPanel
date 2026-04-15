"""
Instrument Panel — Interactive Layout Designer

  Live mode  : gauges animate with real psutil data.
  Edit mode  : press E (or toolbar button) to enter.

In edit mode:
  - Grid lines appear; gauges stay live.
  - Click a gauge to select it (amber border); its properties load in the sidebar.
  - Drag a gauge to a new grid cell (target cell highlights in green).
  - Edit source, label, unit, min/max/danger in the sidebar; click Apply.
  - Add gauge  — places a new CPU-total gauge in the first empty cell.
  - Delete gauge — removes the selected gauge.
  - Save / Load — layout.json in the same folder as this file.
  - Press E or Escape (or "LIVE MODE" button) to return to live mode.
    Layout is auto-saved on exit from edit mode.
"""

import os
import sys
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QVBoxLayout,
    QLabel, QComboBox, QLineEdit,
    QDoubleSpinBox, QSpinBox, QCheckBox,
    QPushButton, QFrame,
    QDialog, QListWidget, QListWidgetItem, QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QShortcut, QKeySequence

import host_registry
from gauge import Gauge, GaugeConfig, GaugeTheme, theme_wwii_cockpit, theme_f1_racing
from datasources import (
    cpu_total, cpu_core, ram_percent,
    disk_percent, net_bytes_recv_rate, net_bytes_sent_rate,
)


# ============================================================
#  Sidebar style (defined here so THEME_REGISTRY can call it)
# ============================================================

def _sidebar_style(bg="#191c12", input_bg="#22261a", border="#404530",
                   fg="#c8bfa8", dim="#8a8270",
                   btn_bg="#2e3220", btn_border="#4e5238") -> str:
    return f"""
QWidget           {{ background-color: {bg}; color: {fg}; }}
QLabel            {{ color: {dim}; font-size: 9px; }}
QComboBox,
QLineEdit,
QDoubleSpinBox,
QSpinBox          {{ background: {input_bg}; color: {fg};
                     border: 1px solid {border}; padding: 2px 4px; }}
QComboBox QAbstractItemView
                  {{ background: {input_bg}; color: {fg};
                     selection-background-color: {btn_bg};
                     selection-color: #e8e0cc;
                     border: 1px solid {border}; }}
QPushButton       {{ background: {btn_bg}; color: {fg};
                     border: 1px solid {btn_border}; padding: 4px 8px; }}
QPushButton:hover {{ background: {btn_border}; }}
QCheckBox         {{ color: {fg}; }}
QFrame            {{ color: {btn_bg}; }}
"""


# ============================================================
#  Theme registry
# ============================================================

THEME_REGISTRY: dict = {
    "wwii": {
        "name":       "WWII Cockpit",
        "factory":    theme_wwii_cockpit,
        "bg":         "#3C4323",
        "toolbar_bg": "#2a2e1a",
        "toolbar_fg": "#c8bfa8",
        "sidebar":    _sidebar_style(),   # olive-dark defaults
    },
    "f1": {
        "name":       "F1 Racing",
        "factory":    theme_f1_racing,
        "bg":         "#0E0E10",
        "toolbar_bg": "#131316",
        "toolbar_fg": "#d0d0d8",
        "sidebar":    _sidebar_style(bg="#0f0f14", input_bg="#1a1a22",
                                     border="#2e2e40", fg="#d0d0d8",
                                     dim="#707080", btn_bg="#1a1a28",
                                     btn_border="#2e2e44"),
    },
}


# ============================================================
#  Source registry
#  Each entry: {"label": str, "unit": str, "factory": callable}
#  factory() -> source_callable; factory is called once per slot instance.
# ============================================================

def _local(label, unit, factory):
    return {"label": label, "unit": unit, "factory": factory, "group": None}

SOURCE_REGISTRY: dict = {
    "cpu_total": _local("CPU TOTAL",  "PERCENT",  cpu_total),
    "ram":       _local("MEMORY",     "PERCENT",  ram_percent),
    "disk_c":    _local("DISK C:",    "PERCENT",  lambda: disk_percent("C:\\")),
    "disk_d":    _local("DISK D:",    "PERCENT",  lambda: disk_percent("D:\\")),
    "net_in":    _local("NET IN",     "MB / SEC", net_bytes_recv_rate),
    "net_out":   _local("NET OUT",    "MB / SEC", net_bytes_sent_rate),
    "core_0":    _local("CORE 0",     "PERCENT",  lambda: cpu_core(0)),
    "core_1":    _local("CORE 1",     "PERCENT",  lambda: cpu_core(1)),
    "core_2":    _local("CORE 2",     "PERCENT",  lambda: cpu_core(2)),
    "core_3":    _local("CORE 3",     "PERCENT",  lambda: cpu_core(3)),
    "core_4":    _local("CORE 4",     "PERCENT",  lambda: cpu_core(4)),
    "core_5":    _local("CORE 5",     "PERCENT",  lambda: cpu_core(5)),
    "core_6":    _local("CORE 6",     "PERCENT",  lambda: cpu_core(6)),
    "core_7":    _local("CORE 7",     "PERCENT",  lambda: cpu_core(7)),
}


# ============================================================
#  Data model
# ============================================================

@dataclass
class LayoutSlot:
    source_key:  str
    label:       str   = ""          # empty = use registry default
    unit:        str   = ""          # empty = use registry default
    min_val:     float = 0.0
    max_val:     float = 100.0
    danger_from: Optional[float] = 80.0   # None = no danger arc
    row:         int   = 0
    col:         int   = 0
    row_span:    int   = 1
    col_span:    int   = 1


@dataclass
class LayoutModel:
    grid_cols: int  = 3
    grid_rows: int  = 2
    theme_key: str  = "wwii"
    slots:     list = field(default_factory=list)

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump({
                "grid_cols": self.grid_cols,
                "grid_rows": self.grid_rows,
                "theme_key": self.theme_key,
                "slots":     [asdict(s) for s in self.slots],
            }, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "LayoutModel":
        with open(path) as f:
            d = json.load(f)
        slots = [LayoutSlot(**s) for s in d["slots"]]
        return cls(
            grid_cols = d["grid_cols"],
            grid_rows = d["grid_rows"],
            theme_key = d.get("theme_key", "wwii"),
            slots     = slots,
        )


# ============================================================
#  Edit overlay — transparent child on top of LayoutCanvas
# ============================================================

class _EditOverlay(QWidget):
    """
    Covers LayoutCanvas in edit mode.  Draws grid lines, selection border,
    and drag-target highlight.  Captures all mouse events for drag logic.
    """

    def __init__(self, canvas: "LayoutCanvas"):
        super().__init__(canvas)
        self._canvas = canvas
        self.setMouseTracking(True)
        # Semi-transparent so gauges show through
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, event):
        c   = self._canvas
        m   = c._model
        p   = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # ── grid lines ──────────────────────────────────────────────────
        p.setPen(QPen(QColor(120, 130, 80, 90), 1))
        cell_w = w / m.grid_cols
        cell_h = h / m.grid_rows
        for col in range(m.grid_cols + 1):
            x = int(col * cell_w)
            p.drawLine(x, 0, x, h)
        for row in range(m.grid_rows + 1):
            y = int(row * cell_h)
            p.drawLine(0, y, w, y)

        # ── drag target highlight ────────────────────────────────────────
        if c._drag_cell is not None:
            row, col = c._drag_cell
            r = c._cell_rect(row, col, 1, 1)
            # Check if target cell is occupied (swap) or empty (move)
            occupied = any(
                i != c._drag_idx and s.row == row and s.col == col
                for i, s in enumerate(c._model.slots)
            )
            fill  = QColor(210, 160, 60, 55)  if occupied else QColor(140, 180, 90, 55)
            border= QColor(210, 160, 60, 200) if occupied else QColor(140, 180, 90, 200)
            p.fillRect(r, fill)
            p.setPen(QPen(border, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1, 1, -1, -1))

        # ── selection border ────────────────────────────────────────────
        if c._selected >= 0 and c._selected < len(m.slots):
            s = m.slots[c._selected]
            r = c._cell_rect(s.row, s.col, s.row_span, s.col_span)
            p.setPen(QPen(QColor(210, 175, 80, 230), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(2, 2, -2, -2))

        # ── "EDIT MODE" watermark ────────────────────────────────────────
        p.setFont(QFont("Arial Narrow", 9, QFont.Bold))
        p.setPen(QColor(160, 170, 110, 90))
        p.drawText(8, h - 8, "EDIT MODE  —  E to exit  —  click to select  —  drag to move")

        p.end()

    def mousePressEvent(self, event):
        c = self._canvas
        pos = event.position()
        x, y = int(pos.x()), int(pos.y())
        idx = c._hit_slot(x, y)
        c.select_slot(idx)
        if idx >= 0:
            c._drag_idx   = idx
            c._drag_cell  = None

    def mouseMoveEvent(self, event):
        c = self._canvas
        if c._drag_idx < 0:
            return
        pos = event.position()
        row, col = c._pos_to_cell(int(pos.x()), int(pos.y()))
        slot = c._model.slots[c._drag_idx]
        # Only update if different from current position
        if (row, col) != (slot.row, slot.col):
            c._drag_cell = (row, col)
        else:
            c._drag_cell = None
        self.update()

    def mouseReleaseEvent(self, event):
        c = self._canvas
        if c._drag_idx >= 0 and c._drag_cell is not None:
            row, col = c._drag_cell
            c.move_slot(c._drag_idx, row, col)
        c._drag_idx  = -1
        c._drag_cell = None
        self.update()


# ============================================================
#  LayoutCanvas — the gauge grid
# ============================================================

_SPACING = 8   # pixels between gauges


class LayoutCanvas(QWidget):
    """
    Owns Gauge children, positions them manually, drives poll + animation.
    In edit mode, raises an overlay that handles drag interaction.
    """

    slot_selected = Signal(int)   # emits slot index (-1 = none selected)

    def __init__(self, model: LayoutModel, theme=None,
                 poll_ms: int = 1000, fps: int = 60, parent=None):
        super().__init__(parent)
        self._model   = model
        self._theme   = theme or theme_wwii_cockpit()
        self._gauges: list[Gauge]    = []
        self._sources: list[Callable] = []
        self._selected  = -1
        self._drag_idx  = -1
        self._drag_cell = None

        self._rebuild()

        # Overlay — hidden until edit mode
        self._overlay = _EditOverlay(self)
        self._overlay.hide()

        # Animation timer (repaints at fps)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._repaint_all)
        self._anim_timer.start(max(1, 1000 // fps))

        # Poll timer (reads data sources)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(poll_ms)

        self._poll()   # initial read before first timer fire

    # ── internal helpers ─────────────────────────────────────────────── #

    def _make_config(self, slot: LayoutSlot) -> GaugeConfig:
        info  = SOURCE_REGISTRY.get(slot.source_key, {})
        return GaugeConfig(
            label       = slot.label or info.get("label", slot.source_key),
            unit        = slot.unit  or info.get("unit",  ""),
            min_val     = slot.min_val,
            max_val     = slot.max_val,
            danger_from = slot.danger_from,
        )

    def _make_source(self, slot: LayoutSlot) -> Callable:
        info = SOURCE_REGISTRY.get(slot.source_key)
        if info:
            return info["factory"]()
        return lambda: 0.0

    def _cell_rect(self, row: int, col: int,
                   row_span: int = 1, col_span: int = 1) -> QRect:
        w, h  = self.width(), self.height()
        cw    = w / self._model.grid_cols
        ch    = h / self._model.grid_rows
        s     = _SPACING
        x     = int(col * cw) + s
        y     = int(row * ch) + s
        rw    = int(col_span * cw) - 2 * s
        rh    = int(row_span * ch) - 2 * s
        return QRect(x, y, max(rw, 1), max(rh, 1))

    def _reposition(self):
        for slot, gauge in zip(self._model.slots, self._gauges):
            gauge.setGeometry(
                self._cell_rect(slot.row, slot.col, slot.row_span, slot.col_span)
            )

    def _rebuild(self):
        for g in self._gauges:
            g.deleteLater()
        self._gauges.clear()
        self._sources.clear()
        for slot in self._model.slots:
            g = Gauge(config=self._make_config(slot), theme=self._theme, parent=self)
            g.show()
            self._gauges.append(g)
            self._sources.append(self._make_source(slot))
        self._reposition()

    def _poll(self):
        for src, gauge in zip(self._sources, self._gauges):
            try:
                gauge.value = src()
            except Exception:
                pass

    def _repaint_all(self):
        for g in self._gauges:
            g.update()

    # ── resize ───────────────────────────────────────────────────────── #

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()
        self._overlay.setGeometry(0, 0, self.width(), self.height())

    # ── hit-testing & coordinate helpers ─────────────────────────────── #

    def _hit_slot(self, x: int, y: int) -> int:
        """Return index of gauge whose geometry contains (x, y), or -1."""
        for i, gauge in enumerate(self._gauges):
            if gauge.geometry().contains(x, y):
                return i
        return -1

    def _pos_to_cell(self, x: int, y: int) -> tuple:
        """Convert pixel position to (row, col), clamped to grid."""
        m   = self._model
        cw  = self.width()  / m.grid_cols
        ch  = self.height() / m.grid_rows
        col = max(0, min(m.grid_cols - 1, int(x / cw)))
        row = max(0, min(m.grid_rows - 1, int(y / ch)))
        return row, col

    # ── public API (called from overlay / sidebar) ────────────────────── #

    def select_slot(self, idx: int):
        self._selected = idx
        self._overlay.update()
        self.slot_selected.emit(idx)

    def move_slot(self, idx: int, new_row: int, new_col: int):
        old_row = self._model.slots[idx].row
        old_col = self._model.slots[idx].col
        # Find any gauge already occupying the target cell
        swap_idx = next(
            (i for i, s in enumerate(self._model.slots)
             if i != idx and s.row == new_row and s.col == new_col),
            None
        )
        self._model.slots[idx].row = new_row
        self._model.slots[idx].col = new_col
        if swap_idx is not None:
            # Swap: displaced gauge goes to the vacated cell
            self._model.slots[swap_idx].row = old_row
            self._model.slots[swap_idx].col = old_col
        self._reposition()
        self._overlay.update()

    def update_slot(self, idx: int, slot: LayoutSlot):
        """Replace slot data and refresh gauge + source."""
        old_key = self._model.slots[idx].source_key
        self._model.slots[idx] = slot
        g = self._gauges[idx]
        g.config = self._make_config(slot)
        # Only reset needle when the data source changes — not for visual edits
        if slot.source_key != old_key:
            g._value = g.config.min_val
            g._display_value = g.config.min_val
            self._sources[idx] = self._make_source(slot)
        g.update()

    def add_slot(self, slot: LayoutSlot):
        self._model.slots.append(slot)
        g = Gauge(config=self._make_config(slot), theme=self._theme, parent=self)
        g.show()
        self._gauges.append(g)
        self._sources.append(self._make_source(slot))
        self._reposition()
        if self._overlay.isVisible():
            self._overlay.raise_()   # new child lands on top; push overlay back up
        self.select_slot(len(self._model.slots) - 1)

    def remove_slot(self, idx: int):
        self._gauges[idx].deleteLater()
        del self._gauges[idx]
        del self._sources[idx]
        del self._model.slots[idx]
        self._selected = -1
        self._overlay.update()
        self.slot_selected.emit(-1)

    def set_grid_size(self, cols: int, rows: int):
        self._model.grid_cols = cols
        self._model.grid_rows = rows
        self._reposition()
        self._overlay.update()

    def load_model(self, model: LayoutModel):
        self._model = model
        self._selected = -1
        self._drag_idx = -1
        self._drag_cell = None
        self._rebuild()
        self._overlay.update()
        self.slot_selected.emit(-1)

    def set_theme(self, theme: GaugeTheme, key: str):
        """Replace theme on all gauges and update model's theme_key."""
        self._theme = theme
        self._model.theme_key = key
        for i, (slot, old_g) in enumerate(zip(self._model.slots, self._gauges)):
            old_g.deleteLater()
            new_g = Gauge(config=self._make_config(slot), theme=theme, parent=self)
            new_g.show()
            self._gauges[i] = new_g
        self._reposition()

    def set_edit_mode(self, enabled: bool):
        if enabled:
            self._overlay.setGeometry(0, 0, self.width(), self.height())
            self._overlay.raise_()
            self._overlay.show()
        else:
            self._overlay.hide()
            self._selected  = -1
            self._drag_idx  = -1
            self._drag_cell = None


# ============================================================
#  Gauge picker dialog
# ============================================================

class _GaugePickerDialog(QDialog):
    """Simple list of available sources; user picks one to add."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Gauge")
        self.setMinimumWidth(280)
        self.chosen_key: Optional[str] = None
        self.setStyleSheet(_sidebar_style())

        vbox = QVBoxLayout(self)
        vbox.addWidget(QLabel("Choose a data source:"))

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: #22261a; color: #c8bfa8; border: 1px solid #404530; }"
            "QListWidget::item:selected { background: #3e4230; }"
        )
        self._populate_list()
        self._list.setCurrentRow(0)
        self._list.doubleClicked.connect(self._confirm)
        vbox.addWidget(self._list)

        row = QHBoxLayout()
        ok_btn = QPushButton("Add")
        ok_btn.clicked.connect(self._confirm)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(ok_btn)
        row.addWidget(cancel_btn)
        vbox.addLayout(row)

    def _populate_list(self):
        def _header(text):
            it = QListWidgetItem(text)
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor("#707860"))
            self._list.addItem(it)

        def _entry(key, info):
            it = QListWidgetItem(f"  {info['label']}   ({info['unit']})")
            it.setData(Qt.UserRole, key)
            self._list.addItem(it)

        # Local sources first
        _header("── Local ──────────────────────")
        for key, info in SOURCE_REGISTRY.items():
            if info.get("group") is None:
                _entry(key, info)

        # Remote sources grouped by host
        groups: dict = {}
        for key, info in SOURCE_REGISTRY.items():
            g = info.get("group")
            if g:
                groups.setdefault(g, []).append((key, info))

        for group_name, entries in groups.items():
            _header(f"── {group_name} {'─' * max(1, 28 - len(group_name))}")
            for key, info in entries:
                _entry(key, info)

    def _confirm(self):
        item = self._list.currentItem()
        if item and bool(item.flags() & Qt.ItemIsEnabled) and item.data(Qt.UserRole):
            self.chosen_key = item.data(Qt.UserRole)
            self.accept()


# ============================================================
#  EditSidebar
# ============================================================


def _sep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Plain)
    return line


class EditSidebar(QWidget):
    def __init__(self, canvas: LayoutCanvas):
        super().__init__()
        self._canvas = canvas
        self._idx    = -1
        self.setFixedWidth(230)
        self.setStyleSheet(_sidebar_style())
        self._build_ui()
        canvas.slot_selected.connect(self._on_select)

    def _build_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setSpacing(6)
        vbox.setContentsMargins(10, 10, 10, 10)

        # Title
        self._title = QLabel("EDIT MODE")
        self._title.setAlignment(Qt.AlignCenter)
        f = self._title.font()
        f.setBold(True)
        f.setPointSize(10)
        self._title.setFont(f)
        self._title.setStyleSheet("color: #d4cbb8; letter-spacing: 2px;")
        vbox.addWidget(self._title)
        vbox.addWidget(_sep())

        # ── Panel theme (global) ──────────────────────────────────────
        vbox.addWidget(QLabel("PANEL THEME"))
        self._theme_combo = QComboBox()
        for key, info in THEME_REGISTRY.items():
            self._theme_combo.addItem(info["name"], key)
        vbox.addWidget(self._theme_combo)
        self._theme_combo.currentIndexChanged.connect(self._change_theme)
        vbox.addWidget(_sep())

        # ── Gauge properties ──────────────────────────────────────────
        vbox.addWidget(QLabel("SOURCE"))
        self._src = QComboBox()
        for key, info in SOURCE_REGISTRY.items():
            self._src.addItem(info["label"], key)
        vbox.addWidget(self._src)

        vbox.addWidget(QLabel("LABEL  (blank = source default)"))
        self._label = QLineEdit()
        self._label.setPlaceholderText("leave blank for default")
        vbox.addWidget(self._label)

        vbox.addWidget(QLabel("UNIT  (blank = source default)"))
        self._unit = QLineEdit()
        self._unit.setPlaceholderText("leave blank for default")
        vbox.addWidget(self._unit)

        # Min / Max row
        minmax = QWidget()
        mm_l = QHBoxLayout(minmax)
        mm_l.setContentsMargins(0, 0, 0, 0); mm_l.setSpacing(6)
        mm_l.addWidget(QLabel("MIN"))
        self._min = QDoubleSpinBox()
        self._min.setRange(-99999, 99999)
        mm_l.addWidget(self._min)
        mm_l.addWidget(QLabel("MAX"))
        self._max = QDoubleSpinBox()
        self._max.setRange(-99999, 99999)
        self._max.setValue(100)
        mm_l.addWidget(self._max)
        vbox.addWidget(minmax)

        # Danger row
        danger = QWidget()
        dng_l = QHBoxLayout(danger)
        dng_l.setContentsMargins(0, 0, 0, 0); dng_l.setSpacing(6)
        self._danger_chk = QCheckBox("DANGER AT")
        self._danger_chk.setChecked(True)
        self._danger_val = QDoubleSpinBox()
        self._danger_val.setRange(-99999, 99999)
        self._danger_val.setValue(80)
        dng_l.addWidget(self._danger_chk)
        dng_l.addWidget(self._danger_val)
        vbox.addWidget(danger)
        self._danger_chk.toggled.connect(self._danger_val.setEnabled)

        # Apply
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._apply)
        vbox.addWidget(self._apply_btn)

        # Delete
        self._del_btn = QPushButton("Delete Gauge")
        self._del_btn.clicked.connect(self._delete)
        self._del_btn.setStyleSheet(
            "QPushButton { color: #c06050; }"
            "QPushButton:hover { background: #3a2020; }"
        )
        vbox.addWidget(self._del_btn)

        vbox.addWidget(_sep())

        # ── Add gauge ─────────────────────────────────────────────────
        add_btn = QPushButton("Add Gauge")
        add_btn.clicked.connect(self._add)
        vbox.addWidget(add_btn)

        vbox.addWidget(_sep())

        # ── Grid size ─────────────────────────────────────────────────
        vbox.addWidget(QLabel("GRID SIZE"))
        grid_w = QWidget()
        grid_l = QHBoxLayout(grid_w)
        grid_l.setContentsMargins(0, 0, 0, 0); grid_l.setSpacing(6)
        grid_l.addWidget(QLabel("COLS"))
        self._cols = QSpinBox(); self._cols.setRange(1, 10)
        self._cols.setValue(self._canvas._model.grid_cols)
        grid_l.addWidget(self._cols)
        grid_l.addWidget(QLabel("ROWS"))
        self._rows = QSpinBox(); self._rows.setRange(1, 10)
        self._rows.setValue(self._canvas._model.grid_rows)
        grid_l.addWidget(self._rows)
        vbox.addWidget(grid_w)

        resize_btn = QPushButton("Resize Grid")
        resize_btn.clicked.connect(self._resize_grid)
        vbox.addWidget(resize_btn)

        vbox.addWidget(_sep())

        # ── Save / Load ───────────────────────────────────────────────
        save_btn = QPushButton("Save Layout")
        save_btn.clicked.connect(self._save)
        vbox.addWidget(save_btn)

        load_btn = QPushButton("Load Layout")
        load_btn.clicked.connect(self._load)
        vbox.addWidget(load_btn)

        vbox.addStretch()

        # Done
        done_btn = QPushButton("▶   LIVE MODE")
        done_btn.setStyleSheet(
            "QPushButton { background: #1e2e14; color: #90c060;"
            "              border: 1px solid #507840; font-weight: bold; padding: 6px; }"
            "QPushButton:hover { background: #2e3e20; }"
        )
        done_btn.clicked.connect(self._exit_edit)
        vbox.addWidget(done_btn)

        self._set_enabled(False)

    def _set_enabled(self, v: bool):
        for w in (self._src, self._label, self._unit,
                  self._min, self._max,
                  self._danger_chk, self._danger_val,
                  self._apply_btn, self._del_btn):
            w.setEnabled(v)

    # ── slot selection ─────────────────────────────────────────────── #

    def sync_theme_combo(self, key: str):
        """Called by DesignerWindow on load to sync combo to current theme."""
        ci = self._theme_combo.findData(key)
        if ci >= 0:
            # Block signal so we don't re-trigger _change_theme during load
            self._theme_combo.blockSignals(True)
            self._theme_combo.setCurrentIndex(ci)
            self._theme_combo.blockSignals(False)

    def _change_theme(self):
        key  = self._theme_combo.currentData()
        info = THEME_REGISTRY[key]
        self._canvas.set_theme(info["factory"](), key)
        w = self.window()
        if hasattr(w, "update_bg"):
            w.update_bg(info)

    def _on_select(self, idx: int):
        self._idx = idx
        if idx < 0:
            self._title.setText("EDIT MODE")
            self._set_enabled(False)
            return
        self._set_enabled(True)
        slot = self._canvas._model.slots[idx]
        self._title.setText(f"GAUGE {idx + 1}")

        ci = self._src.findData(slot.source_key)
        if ci >= 0:
            self._src.setCurrentIndex(ci)

        self._label.setText(slot.label)
        self._unit.setText(slot.unit)
        self._min.setValue(slot.min_val)
        self._max.setValue(slot.max_val)
        has_d = slot.danger_from is not None
        self._danger_chk.setChecked(has_d)
        self._danger_val.setEnabled(has_d)
        self._danger_val.setValue(slot.danger_from if has_d else 80.0)

    # ── actions ──────────────────────────────────────────────────────── #

    def _apply(self):
        if self._idx < 0:
            return
        old = self._canvas._model.slots[self._idx]
        new = LayoutSlot(
            source_key  = self._src.currentData(),
            label       = self._label.text().strip(),
            unit        = self._unit.text().strip(),
            min_val     = self._min.value(),
            max_val     = self._max.value(),
            danger_from = self._danger_val.value() if self._danger_chk.isChecked() else None,
            row         = old.row,
            col         = old.col,
            row_span    = old.row_span,
            col_span    = old.col_span,
        )
        self._canvas.update_slot(self._idx, new)

    def _delete(self):
        if self._idx < 0:
            return
        self._canvas.remove_slot(self._idx)
        self._idx = -1

    def _add(self):
        dlg = _GaugePickerDialog(self)
        if dlg.exec() != QDialog.Accepted or dlg.chosen_key is None:
            return

        m        = self._canvas._model
        occupied = {(s.row, s.col) for s in m.slots}

        # Find first empty cell
        target = None
        for row in range(m.grid_rows):
            for col in range(m.grid_cols):
                if (row, col) not in occupied:
                    target = (row, col)
                    break
            if target:
                break

        if target is None:
            # Grid is full — offer to add a row
            ans = QMessageBox.question(
                self, "Grid Full",
                "All cells are occupied.  Add a row to make room?",
                QMessageBox.Yes | QMessageBox.Cancel,
            )
            if ans != QMessageBox.Yes:
                return
            m.grid_rows += 1
            self._rows.setValue(m.grid_rows)
            self._canvas.set_grid_size(m.grid_cols, m.grid_rows)
            target = (m.grid_rows - 1, 0)

        info = SOURCE_REGISTRY[dlg.chosen_key]
        self._canvas.add_slot(LayoutSlot(
            source_key  = dlg.chosen_key,
            label       = "",   # blank = use registry default
            unit        = "",
            min_val     = 0,
            max_val     = 100,
            danger_from = 80,
            row         = target[0],
            col         = target[1],
        ))

    def _resize_grid(self):
        self._canvas.set_grid_size(self._cols.value(), self._rows.value())

    def _save(self):
        self._canvas._model.save(_layout_path())

    def _load(self):
        p = _layout_path()
        if os.path.exists(p):
            self._canvas.load_model(LayoutModel.load(p))
            self._cols.setValue(self._canvas._model.grid_cols)
            self._rows.setValue(self._canvas._model.grid_rows)

    def _exit_edit(self):
        w = self.window()
        if hasattr(w, "set_edit_mode"):
            w.set_edit_mode(False)


# ============================================================
#  DesignerWindow
# ============================================================

class DesignerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instrument Panel")
        self.setStyleSheet("QMainWindow { background-color: #3C4323; }"
                           "QToolBar    { background-color: #2a2e1a; border: none; spacing: 6px; }"
                           "QToolButton { color: #c8bfa8; padding: 4px 10px; }")

        model         = _load_or_default()
        theme_info    = THEME_REGISTRY.get(model.theme_key, THEME_REGISTRY["wwii"])
        self._canvas  = LayoutCanvas(model, theme_info["factory"]())
        self._sidebar = EditSidebar(self._canvas)
        self._sidebar.sync_theme_combo(model.theme_key)
        self._sidebar.hide()
        self._edit_mode = False

        self._container = QWidget()
        hbox = QHBoxLayout(self._container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        hbox.addWidget(self._canvas, 1)
        hbox.addWidget(self._sidebar, 0)
        self.setCentralWidget(self._container)

        # Toolbar
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        self._edit_action = tb.addAction("✏  Edit Layout  [E]")
        self._edit_action.setCheckable(True)
        self._edit_action.triggered.connect(self.toggle_edit_mode)

        self.resize(960, 660)
        self.update_bg(theme_info)   # apply full chrome for the loaded theme

        # QShortcut works regardless of which child widget has focus
        QShortcut(QKeySequence("E"), self).activated.connect(self.toggle_edit_mode)
        QShortcut(QKeySequence(Qt.Key_Escape), self).activated.connect(
            lambda: self.set_edit_mode(False)
        )

    def update_bg(self, theme_info: dict):
        """Update all window chrome to match the active theme."""
        bg  = theme_info["bg"]
        tbg = theme_info["toolbar_bg"]
        tfg = theme_info["toolbar_fg"]
        self._canvas.setStyleSheet(f"background-color: {bg};")
        self._container.setStyleSheet(f"background-color: {bg};")
        self.setStyleSheet(
            f"QMainWindow  {{ background-color: {bg}; }}"
            f"QToolBar     {{ background-color: {tbg}; border: none; spacing: 6px; }}"
            f"QToolButton  {{ color: {tfg}; padding: 4px 10px; }}"
        )
        self._sidebar.setStyleSheet(theme_info["sidebar"])

    def toggle_edit_mode(self):
        self.set_edit_mode(not self._edit_mode)

    def set_edit_mode(self, enabled: bool):
        self._edit_mode = enabled
        self._canvas.set_edit_mode(enabled)
        self._sidebar.setVisible(enabled)
        self._edit_action.setChecked(enabled)
        if not enabled:
            # Auto-save layout on exit
            self._canvas._model.save(_layout_path())


# ============================================================
#  Helpers
# ============================================================

def _layout_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "layout.json")

def _hosts_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "hosts.json")


def _load_or_default() -> LayoutModel:
    p = _layout_path()
    if os.path.exists(p):
        try:
            return LayoutModel.load(p)
        except Exception:
            pass
    # Default 3×2 layout
    return LayoutModel(
        grid_cols=3, grid_rows=2, theme_key="wwii",
        slots=[
            LayoutSlot("cpu_total", row=0, col=0),
            LayoutSlot("ram",       row=0, col=1),
            LayoutSlot("disk_c",    row=0, col=2),
            LayoutSlot("net_in",    row=1, col=0),
            LayoutSlot("net_out",   row=1, col=1),
            LayoutSlot("core_0",    row=1, col=2),
        ],
    )


# ============================================================
#  Entry point
# ============================================================

if __name__ == "__main__":
    import atexit
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s  %(name)s  %(message)s")

    # Load remote hosts before building the window so their sources
    # appear in the gauge picker immediately.
    host_registry.load(_hosts_path(), SOURCE_REGISTRY)
    atexit.register(host_registry.stop_all)

    app = QApplication(sys.argv)
    win = DesignerWindow()
    win.show()
    sys.exit(app.exec())

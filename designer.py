"""
Instrument Panel — Interactive Layout Designer

  Live mode  : gauges animate with real psutil data.
  Edit mode  : press E (or toolbar button) to enter.

In edit mode:
  - Grid lines appear; gauges stay live.
  - Click a gauge to select it (amber border); its properties load in the sidebar.
  - Drag a gauge to a new grid cell (target cell highlights in green).
  - Edit source, label, unit, min/max/danger in the sidebar; click Apply.
  - Add Gauge   — places a new CPU-total gauge in the first empty cell.
  - Add Divider — inserts a labeled header bar above the selected row.
  - Delete      — removes the selected gauge or divider.
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
from PySide6.QtCore import Qt, QTimer, QRect, Signal
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
        "name":         "WWII Cockpit",
        "factory":      theme_wwii_cockpit,
        "bg":           "#3C4323",
        "toolbar_bg":   "#2a2e1a",
        "toolbar_fg":   "#c8bfa8",
        "div_bg":       "#4a5230",
        "div_stripe":   "#a09870",
        "div_text":     "#c8bfa8",
        "sidebar":      _sidebar_style(),
    },
    "f1": {
        "name":         "F1 Racing",
        "factory":      theme_f1_racing,
        "bg":           "#0E0E10",
        "toolbar_bg":   "#131316",
        "toolbar_fg":   "#d0d0d8",
        "div_bg":       "#16161c",
        "div_stripe":   "#ff5f10",
        "div_text":     "#d0d0d8",
        "sidebar":      _sidebar_style(bg="#0f0f14", input_bg="#1a1a22",
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
    label:       str   = ""           # empty = use registry default
    unit:        str   = ""           # empty = use registry default
    min_val:     float = 0.0
    max_val:     float = 100.0
    danger_from: Optional[float] = 80.0    # None = no danger arc
    row:         int   = 0
    col:         int   = 0
    row_span:    int   = 1
    col_span:    int   = 1
    slot_type:   str   = "gauge"      # "gauge" | "divider"


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
        slots = [LayoutSlot(**{k: v for k, v in s.items()
                               if k in LayoutSlot.__dataclass_fields__})
                 for s in d["slots"]]
        return cls(
            grid_cols = d["grid_cols"],
            grid_rows = d["grid_rows"],
            theme_key = d.get("theme_key", "wwii"),
            slots     = slots,
        )


# ============================================================
#  DividerWidget — full-width labeled group header bar
# ============================================================

_DIVIDER_H = 30   # pixel height of a divider row


class DividerWidget(QWidget):
    """
    Renders a themed horizontal bar that visually groups gauges below it.
    source_key stores the host key prefix for the live status dot (optional).
    label stores the display text.
    """

    def __init__(self, slot: LayoutSlot, theme_key: str = "wwii", parent=None):
        super().__init__(parent)
        self._label     = slot.label or "GROUP"
        self._host_key  = slot.source_key   # e.g. "wsl_ubuntu"; empty = no dot
        self._theme_key = theme_key
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_theme_key(self, key: str):
        self._theme_key = key
        self.update()

    def paintEvent(self, event):
        try:
            ti   = THEME_REGISTRY.get(self._theme_key, THEME_REGISTRY["wwii"])
            p    = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()

            # Background
            p.fillRect(0, 0, w, h, QColor(ti["div_bg"]))

            # Left accent stripe (4 px, vertically centered with 4px margin)
            p.fillRect(0, 3, 4, h - 6, QColor(ti["div_stripe"]))

            # Label
            font = QFont("Arial Narrow", 9, QFont.Bold)
            font.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
            p.setFont(font)
            p.setPen(QColor(ti["div_text"]))
            p.drawText(QRect(12, 0, w - 32, h),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       self._label.upper())

            # Status dot (if host_key is set)
            if self._host_key:
                status = host_registry.get_host_status(self._host_key)
                dot_color = {
                    "connected":    QColor(80,  200,  80),
                    "connecting":   QColor(200, 160,  40),
                    "error":        QColor(200,  60,  60),
                }.get(status, QColor(80, 80, 80))
                cx, cy = w - 14, h // 2
                p.setPen(Qt.NoPen)
                p.setBrush(dot_color)
                p.drawEllipse(cx - 5, cy - 5, 10, 10)

            p.end()
        except Exception:
            pass


# ============================================================
#  Edit overlay — transparent child on top of LayoutCanvas
# ============================================================

class _EditOverlay(QWidget):
    """
    Covers LayoutCanvas in edit mode.  Draws grid lines, selection border,
    resize handles, and drag/resize previews.  Captures all mouse events.
    """

    _HANDLE_R = 6    # handle square half-size (px)
    _HANDLE_HIT = 10 # hit-test radius (px)

    def __init__(self, canvas: "LayoutCanvas"):
        super().__init__(canvas)
        self._canvas = canvas
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._resize_handle  = None   # "e" | "s" | "se"
        self._resize_preview = None   # (row_span, col_span) while dragging

    # ── handle geometry helpers ──────────────────────────────────────── #

    def _handle_points(self, r: QRect) -> dict:
        """Centre points of the E, S, SE resize handles for rect r."""
        return {
            "e":  (r.right(),        r.center().y()),
            "s":  (r.center().x(),   r.bottom()),
            "se": (r.right(),        r.bottom()),
        }

    def _hit_handle(self, r: QRect, x: int, y: int):
        """Return handle name "e"/"s"/"se" if (x,y) is close to one, else None."""
        hr = self._HANDLE_HIT
        for name, (hx, hy) in self._handle_points(r).items():
            if abs(x - hx) <= hr and abs(y - hy) <= hr:
                return name
        return None

    # ── paint ────────────────────────────────────────────────────────── #

    def paintEvent(self, event):
        c       = self._canvas
        m       = c._model
        p       = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h    = self.width(), self.height()
        heights = c._row_heights()

        # ── grid lines ───────────────────────────────────────────────────
        p.setPen(QPen(QColor(120, 130, 80, 90), 1))
        cell_w = w / m.grid_cols
        for col in range(m.grid_cols + 1):
            x = int(col * cell_w)
            p.drawLine(x, 0, x, h)
        y_acc = 0
        for rh in heights:
            p.drawLine(0, int(y_acc), w, int(y_acc))
            y_acc += rh
        p.drawLine(0, int(y_acc), w, int(y_acc))

        # ── move drag target ─────────────────────────────────────────────
        if c._drag_cell is not None:
            row, col   = c._drag_cell
            drag_slot  = c._model.slots[c._drag_idx]
            r = c._cell_rect_for(row, col, drag_slot.row_span, drag_slot.col_span)
            occupied = any(
                i != c._drag_idx and s.row == row and s.col == col
                for i, s in enumerate(c._model.slots)
            )
            fill   = QColor(210, 160, 60,  55) if occupied else QColor(140, 180, 90,  55)
            border = QColor(210, 160, 60, 200) if occupied else QColor(140, 180, 90, 200)
            p.fillRect(r, fill)
            p.setPen(QPen(border, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1, 1, -1, -1))

        # ── selection border + resize handles ───────────────────────────
        if c._selected >= 0 and c._selected < len(m.slots):
            s = m.slots[c._selected]
            r = c._widget_rect(s)
            p.setPen(QPen(QColor(210, 175, 80, 230), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(2, 2, -2, -2))

            if s.slot_type == "gauge":
                # Resize ghost preview
                if self._resize_preview:
                    rs, cs = self._resize_preview
                    pr = c._cell_rect_for(s.row, s.col, rs, cs)
                    p.setPen(QPen(QColor(210, 175, 80, 140), 1, Qt.DashLine))
                    p.setBrush(QColor(210, 175, 80, 18))
                    p.drawRect(pr.adjusted(2, 2, -2, -2))

                # Resize handles
                hr = self._HANDLE_R
                p.setPen(QPen(QColor(210, 175, 80, 230), 1))
                p.setBrush(QColor(50, 46, 30))
                for hx, hy in self._handle_points(r).values():
                    p.drawRect(hx - hr, hy - hr, hr * 2, hr * 2)

        # ── watermark ───────────────────────────────────────────────────
        p.setFont(QFont("Arial Narrow", 9, QFont.Bold))
        p.setPen(QColor(160, 170, 110, 90))
        p.drawText(8, h - 8,
                   "EDIT MODE  —  E to exit  —  drag to move  —  drag corner/edge to resize")
        p.end()

    # ── mouse ────────────────────────────────────────────────────────── #

    def mousePressEvent(self, event):
        c    = self._canvas
        pos  = event.position()
        x, y = int(pos.x()), int(pos.y())

        # Check resize handles on currently selected gauge first
        if c._selected >= 0 and c._selected < len(c._model.slots):
            s = c._model.slots[c._selected]
            if s.slot_type == "gauge":
                handle = self._hit_handle(c._widget_rect(s), x, y)
                if handle:
                    self._resize_handle  = handle
                    self._resize_preview = (s.row_span, s.col_span)
                    return

        # Otherwise select + start move drag
        self._resize_handle  = None
        self._resize_preview = None
        idx = c._hit_slot(x, y)
        c.select_slot(idx)
        if idx >= 0 and c._model.slots[idx].slot_type == "gauge":
            c._drag_idx  = idx
            c._drag_cell = None

    def mouseMoveEvent(self, event):
        c    = self._canvas
        pos  = event.position()
        x, y = int(pos.x()), int(pos.y())

        # ── resize drag ──────────────────────────────────────────────────
        if self._resize_handle:
            m    = c._model
            s    = m.slots[c._selected]
            mr, mc = c._pos_to_cell(x, y)
            rs, cs = s.row_span, s.col_span
            if self._resize_handle in ("s", "se"):
                rs = max(1, min(m.grid_rows - s.row, mr - s.row + 1))
            if self._resize_handle in ("e", "se"):
                cs = max(1, min(m.grid_cols - s.col, mc - s.col + 1))
            self._resize_preview = (rs, cs)
            self.update()
            return

        # ── move drag ────────────────────────────────────────────────────
        if c._drag_idx < 0:
            return
        row, col = c._pos_to_cell(x, y)
        slot = c._model.slots[c._drag_idx]
        target_slot = next(
            (s for s in c._model.slots if s.row == row and s.col == col
             and s.slot_type == "divider"), None
        )
        if (row, col) != (slot.row, slot.col) and target_slot is None:
            c._drag_cell = (row, col)
        else:
            c._drag_cell = None
        self.update()

    def mouseReleaseEvent(self, event):
        c = self._canvas

        # ── commit resize ────────────────────────────────────────────────
        if self._resize_handle and self._resize_preview:
            rs, cs = self._resize_preview
            c.resize_slot(c._selected, rs, cs)
            self._resize_handle  = None
            self._resize_preview = None
            self.update()
            return

        # ── commit move ──────────────────────────────────────────────────
        if c._drag_idx >= 0 and c._drag_cell is not None:
            row, col = c._drag_cell
            c.move_slot(c._drag_idx, row, col)
        c._drag_idx  = -1
        c._drag_cell = None
        self.update()


# ============================================================
#  LayoutCanvas — the gauge grid
# ============================================================

_SPACING = 2   # pixels between gauges


class LayoutCanvas(QWidget):
    """
    Owns Gauge and DividerWidget children, positions them manually, drives
    poll + animation.  In edit mode, raises an overlay for drag interaction.
    """

    slot_selected = Signal(int)   # emits slot index (-1 = none selected)

    def __init__(self, model: LayoutModel, theme=None, theme_key: str = "wwii",
                 poll_ms: int = 1000, fps: int = 60, parent=None):
        super().__init__(parent)
        self._model     = model
        self._theme     = theme or theme_wwii_cockpit()
        self._theme_key = theme_key
        self._widgets: list[QWidget]   = []
        self._sources:  list[Optional[Callable]] = []
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

    # ── row height helpers ───────────────────────────────────────────── #

    def _row_heights(self) -> list:
        """
        Returns pixel heights for each row.
        Divider rows get _DIVIDER_H px; gauge rows share the remainder equally.
        """
        m = self._model
        divider_rows = {s.row for s in m.slots if s.slot_type == "divider"}
        n_div   = len(divider_rows)
        n_gauge = m.grid_rows - n_div
        total   = max(1, self.height())
        gauge_h = max(40.0, (total - n_div * _DIVIDER_H) / max(1, n_gauge))
        return [
            float(_DIVIDER_H) if r in divider_rows else gauge_h
            for r in range(m.grid_rows)
        ]

    def _row_y(self, row: int) -> int:
        return int(sum(self._row_heights()[:row]))

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

    def _make_source(self, slot: LayoutSlot) -> Optional[Callable]:
        if slot.slot_type == "divider":
            return None
        info = SOURCE_REGISTRY.get(slot.source_key)
        if info:
            return info["factory"]()
        return lambda: 0.0

    def _cell_rect_for(self, row: int, col: int,
                       row_span: int = 1, col_span: int = 1,
                       is_divider: bool = False) -> QRect:
        w      = self.width()
        m      = self._model
        cw     = w / m.grid_cols
        heights = self._row_heights()
        sv     = 2 if is_divider else _SPACING
        x      = int(col * cw) + _SPACING
        y      = int(sum(heights[:row])) + sv
        rw     = int(col_span * cw) - 2 * _SPACING
        rh     = int(sum(heights[row:row + row_span])) - 2 * sv
        return QRect(x, y, max(rw, 1), max(rh, 1))

    def _widget_rect(self, slot: LayoutSlot) -> QRect:
        """Return the QRect for a slot (handles gauge vs divider spacing)."""
        is_div = slot.slot_type == "divider"
        cs     = self._model.grid_cols if is_div else slot.col_span
        return self._cell_rect_for(slot.row, slot.col,
                                   slot.row_span, cs, is_divider=is_div)

    def _reposition(self):
        for slot, widget in zip(self._model.slots, self._widgets):
            widget.setGeometry(self._widget_rect(slot))

    def _rebuild(self):
        for w in self._widgets:
            w.deleteLater()
        self._widgets.clear()
        self._sources.clear()
        for slot in self._model.slots:
            w = self._make_widget(slot)
            w.show()
            self._widgets.append(w)
            self._sources.append(self._make_source(slot))
        self._reposition()

    def _make_widget(self, slot: LayoutSlot) -> QWidget:
        if slot.slot_type == "divider":
            return DividerWidget(slot, theme_key=self._theme_key, parent=self)
        return Gauge(config=self._make_config(slot), theme=self._theme, parent=self)

    def _poll(self):
        for src, widget in zip(self._sources, self._widgets):
            if src is None:
                continue
            try:
                widget.value = src()
            except Exception:
                pass

    def _repaint_all(self):
        for w in self._widgets:
            w.update()

    # ── resize ───────────────────────────────────────────────────────── #

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()
        self._overlay.setGeometry(0, 0, self.width(), self.height())

    # ── hit-testing & coordinate helpers ─────────────────────────────── #

    def _hit_slot(self, x: int, y: int) -> int:
        """Return index of widget whose geometry contains (x, y), or -1."""
        for i, widget in enumerate(self._widgets):
            if widget.geometry().contains(x, y):
                return i
        return -1

    def _pos_to_cell(self, x: int, y: int) -> tuple:
        """Convert pixel position to (row, col), clamped to grid."""
        m       = self._model
        cw      = self.width() / m.grid_cols
        heights = self._row_heights()
        col     = max(0, min(m.grid_cols - 1, int(x / cw)))
        # Find row by cumulative y
        y_acc = 0.0
        row   = m.grid_rows - 1
        for r, h in enumerate(heights):
            if y < y_acc + h:
                row = r
                break
            y_acc += h
        return row, col

    # ── public API (called from overlay / sidebar) ────────────────────── #

    def select_slot(self, idx: int):
        self._selected = idx
        self._overlay.update()
        self.slot_selected.emit(idx)

    def move_slot(self, idx: int, new_row: int, new_col: int):
        old_row = self._model.slots[idx].row
        old_col = self._model.slots[idx].col
        swap_idx = next(
            (i for i, s in enumerate(self._model.slots)
             if i != idx and s.row == new_row and s.col == new_col
             and s.slot_type == "gauge"),
            None
        )
        self._model.slots[idx].row = new_row
        self._model.slots[idx].col = new_col
        if swap_idx is not None:
            self._model.slots[swap_idx].row = old_row
            self._model.slots[swap_idx].col = old_col
        self._reposition()
        self._overlay.update()

    def update_slot(self, idx: int, slot: LayoutSlot):
        """Replace slot data and refresh widget + source."""
        old_key  = self._model.slots[idx].source_key
        old_type = self._model.slots[idx].slot_type
        self._model.slots[idx] = slot

        if slot.slot_type == "divider":
            # Replace widget if type changed, otherwise just update label/host
            if old_type != "divider":
                self._widgets[idx].deleteLater()
                w = DividerWidget(slot, theme_key=self._theme_key, parent=self)
                w.show()
                self._widgets[idx] = w
                self._sources[idx] = None
            else:
                dw = self._widgets[idx]
                dw._label    = slot.label or "GROUP"
                dw._host_key = slot.source_key
                dw._theme_key = self._theme_key
            self._reposition()
            if self._overlay.isVisible():
                self._overlay.raise_()
            return

        # Gauge path
        if old_type == "divider":
            # Was a divider, now a gauge — replace widget
            self._widgets[idx].deleteLater()
            g = Gauge(config=self._make_config(slot), theme=self._theme, parent=self)
            g.show()
            self._widgets[idx] = g
            self._sources[idx] = self._make_source(slot)
        else:
            g = self._widgets[idx]
            g.config = self._make_config(slot)
            if slot.source_key != old_key:
                g._value         = g.config.min_val
                g._display_value = g.config.min_val
                self._sources[idx] = self._make_source(slot)
        self._widgets[idx].update()
        self._reposition()
        if self._overlay.isVisible():
            self._overlay.raise_()

    def add_slot(self, slot: LayoutSlot):
        self._model.slots.append(slot)
        w = self._make_widget(slot)
        w.show()
        self._widgets.append(w)
        self._sources.append(self._make_source(slot))
        self._reposition()
        if self._overlay.isVisible():
            self._overlay.raise_()
        self.select_slot(len(self._model.slots) - 1)

    def add_divider(self, before_row: int, label: str, host_key: str = ""):
        """
        Insert a new divider row before `before_row`.
        Shifts all slots at row >= before_row down by 1, increments grid_rows.
        """
        for s in self._model.slots:
            if s.row >= before_row:
                s.row += 1
        self._model.grid_rows += 1

        div_slot = LayoutSlot(
            source_key = host_key,
            label      = label,
            row        = before_row,
            col        = 0,
            col_span   = self._model.grid_cols,
            slot_type  = "divider",
        )
        self._model.slots.append(div_slot)
        w = DividerWidget(div_slot, theme_key=self._theme_key, parent=self)
        w.show()
        self._widgets.append(w)
        self._sources.append(None)
        self._reposition()
        if self._overlay.isVisible():
            self._overlay.raise_()
        self.select_slot(len(self._model.slots) - 1)

    def remove_slot(self, idx: int):
        slot = self._model.slots[idx]
        removed_row = slot.row

        self._widgets[idx].deleteLater()
        del self._widgets[idx]
        del self._sources[idx]
        del self._model.slots[idx]

        # If we removed a divider, collapse that row
        if slot.slot_type == "divider":
            self._model.grid_rows = max(1, self._model.grid_rows - 1)
            for s in self._model.slots:
                if s.row > removed_row:
                    s.row -= 1

        self._selected = -1
        self._reposition()
        self._overlay.update()
        self.slot_selected.emit(-1)

    def resize_slot(self, idx: int, row_span: int, col_span: int):
        """Resize a gauge by changing its row/col span, then refresh."""
        self._model.slots[idx].row_span = row_span
        self._model.slots[idx].col_span = col_span
        self._reposition()
        self._overlay.update()
        self.slot_selected.emit(idx)   # refreshes sidebar spinboxes

    def set_grid_size(self, cols: int, rows: int):
        self._model.grid_cols = cols
        self._model.grid_rows = rows
        # Update divider col_spans to match new width
        for s in self._model.slots:
            if s.slot_type == "divider":
                s.col_span = cols
        self._reposition()
        self._overlay.update()

    def load_model(self, model: LayoutModel):
        self._model    = model
        self._selected = -1
        self._drag_idx = -1
        self._drag_cell = None
        self._rebuild()
        self._overlay.update()
        self.slot_selected.emit(-1)

    def set_theme(self, theme: GaugeTheme, key: str):
        """Replace theme on all gauges and update model's theme_key."""
        self._theme     = theme
        self._theme_key = key
        self._model.theme_key = key
        for i, (slot, old_w) in enumerate(zip(self._model.slots, self._widgets)):
            old_w.deleteLater()
            new_w = self._make_widget(slot)
            new_w.show()
            self._widgets[i] = new_w
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

        _header("── Local ──────────────────────")
        for key, info in SOURCE_REGISTRY.items():
            if info.get("group") is None:
                _entry(key, info)

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
        self._gauge_section = QWidget()
        gs = QVBoxLayout(self._gauge_section)
        gs.setContentsMargins(0, 0, 0, 0)
        gs.setSpacing(6)

        gs.addWidget(QLabel("SOURCE"))
        self._src = QComboBox()
        for key, info in SOURCE_REGISTRY.items():
            self._src.addItem(info["label"], key)
        gs.addWidget(self._src)

        gs.addWidget(QLabel("LABEL  (blank = source default)"))
        self._label = QLineEdit()
        self._label.setPlaceholderText("leave blank for default")
        gs.addWidget(self._label)

        gs.addWidget(QLabel("UNIT  (blank = source default)"))
        self._unit = QLineEdit()
        self._unit.setPlaceholderText("leave blank for default")
        gs.addWidget(self._unit)

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
        gs.addWidget(minmax)

        gs.addWidget(QLabel("SIZE  (rows × cols)"))
        size_w = QWidget()
        size_l = QHBoxLayout(size_w)
        size_l.setContentsMargins(0, 0, 0, 0); size_l.setSpacing(6)
        size_l.addWidget(QLabel("ROWS"))
        self._row_span = QSpinBox(); self._row_span.setRange(1, 10)
        size_l.addWidget(self._row_span)
        size_l.addWidget(QLabel("COLS"))
        self._col_span = QSpinBox(); self._col_span.setRange(1, 10)
        size_l.addWidget(self._col_span)
        gs.addWidget(size_w)

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
        gs.addWidget(danger)
        self._danger_chk.toggled.connect(self._danger_val.setEnabled)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._apply)
        gs.addWidget(self._apply_btn)

        vbox.addWidget(self._gauge_section)

        # ── Divider properties ────────────────────────────────────────
        self._div_section = QWidget()
        ds = QVBoxLayout(self._div_section)
        ds.setContentsMargins(0, 0, 0, 0)
        ds.setSpacing(6)

        ds.addWidget(QLabel("DIVIDER LABEL"))
        self._div_label = QLineEdit()
        self._div_label.setPlaceholderText("e.g.  EPIC PROD")
        ds.addWidget(self._div_label)

        ds.addWidget(QLabel("HOST KEY  (for status dot, or blank)"))
        self._div_host = QComboBox()
        self._div_host.addItem("— none —", "")
        ds.addWidget(self._div_host)

        self._div_apply_btn = QPushButton("Apply")
        self._div_apply_btn.clicked.connect(self._apply_divider)
        ds.addWidget(self._div_apply_btn)

        vbox.addWidget(self._div_section)

        # ── Delete (shared) ───────────────────────────────────────────
        self._del_btn = QPushButton("Delete")
        self._del_btn.clicked.connect(self._delete)
        self._del_btn.setStyleSheet(
            "QPushButton { color: #c06050; }"
            "QPushButton:hover { background: #3a2020; }"
        )
        vbox.addWidget(self._del_btn)

        vbox.addWidget(_sep())

        # ── Add gauge / divider ───────────────────────────────────────
        add_btn = QPushButton("Add Gauge")
        add_btn.clicked.connect(self._add_gauge)
        vbox.addWidget(add_btn)

        add_div_btn = QPushButton("Add Divider")
        add_div_btn.clicked.connect(self._add_divider)
        vbox.addWidget(add_div_btn)

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

        done_btn = QPushButton("▶   LIVE MODE")
        done_btn.setStyleSheet(
            "QPushButton { background: #1e2e14; color: #90c060;"
            "              border: 1px solid #507840; font-weight: bold; padding: 6px; }"
            "QPushButton:hover { background: #2e3e20; }"
        )
        done_btn.clicked.connect(self._exit_edit)
        vbox.addWidget(done_btn)

        self._set_selection_mode("none")

    def _set_selection_mode(self, mode: str):
        """mode: "none" | "gauge" | "divider" """
        self._gauge_section.setVisible(mode == "gauge")
        self._div_section.setVisible(mode == "divider")
        self._del_btn.setEnabled(mode in ("gauge", "divider"))

    def _refresh_div_host_combo(self):
        """Rebuild the host-key combo from currently active remote hosts."""
        self._div_host.clear()
        self._div_host.addItem("— none —", "")
        for key, info in SOURCE_REGISTRY.items():
            group = info.get("group")
            if group and key.endswith(":cpu"):
                # Extract host prefix from key like "wsl_ubuntu:cpu"
                host_prefix = key[:-4]   # strip ":cpu"
                self._div_host.addItem(group, host_prefix)

    # ── slot selection ─────────────────────────────────────────────── #

    def sync_theme_combo(self, key: str):
        ci = self._theme_combo.findData(key)
        if ci >= 0:
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
            self._set_selection_mode("none")
            return

        slot = self._canvas._model.slots[idx]

        if slot.slot_type == "divider":
            self._title.setText(f"DIVIDER {idx + 1}")
            self._set_selection_mode("divider")
            self._refresh_div_host_combo()
            self._div_label.setText(slot.label)
            ci = self._div_host.findData(slot.source_key)
            if ci >= 0:
                self._div_host.setCurrentIndex(ci)
            return

        self._title.setText(f"GAUGE {idx + 1}")
        self._set_selection_mode("gauge")

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
        self._row_span.setValue(slot.row_span)
        self._col_span.setValue(slot.col_span)

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
            row_span    = self._row_span.value(),
            col_span    = self._col_span.value(),
            slot_type   = "gauge",
        )
        self._canvas.update_slot(self._idx, new)

    def _apply_divider(self):
        if self._idx < 0:
            return
        old = self._canvas._model.slots[self._idx]
        new = LayoutSlot(
            source_key = self._div_host.currentData() or "",
            label      = self._div_label.text().strip(),
            row        = old.row,
            col        = 0,
            col_span   = self._canvas._model.grid_cols,
            slot_type  = "divider",
        )
        self._canvas.update_slot(self._idx, new)

    def _delete(self):
        if self._idx < 0:
            return
        self._canvas.remove_slot(self._idx)
        self._idx = -1

    def _add_gauge(self):
        dlg = _GaugePickerDialog(self)
        if dlg.exec() != QDialog.Accepted or dlg.chosen_key is None:
            return

        m        = self._canvas._model
        # Gauge cells only (not divider rows)
        gauge_rows = {s.row for s in m.slots if s.slot_type == "divider"}
        occupied   = {(s.row, s.col) for s in m.slots if s.slot_type == "gauge"}

        target = None
        for row in range(m.grid_rows):
            if row in gauge_rows:
                continue
            for col in range(m.grid_cols):
                if (row, col) not in occupied:
                    target = (row, col)
                    break
            if target:
                break

        if target is None:
            ans = QMessageBox.question(
                self, "Grid Full",
                "All gauge cells are occupied.  Add a row to make room?",
                QMessageBox.Yes | QMessageBox.Cancel,
            )
            if ans != QMessageBox.Yes:
                return
            m.grid_rows += 1
            self._rows.setValue(m.grid_rows)
            self._canvas.set_grid_size(m.grid_cols, m.grid_rows)
            target = (m.grid_rows - 1, 0)

        self._canvas.add_slot(LayoutSlot(
            source_key  = dlg.chosen_key,
            label       = "",
            unit        = "",
            min_val     = 0,
            max_val     = 100,
            danger_from = 80,
            row         = target[0],
            col         = target[1],
            slot_type   = "gauge",
        ))

    def _add_divider(self):
        # Determine insertion row: above selected gauge's row, or top of grid
        m = self._canvas._model
        if self._idx >= 0:
            before_row = m.slots[self._idx].row
        else:
            before_row = 0

        # Ask for a label
        label, ok = _simple_input(
            self, "Add Divider",
            "Divider label (e.g. EPIC PROD):",
            placeholder="GROUP LABEL",
        )
        if not ok:
            return
        label = label.strip() or "GROUP"

        # Ask which host to link (for status dot)
        self._refresh_div_host_combo()
        host_key = ""   # default: no dot; user can edit in sidebar after add

        self._canvas.add_divider(before_row, label, host_key)
        self._rows.setValue(m.grid_rows)

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
#  Simple single-field input dialog
# ============================================================

def _simple_input(parent, title: str, prompt: str,
                  default: str = "", placeholder: str = "") -> tuple:
    """Returns (text, ok)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setStyleSheet(_sidebar_style())
    dlg.setMinimumWidth(280)
    vbox = QVBoxLayout(dlg)
    vbox.addWidget(QLabel(prompt))
    edit = QLineEdit(default)
    edit.setPlaceholderText(placeholder)
    vbox.addWidget(edit)
    row = QHBoxLayout()
    ok_btn = QPushButton("OK")
    ok_btn.clicked.connect(dlg.accept)
    cancel_btn = QPushButton("Cancel")
    cancel_btn.clicked.connect(dlg.reject)
    row.addWidget(ok_btn)
    row.addWidget(cancel_btn)
    vbox.addLayout(row)
    ok = dlg.exec() == QDialog.Accepted
    return edit.text(), ok


# ============================================================
#  Panel container — canvas fills full area, sidebar floats on top
# ============================================================

class _PanelContainer(QWidget):
    """Canvas always fills the full container; sidebar overlays the right edge."""

    _SIDEBAR_W = 230

    def __init__(self):
        super().__init__()
        self._canvas  = None
        self._sidebar = None

    def setup(self, canvas: "LayoutCanvas", sidebar: "EditSidebar"):
        self._canvas  = canvas
        self._sidebar = sidebar
        canvas.setParent(self)
        sidebar.setParent(self)
        canvas.show()
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        if not self._canvas:
            return
        w, h = self.width(), self.height()
        self._canvas.setGeometry(0, 0, w, h)
        self._sidebar.setGeometry(w - self._SIDEBAR_W, 0, self._SIDEBAR_W, h)


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
        self._canvas  = LayoutCanvas(model, theme_info["factory"](),
                                     theme_key=model.theme_key)
        self._sidebar = EditSidebar(self._canvas)
        self._sidebar.sync_theme_combo(model.theme_key)
        self._sidebar.hide()
        self._edit_mode = False

        self._container = _PanelContainer()
        self._container.setup(self._canvas, self._sidebar)
        self.setCentralWidget(self._container)

        tb = self.addToolBar("Main")
        tb.setMovable(False)
        self._edit_action = tb.addAction("✏  Edit Layout  [E]")
        self._edit_action.setCheckable(True)
        self._edit_action.triggered.connect(self.toggle_edit_mode)

        self.resize(960, 660)
        self.update_bg(theme_info)

        QShortcut(QKeySequence("E"), self).activated.connect(self.toggle_edit_mode)
        QShortcut(QKeySequence(Qt.Key_Escape), self).activated.connect(
            lambda: self.set_edit_mode(False)
        )

    def update_bg(self, theme_info: dict):
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

    host_registry.load(_hosts_path(), SOURCE_REGISTRY)
    atexit.register(host_registry.stop_all)

    app = QApplication(sys.argv)
    win = DesignerWindow()
    win.show()
    sys.exit(app.exec())

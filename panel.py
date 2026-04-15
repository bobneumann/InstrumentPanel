"""
Instrument Panel — InstrumentPanel widget with live psutil data.

GaugeSlot  — binds a Gauge (config + theme) to a data source callable and a
             grid position.  This is the user's unit of configuration.

InstrumentPanel — QWidget that owns a grid of Gauges, a fast animation
                  timer (60 fps), and a slower data-poll timer (default 1 s).
                  Data refresh rate ≠ display refresh rate: the needle
                  animation lerps smoothly between polls so it feels live.

Usage:
    from panel import InstrumentPanel, GaugeSlot
    from gauge import GaugeConfig, theme_wwii_cockpit
    from datasources import cpu_total, ram_percent

    slots = [
        GaugeSlot(GaugeConfig(label="CPU", unit="%"), theme_wwii_cockpit(),
                  source=cpu_total(), row=0, col=0),
        GaugeSlot(GaugeConfig(label="RAM", unit="%"), theme_wwii_cockpit(),
                  source=ram_percent(), row=0, col=1),
    ]
    panel = InstrumentPanel(slots)
"""

import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout
)
from PySide6.QtCore import QTimer

from gauge import Gauge, GaugeConfig, GaugeTheme, theme_wwii_cockpit
from datasources import (
    cpu_total, cpu_core, ram_percent, disk_percent,
    net_bytes_recv_rate, net_bytes_sent_rate,
)


# ============================================================
#  GaugeSlot — one instrument in the panel
# ============================================================

@dataclass
class GaugeSlot:
    """
    Binds a Gauge (config + theme) to a data source and a grid position.

    source   — callable() -> float, called once per poll cycle
    row/col  — top-left cell in the grid (0-indexed)
    row_span / col_span — how many cells this gauge occupies (default 1×1)
    """
    config:    GaugeConfig
    theme:     GaugeTheme
    source:    Callable[[], float]
    row:       int = 0
    col:       int = 0
    row_span:  int = 1
    col_span:  int = 1


# ============================================================
#  InstrumentPanel widget
# ============================================================

class InstrumentPanel(QWidget):
    """
    A grid of live gauges.  One QTimer drives 60-fps animation repaints;
    a separate slower QTimer drives data polling.

    Parameters
    ----------
    slots      : list of GaugeSlot describing each instrument
    poll_ms    : data poll interval in milliseconds (default 1000)
    fps        : animation repaint rate (default 60)
    spacing    : pixels between gauges in the grid (default 8)
    margins    : (left, top, right, bottom) outer margins in pixels
    """

    def __init__(
        self,
        slots: list,
        poll_ms: int = 1000,
        fps: int = 60,
        spacing: int = 8,
        margins: tuple = (12, 12, 12, 12),
        parent=None,
    ):
        super().__init__(parent)

        self._slots  = slots
        self._gauges = []          # parallel list to slots

        # Build grid
        grid = QGridLayout(self)
        grid.setSpacing(spacing)
        grid.setContentsMargins(*margins)

        for slot in slots:
            g = Gauge(config=slot.config, theme=slot.theme, parent=self)
            grid.addWidget(g, slot.row, slot.col, slot.row_span, slot.col_span)
            self._gauges.append(g)

        # Seed each gauge with an initial reading before the first poll fires
        self._poll()

        # Animation timer — triggers repaints so the needle lerp runs at fps
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._repaint_all)
        self._anim_timer.start(max(1, 1000 // fps))

        # Poll timer — reads data sources, updates target values
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(poll_ms)

    # ------------------------------------------------------------------ #

    def _poll(self):
        """Read all data sources and push new target values to gauges."""
        for slot, gauge in zip(self._slots, self._gauges):
            try:
                v = slot.source()
            except Exception:
                v = gauge.config.min_val
            gauge.value = v

    def _repaint_all(self):
        """Trigger a repaint on every gauge so needle animation advances."""
        for g in self._gauges:
            g.update()


# ============================================================
#  Demo — live local machine metrics
# ============================================================

def _make_slots() -> list:
    """Build a 3×2 grid of live system-metrics gauges."""
    t = theme_wwii_cockpit()

    cpu_cfg  = GaugeConfig(label="CPU",     unit="PERCENT",     min_val=0, max_val=100, danger_from=85)
    ram_cfg  = GaugeConfig(label="MEMORY",  unit="PERCENT",     min_val=0, max_val=100, danger_from=85)
    disk_cfg = GaugeConfig(label="DISK C:", unit="PERCENT",     min_val=0, max_val=100, danger_from=90)
    nin_cfg  = GaugeConfig(label="NET IN",  unit="MB / SEC",    min_val=0, max_val=100, danger_from=80)
    nout_cfg = GaugeConfig(label="NET OUT", unit="MB / SEC",    min_val=0, max_val=100, danger_from=80)
    core_cfg = GaugeConfig(label="CORE 0",  unit="PERCENT",     min_val=0, max_val=100, danger_from=85)

    return [
        GaugeSlot(cpu_cfg,  t, cpu_total(),             row=0, col=0),
        GaugeSlot(ram_cfg,  t, ram_percent(),            row=0, col=1),
        GaugeSlot(disk_cfg, t, disk_percent("C:\\"),     row=0, col=2),
        GaugeSlot(nin_cfg,  t, net_bytes_recv_rate(),    row=1, col=0),
        GaugeSlot(nout_cfg, t, net_bytes_sent_rate(),    row=1, col=1),
        GaugeSlot(core_cfg, t, cpu_core(0),              row=1, col=2),
    ]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instrument Panel — Live System Metrics")
        self.setStyleSheet("background-color: #3C4323;")

        panel = InstrumentPanel(_make_slots(), poll_ms=1000, fps=60)
        panel.setStyleSheet("background-color: #3C4323;")
        self.setCentralWidget(panel)

        # Size: 3 gauges × ~290px + margins/spacing
        self.resize(920, 640)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

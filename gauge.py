"""
Instrument Panel — Gauge Renderer
Architecture:
  GaugeConfig  — data contract (range, label, source). Theme-agnostic.
  GaugeTheme   — visual contract (colors, fonts, bezel, ring, needle). Swappable.
  Gauge        — QWidget that takes both and renders.
"""

import sys
import math
import random
from dataclasses import dataclass, field
from typing import Optional
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QRadialGradient,
    QLinearGradient, QFont, QPainterPath, QConicalGradient
)


# ============================================================
#  GaugeConfig — purely about data and range
# ============================================================

@dataclass
class GaugeConfig:
    label:       str   = "PRESSURE"
    unit:        str   = ""
    min_val:     float = 0.0
    max_val:     float = 100.0
    danger_from: Optional[float] = 80.0   # None = no danger arc
    start_angle: float = 225.0    # degrees from top, clockwise
    sweep:       float = 270.0    # total arc sweep in degrees


# ============================================================
#  GaugeTheme — purely about visuals
# ============================================================

@dataclass
class GaugeTheme:
    name: str = "default"

    # Panel behind the bezel
    panel_color_top:    QColor = field(default_factory=lambda: QColor(60, 67, 35))
    panel_color_mid:    QColor = field(default_factory=lambda: QColor(60, 67, 35))
    panel_color_bot:    QColor = field(default_factory=lambda: QColor(60, 67, 35))
    panel_texture:       bool  = True   # stipple noise dots
    panel_texture_style: str  = "stipple"  # "stipple" | "carbon"

    # Bezel (square housing)
    bezel_color_top:    QColor = field(default_factory=lambda: QColor(18, 18, 18))
    bezel_color_mid:    QColor = field(default_factory=lambda: QColor(8,  8,  8))
    bezel_color_bot:    QColor = field(default_factory=lambda: QColor(4,  4,  4))
    bezel_rim_color:    QColor = field(default_factory=lambda: QColor(40, 40, 38))
    bezel_corner_radius: float = 22.0
    bezel_margin:       float  = 20.0

    # Corner screws
    show_screws:        bool   = True
    screw_offset:       float  = 38.0   # from bezel edge to screw center
    screw_r_dimple:     float  = 28.0
    screw_r_head:       float  = 19.0
    screw_slot_angle:   float  = 20.0   # degrees
    screw_body_hi:      QColor = field(default_factory=lambda: QColor(80, 75, 66))
    screw_body_mid:     QColor = field(default_factory=lambda: QColor(52, 49, 44))
    screw_body_lo:      QColor = field(default_factory=lambda: QColor(25, 23, 20))
    screw_slot_color:   QColor = field(default_factory=lambda: QColor(10,  9,  8))
    screw_dimple_color: QColor = field(default_factory=lambda: QColor(30, 28, 25))

    # Inner ring (between bezel and face)
    ring_r_outer:       float  = 152.0
    ring_r_inner:       float  = 144.0
    ring_color_hi:      QColor = field(default_factory=lambda: QColor(65, 62, 55))   # dark oxidized aluminum
    ring_color_mid_lo:  QColor = field(default_factory=lambda: QColor(38, 36, 32))
    ring_color_mid_hi:  QColor = field(default_factory=lambda: QColor(72, 68, 60))
    ring_color_lo:      QColor = field(default_factory=lambda: QColor(22, 20, 18))

    # Gauge face
    face_color_center:  QColor = field(default_factory=lambda: QColor(38, 33, 28))
    face_color_mid:     QColor = field(default_factory=lambda: QColor(22, 19, 16))
    face_color_edge:    QColor = field(default_factory=lambda: QColor(10,  8,  6))

    # Markings
    primary_mark_color:   QColor = field(default_factory=lambda: QColor(220, 210, 185))  # cream
    secondary_mark_color: QColor = field(default_factory=lambda: QColor(180,  40,  30))  # red
    minor_mark_color:     QColor = field(default_factory=lambda: QColor(160, 152, 135))

    # Needle
    needle_color_hi:    QColor = field(default_factory=lambda: QColor(240, 230, 205))
    needle_color_mid:   QColor = field(default_factory=lambda: QColor(220, 210, 185))
    needle_color_lo:    QColor = field(default_factory=lambda: QColor(180, 170, 148))
    needle_tip_r:       float  = 118.0
    needle_base_r:      float  = 22.0
    needle_base_w:      float  = 5.5

    # Fonts
    font_numbers:       str    = "Arial Narrow"
    font_numbers_size:  int    = 13
    font_label:         str    = "Arial Narrow"
    font_label_size:    int    = 10
    font_unit:          str    = "Arial Narrow"
    font_unit_size:     int    = 8

    # Per-gauge face tint variation (authenticity — different manufacturers)
    face_variation:        bool = True   # each gauge instance gets unique face tint
    face_variation_amount: int  = 14     # max RGB channel shift

    # Per-gauge bezel tint variation (different suppliers, same spec, different black)
    bezel_variation:        bool = True  # warm brownish ↔ cool bluish black
    bezel_variation_amount: int  = 10    # max RGB channel shift


# ============================================================
#  Built-in themes
# ============================================================

def theme_wwii_cockpit() -> GaugeTheme:
    """WWII aircraft cockpit — olive drab panel, flat black bezel,
    dark oxidized aluminum ring, cream markings, ivory needle."""
    return GaugeTheme(name="wwii_cockpit")   # all defaults ARE the WWII theme


def theme_f1_racing() -> GaugeTheme:
    """F1 / motorsport — near-black carbon-fibre panel, bright chrome ring,
    white numbers, vivid orange danger arc and needle."""
    return GaugeTheme(
        name = "f1_racing",

        # Panel — near-black carbon
        panel_color_top    = QColor(14, 14, 16),
        panel_color_mid    = QColor(14, 14, 16),
        panel_color_bot    = QColor(14, 14, 16),
        panel_texture      = True,
        panel_texture_style= "carbon",

        # Bezel — sharper corners, slightly cool black
        bezel_color_top    = QColor(32, 32, 38),
        bezel_color_mid    = QColor(22, 22, 28),
        bezel_color_bot    = QColor(10, 10, 14),
        bezel_rim_color    = QColor(58, 58, 66),
        bezel_corner_radius= 8.0,
        bezel_margin       = 18.0,

        # Screws — titanium/chrome finish
        screw_body_hi      = QColor(205, 205, 215),
        screw_body_mid     = QColor(130, 130, 142),
        screw_body_lo      = QColor( 58,  58,  68),
        screw_slot_color   = QColor( 18,  18,  22),
        screw_dimple_color = QColor( 38,  38,  46),

        # Ring — bright chrome (high contrast against dark face)
        ring_color_hi      = QColor(205, 210, 220),
        ring_color_mid_lo  = QColor( 95,  98, 108),
        ring_color_mid_hi  = QColor(195, 200, 210),
        ring_color_lo      = QColor( 45,  46,  54),

        # Face — near-black, very slight warmth
        face_color_center  = QColor(24, 24, 28),
        face_color_mid     = QColor(15, 15, 19),
        face_color_edge    = QColor( 6,  6,  10),

        # Markings — clean white primary, vivid orange danger
        primary_mark_color   = QColor(240, 240, 248),
        secondary_mark_color = QColor(255,  95,  15),
        minor_mark_color     = QColor(155, 155, 168),

        # Needle — vivid orange
        needle_color_hi    = QColor(255, 125,  25),
        needle_color_mid   = QColor(235,  95,  15),
        needle_color_lo    = QColor(185,  68,  10),

        # Fonts — slightly larger for legibility on dark face
        font_numbers_size  = 14,

        # Precision manufacturing — no supplier variation
        face_variation     = False,
        bezel_variation    = False,
    )


# ============================================================
#  Gauge widget
# ============================================================

class Gauge(QWidget):
    """
    Universal gauge widget. Pass a GaugeConfig for data contract
    and a GaugeTheme for visual appearance.
    """

    def __init__(self, config: GaugeConfig = None, theme: GaugeTheme = None,
                 parent=None):
        super().__init__(parent)
        self.config = config or GaugeConfig()
        self.theme  = theme  or theme_wwii_cockpit()
        self._value = (self.config.min_val + self.config.max_val) / 2
        self._display_value = self._value
        self.setMinimumSize(40, 40)

        # Per-instance face tint — fixed at construction, unique per gauge
        if self.theme.face_variation:
            amt = self.theme.face_variation_amount
            rng = random.Random()
            self._face_tint = (
                rng.randint(-amt // 2, amt),
                rng.randint(-amt // 3, amt // 3),
                rng.randint(-amt, amt // 3),
            )
        else:
            self._face_tint = (0, 0, 0)

        # Per-instance bezel tint — warm brownish ↔ cool bluish black
        if self.theme.bezel_variation:
            amt = self.theme.bezel_variation_amount
            rng = random.Random()
            warm_or_cool = rng.choice([-1, 1])   # -1=cool, +1=warm
            shift = rng.randint(3, amt)
            self._bezel_tint = (
                warm_or_cool * shift,              # red up=warm, down=cool
                rng.randint(-2, 2),                # green barely moves
                -warm_or_cool * shift,             # blue inverse of red
            )
        else:
            self._bezel_tint = (0, 0, 0)

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = max(self.config.min_val, min(self.config.max_val, v))

    def _val_to_qt_angle(self, v):
        """Map value → Qt painter angle (0=3oclock, CCW positive)."""
        cfg = self.config
        frac = (v - cfg.min_val) / (cfg.max_val - cfg.min_val)
        deg = cfg.start_angle + frac * cfg.sweep
        return -(deg - 90)

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        size = min(w, h)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate((w - size) / 2, (h - size) / 2)
        p.scale(size / 400.0, size / 400.0)
        try:
            self._draw_panel(p)
            self._draw_bezel(p)
            if self.theme.show_screws:
                self._draw_screws(p)
            self._draw_ring(p)
            self._draw_face(p)
            self._draw_markings(p)
            self._draw_needle(p)
            self._draw_center_hub(p)
        except Exception as e:
            import traceback
            traceback.print_exc()
        finally:
            p.end()

    # ------------------------------------------------------------------ #

    def _draw_panel(self, p):
        t = self.theme
        p.fillRect(0, 0, 400, 400, t.panel_color_mid)
        if not t.panel_texture:
            return
        if t.panel_texture_style == "carbon":
            self._draw_carbon_texture(p)
        else:
            p.setPen(QPen(QColor(0, 0, 0, 18), 1))
            rng = random.Random(42)
            for _ in range(600):
                p.drawPoint(rng.randint(0, 399), rng.randint(0, 399))

    def _draw_carbon_texture(self, p):
        """Woven carbon-fibre pattern — used by F1/modern themes."""
        cs = 6   # cell size in 400-unit coordinate space
        p.setPen(Qt.NoPen)
        for row in range(0, 400, cs):
            for col in range(0, 400, cs):
                phase = ((row // cs) + (col // cs)) % 2
                # Alternate which half-cell is darkened to create weave
                x = col if phase == 0 else col + cs // 2
                p.fillRect(x, row, cs // 2, cs, QColor(0, 0, 0, 38))
        # Hairline grid — fibre-bundle separators
        p.setPen(QPen(QColor(255, 255, 255, 7), 1))
        for v in range(0, 401, cs):
            p.drawLine(0, v, 400, v)
        for v in range(0, 401, cs // 2):
            p.drawLine(v, 0, v, 400)

    def _draw_bezel(self, p):
        t = self.theme
        m, r = t.bezel_margin, t.bezel_corner_radius
        rect = QRectF(m, m, 400 - 2*m, 400 - 2*m)

        # Apply per-instance bezel tint (warm brownish ↔ cool bluish black)
        def _bt(c: QColor) -> QColor:
            tr, tg, tb = self._bezel_tint
            return QColor(max(0,min(255,c.red()+tr)),
                          max(0,min(255,c.green()+tg)),
                          max(0,min(255,c.blue()+tb)))

        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        grad = QLinearGradient(m, m, m, 400 - m)
        grad.setColorAt(0.0, _bt(t.bezel_color_top))
        grad.setColorAt(0.4, _bt(t.bezel_color_mid))
        grad.setColorAt(1.0, _bt(t.bezel_color_bot))
        p.fillPath(path, grad)

        # Raised-bezel illusion via edge treatment — entirely within bezel bounds,
        # nothing painted on the panel.
        # Top-left: slight highlight (light catches the raised edge)
        p.setPen(QPen(QColor(52, 50, 46, 160), 1.5))
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), r - 1, r - 1)
        # Bottom-right inner shadow (receding edge)
        p.setPen(QPen(QColor(0, 0, 0, 100), 1.0))
        p.drawRoundedRect(rect.adjusted(3, 3, -1, -1), r - 2, r - 2)

    def _draw_screws(self, p):
        t = self.theme
        m, off = t.bezel_margin, t.screw_offset
        positions = [
            (m + off,       m + off),
            (400 - m - off, m + off),
            (m + off,       400 - m - off),
            (400 - m - off, 400 - m - off),
        ]
        # Fixed-seed random slot angles — same every frame, looks hand-tightened
        rng = random.Random(7)
        for cx, cy in positions:
            slot_angle = rng.uniform(0, 180)
            self._draw_single_screw(p, cx, cy, slot_angle)

    def _draw_single_screw(self, p, cx, cy, slot_angle_deg=20.0):
        t = self.theme
        rd, rh = t.screw_r_dimple, t.screw_r_head

        dimple_grad = QRadialGradient(cx - 4, cy - 4, rd * 1.4)
        dimple_grad.setColorAt(0.0, QColor(8, 8, 8))
        dimple_grad.setColorAt(0.6, QColor(18, 18, 18))
        dimple_grad.setColorAt(1.0, t.screw_dimple_color)
        p.setPen(Qt.NoPen)
        p.setBrush(dimple_grad)
        p.drawEllipse(QPointF(cx, cy), rd, rd)

        head_grad = QRadialGradient(cx - 4, cy - 4, rh * 1.6)
        head_grad.setColorAt(0.0, t.screw_body_hi)
        head_grad.setColorAt(0.4, t.screw_body_mid)
        head_grad.setColorAt(1.0, t.screw_body_lo)
        p.setBrush(head_grad)
        p.drawEllipse(QPointF(cx, cy), rh, rh)

        slot_angle = math.radians(slot_angle_deg)
        slot_len = rh * 0.80
        dx = math.cos(slot_angle) * slot_len
        dy = math.sin(slot_angle) * slot_len
        pen = QPen(t.screw_slot_color, rh * 0.12)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx - dx, cy - dy), QPointF(cx + dx, cy + dy))

        p.setPen(QPen(QColor(110, 105, 92, 140), 1.0))
        p.drawArc(QRectF(cx - rh + 1, cy - rh + 1, (rh-1)*2, (rh-1)*2),
                  100 * 16, 80 * 16)

    def _draw_ring(self, p):
        t = self.theme
        cx, cy = 200, 200
        ro, ri = t.ring_r_outer, t.ring_r_inner

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 55))
        p.drawEllipse(QPointF(cx + 1, cy + 2), ro + 1, ro + 1)

        ring_grad = QConicalGradient(cx, cy, -30)
        ring_grad.setColorAt(0.00, t.ring_color_hi)
        ring_grad.setColorAt(0.20, t.ring_color_mid_lo)
        ring_grad.setColorAt(0.40, t.ring_color_mid_hi)
        ring_grad.setColorAt(0.60, t.ring_color_lo)
        ring_grad.setColorAt(0.80, t.ring_color_mid_hi)
        ring_grad.setColorAt(1.00, t.ring_color_hi)

        outer = QPainterPath()
        outer.addEllipse(QPointF(cx, cy), ro, ro)
        inner = QPainterPath()
        inner.addEllipse(QPointF(cx, cy), ri, ri)
        p.fillPath(outer.subtracted(inner), ring_grad)

    def _tint(self, color: QColor) -> QColor:
        """Apply per-instance face tint offset to a color."""
        tr, tg, tb = self._face_tint
        return QColor(
            max(0, min(255, color.red()   + tr)),
            max(0, min(255, color.green() + tg)),
            max(0, min(255, color.blue()  + tb)),
        )

    def _draw_face(self, p):
        t = self.theme
        cx, cy, r = 200, 200, 143
        grad = QRadialGradient(cx, cy - 20, 10, cx, cy, r)
        grad.setColorAt(0.0, self._tint(t.face_color_center))
        grad.setColorAt(0.6, self._tint(t.face_color_mid))
        grad.setColorAt(1.0, self._tint(t.face_color_edge))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_markings(self, p):
        t = self.theme
        cfg = self.config
        cx, cy = 200, 200
        r_face = 143

        # danger arc
        if cfg.danger_from is not None:
            a_start = self._val_to_qt_angle(cfg.danger_from)
            a_end   = self._val_to_qt_angle(cfg.max_val)
            span    = a_end - a_start
            arc_r   = r_face - 10
            arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r*2, arc_r*2)
            pen = QPen(t.secondary_mark_color, 6)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(arc_rect, int(a_start * 16), int(span * 16))

        # ticks
        num_major  = 10
        num_minor  = 5
        total      = num_major * num_minor

        for i in range(total + 1):
            frac      = i / total
            val       = cfg.min_val + frac * (cfg.max_val - cfg.min_val)
            angle_deg = cfg.start_angle + frac * cfg.sweep
            angle_rad = math.radians(angle_deg)
            sa, ca    = math.sin(angle_rad), math.cos(angle_rad)

            in_danger = (cfg.danger_from is not None and val >= cfg.danger_from)
            is_major  = (i % num_minor == 0)

            if is_major:
                r_out, r_in = r_face - 8, r_face - 22
                color = t.secondary_mark_color if in_danger else t.primary_mark_color
                pen = QPen(color, 2.0)
            else:
                r_out, r_in = r_face - 10, r_face - 18
                color = t.secondary_mark_color if in_danger else t.minor_mark_color
                pen = QPen(color, 1.0)

            p.setPen(pen)
            p.drawLine(QPointF(cx + r_out*sa, cy - r_out*ca),
                       QPointF(cx + r_in *sa, cy - r_in *ca))

        # numbers
        p.setFont(QFont(t.font_numbers, t.font_numbers_size, QFont.Bold))
        r_num = r_face - 34
        for i in range(num_major + 1):
            frac      = i / num_major
            val       = cfg.min_val + frac * (cfg.max_val - cfg.min_val)
            angle_deg = cfg.start_angle + frac * cfg.sweep
            angle_rad = math.radians(angle_deg)
            sa, ca    = math.sin(angle_rad), math.cos(angle_rad)
            in_danger = (cfg.danger_from is not None and val >= cfg.danger_from)
            txt       = str(int(round(val)))
            p.setPen(t.secondary_mark_color if in_danger else t.primary_mark_color)
            fm = p.fontMetrics()
            x  = cx + r_num*sa - fm.horizontalAdvance(txt)/2
            y  = cy - r_num*ca + fm.height()/3
            p.drawText(QPointF(x, y), txt)

        # label
        p.setFont(QFont(t.font_label, t.font_label_size, QFont.Bold))
        p.setPen(t.primary_mark_color)
        fm = p.fontMetrics()
        lw = fm.horizontalAdvance(cfg.label)
        p.drawText(QPointF(cx - lw/2, cy - 28), cfg.label)

        # unit
        p.setFont(QFont(t.font_unit, t.font_unit_size))
        p.setPen(t.minor_mark_color)
        fm = p.fontMetrics()
        uw = fm.horizontalAdvance(cfg.unit)
        p.drawText(QPointF(cx - uw/2, cy - 14), cfg.unit)

    def _draw_needle(self, p):
        t = self.theme
        cx, cy = 200, 200
        self._display_value += (self._value - self._display_value) * 0.15
        frac      = (self._display_value - self.config.min_val) / (self.config.max_val - self.config.min_val)
        angle_deg = self.config.start_angle + frac * self.config.sweep
        angle_rad = math.radians(angle_deg)
        sa, ca    = math.sin(angle_rad), math.cos(angle_rad)

        tr, br, bw = t.needle_tip_r, t.needle_base_r, t.needle_base_w
        perp_s, perp_c = math.cos(angle_rad), -math.sin(angle_rad)

        tip = QPointF(cx + tr*sa, cy - tr*ca)
        bl  = QPointF(cx - br*sa + bw*perp_s, cy + br*ca - bw*perp_c)
        br_ = QPointF(cx - br*sa - bw*perp_s, cy + br*ca + bw*perp_c)

        path = QPainterPath()
        path.moveTo(tip)
        path.lineTo(bl)
        path.lineTo(br_)
        path.closeSubpath()

        grad = QLinearGradient(bl, br_)
        grad.setColorAt(0.0, t.needle_color_hi)
        grad.setColorAt(0.5, t.needle_color_mid)
        grad.setColorAt(1.0, t.needle_color_lo)
        p.setPen(QPen(QColor(60, 55, 45), 0.5))
        p.setBrush(grad)
        p.drawPath(path)

    def _draw_center_hub(self, p):
        cx, cy, r = 200, 200, 7
        grad = QRadialGradient(cx - 2, cy - 2, 1, cx, cy, r)
        grad.setColorAt(0.0, QColor(110, 105, 92))
        grad.setColorAt(0.6, QColor(55,  52,  46))
        grad.setColorAt(1.0, QColor(25,  23,  20))
        p.setPen(QPen(QColor(15, 14, 12), 0.5))
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, cy), r, r)


# ============================================================
#  Demo
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instrument Panel — WWII Cockpit")
        self.setStyleSheet("background-color: #3C4323;")

        configs = [
            GaugeConfig(label="FUEL PRESSURE",  unit="LBS / SQ IN", min_val=0,   max_val=100,  danger_from=80),
            GaugeConfig(label="OIL PRESSURE",   unit="LBS / SQ IN", min_val=0,   max_val=120,  danger_from=100),
            GaugeConfig(label="MANIFOLD PRESS", unit="IN. HG.",      min_val=10,  max_val=60,   danger_from=52),
            GaugeConfig(label="ENGINE RPM",     unit="RPM × 100",   min_val=0,   max_val=35,   danger_from=30),
        ]
        theme = theme_wwii_cockpit()

        from PySide6.QtWidgets import QGridLayout, QWidget as QW
        container = QW()
        container.setStyleSheet("background-color: #3C4323;")
        grid = QGridLayout(container)
        grid.setSpacing(8)
        grid.setContentsMargins(12, 12, 12, 12)

        self.gauges = []
        self._targets = []
        for i, cfg in enumerate(configs):
            g = Gauge(config=cfg, theme=theme)
            row, col = divmod(i, 2)
            grid.addWidget(g, row, col)
            self.gauges.append(g)
            self._targets.append((cfg.min_val + cfg.max_val) / 2)

        self.setCentralWidget(container)
        self.resize(860, 860)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(800)

    def _tick(self):
        for i, g in enumerate(self.gauges):
            cfg = g.config
            self._targets[i] += random.uniform(
                -(cfg.max_val - cfg.min_val) * 0.08,
                 (cfg.max_val - cfg.min_val) * 0.08
            )
            self._targets[i] = max(cfg.min_val, min(cfg.max_val, self._targets[i]))
            g.value = self._targets[i]
            g.update()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

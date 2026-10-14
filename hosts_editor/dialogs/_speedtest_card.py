import logging
import math
import re
import threading
import time

from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt, Signal, QObject, QPointF, QRectF, QByteArray
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QLinearGradient
from PySide6.QtSvg import QSvgRenderer
import shiboken6

from ..icons import FIF
from ..ui_parts import IconWidget
from ..constants import DARK, load_settings, save_settings
from ..widgets_qt import HOTSButton, attach_fluent_tip, attach_fluent_point_tip, colored_svg_icon
from ..i18n import T
from ..bg_tasks import start_bg_thread, is_shutting_down
from ..core_speedtest import run_speedtest, quality_levels, DURATION
from ._features_shared import _InfoButton

logger = logging.getLogger("HOTS.speedtest")

_CHEVRON_CLOSED = FIF.CHEVRON_RIGHT_MED if hasattr(FIF, "CHEVRON_RIGHT_MED") else FIF.RIGHT_ARROW
_CHEVRON_OPEN = FIF.CHEVRON_DOWN_MED if hasattr(FIF, "CHEVRON_DOWN_MED") else FIF.DOWN


def _qcolor(value: str) -> QColor:
    # DARK holds both "#rrggbb" and CSS "rgba(r, g, b, a)" strings; QColor only parses the former.
    v = value.strip()
    if v.lower().startswith("rgba"):
        r, g, b, a = [x.strip() for x in v[v.index("(") + 1:v.rindex(")")].split(",")]
        return QColor(int(r), int(g), int(b), round(float(a) * 255))
    return QColor(v)


def _nice_max(v: float) -> float:
    v = max(v, 10.0)
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def _mmss(seconds: float) -> str:
    s = max(0, int(seconds + 0.5))
    return f"{s // 60}:{s % 60:02d}"


class _SpeedChart(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(84)
        self.setMinimumWidth(120)
        self.setStyleSheet("background: transparent; border: none;")
        self._down = []
        self._up = []

    def set_series(self, down, up):
        self._down = list(down)
        self._up = list(up)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_qcolor(DARK["border_faint"]), 1))
        p.setBrush(_qcolor(DARK["bg3"]))
        p.drawRoundedRect(frame, 4, 4)

        font = self.font()
        font.setPointSizeF(7)
        p.setFont(font)

        if not self._down and not self._up:
            p.setPen(_qcolor(DARK["fg2"]))
            p.drawText(frame, Qt.AlignCenter, T("speed_chart_empty"))
            p.end()
            return

        plot = frame.adjusted(8, 16, -8, -8)
        ymax = _nice_max(max(v for _, v in self._down + self._up) * 1.1)

        p.setPen(QPen(_qcolor(DARK["border_faint"]), 1, Qt.DashLine))
        for frac in (0.0, 0.5, 1.0):
            y = plot.bottom() - frac * plot.height()
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        p.setPen(_qcolor(DARK["fg2"]))
        p.drawText(QRectF(frame.left() + 8, frame.top() + 2, 120, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, f"{ymax:g} {T('speed_unit')}")

        def xy(t, v):
            return QPointF(plot.left() + min(t / DURATION, 1.0) * plot.width(),
                           plot.bottom() - min(v / ymax, 1.0) * plot.height())

        for pts, color in ((self._down, _qcolor(DARK["accent"])), (self._up, _qcolor(DARK["green"]))):
            if not pts:
                continue
            points = [xy(t, v) for t, v in pts]
            line = QPainterPath(points[0])
            for pt in points[1:]:
                line.lineTo(pt)

            fill = QPainterPath(line)
            fill.lineTo(QPointF(points[-1].x(), plot.bottom()))
            fill.lineTo(QPointF(points[0].x(), plot.bottom()))
            fill.closeSubpath()
            grad = QLinearGradient(0, plot.top(), 0, plot.bottom())
            top = QColor(color); top.setAlpha(90)
            bottom = QColor(color); bottom.setAlpha(0)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawPath(fill)

            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(line)
        p.end()


# line icons (24x24 grid): web browsing, games, streaming. {c} is replaced by the colour.
_TILE_SVG = {
    "web": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<g fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
<rect x="2.5" y="3.5" width="17" height="14.5" rx="2"/><line x1="2.5" y1="7.8" x2="19.5" y2="7.8"/>
<rect x="5" y="10.5" width="5" height="5.5" rx="0.8"/><line x1="12" y1="11.5" x2="16" y2="11.5"/></g>
<g fill="{c}"><circle cx="4.8" cy="5.65" r="0.6"/><circle cx="6.8" cy="5.65" r="0.6"/><circle cx="8.8" cy="5.65" r="0.6"/>
<path d="M13.2 12.8 L13.2 21.6 L15.5 19.5 L17.2 23 L19 22.2 L17.3 18.8 L20.4 18.6 Z"/></g></svg>""",
    "games": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<g fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
<path d="M7.5 6.5 H16.5 A4.5 4.5 0 0 1 20.8 9.7 L22 14.5 A2.8 2.8 0 0 1 17.2 16.6 L16 14.5 H8 L6.8 16.6 A2.8 2.8 0 0 1 2 14.5 L3.2 9.7 A4.5 4.5 0 0 1 7.5 6.5 Z"/>
<path d="M7.5 9.2 V12.6 M5.8 10.9 H9.2"/></g>
<g fill="{c}"><circle cx="16.2" cy="9.6" r="0.8"/><circle cx="18.2" cy="11.5" r="0.8"/></g></svg>""",
    "stream": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<g fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
<rect x="2.5" y="4" width="19" height="14" rx="2"/><line x1="5.5" y1="15.2" x2="18.5" y2="15.2" stroke-width="1"/></g>
<g fill="{c}"><path d="M10 7.6 L15 10.6 L10 13.6 Z"/><circle cx="8.3" cy="15.2" r="1"/></g></svg>""",
}
_AMBER = "#d9a21b"
_TILE_SVG["live"] = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<g fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
<rect x="2.5" y="6.5" width="12.5" height="11" rx="2"/><path d="M15 10.8 L20.8 7.8 V16.2 L15 13.2"/></g>
<g fill="{c}"><circle cx="5.7" cy="9.5" r="0.9"/></g></svg>"""


def _level_color(level: int) -> str:
    if level >= 4:
        return DARK["green"]
    if level == 3:
        return _AMBER
    return DARK["red"]


class _QualityTile(QWidget):
    _ICON = 32
    _DOT = 5
    _GAP = 3

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._level = 0
        self._renderers = {}
        self.setFixedSize(44, self._ICON + 5 + self._DOT)
        self.setStyleSheet("background: transparent; border: none;")

    def set_level(self, level: int):
        self._level = level
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = _qcolor(DARK["fg2"] if self._level else DARK["gray"]).name()
        renderer = self._renderers.get(color)
        if renderer is None:
            svg = _TILE_SVG[self._kind].format(c=color)
            renderer = self._renderers[color] = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        renderer.render(p, QRectF((self.width() - self._ICON) / 2, 0, self._ICON, self._ICON))

        total = 5 * self._DOT + 4 * self._GAP
        x0 = (self.width() - total) / 2
        y = self._ICON + 5
        lit = _qcolor(_level_color(self._level))
        dim = _qcolor(DARK["border_strong"])
        p.setPen(Qt.NoPen)
        for i in range(5):
            p.setBrush(lit if i < self._level else dim)
            p.drawEllipse(QRectF(x0 + i * (self._DOT + self._GAP), y, self._DOT, self._DOT))
        p.end()


_HISTORY_MAX = 30


def _ping_line(ping, jitter) -> str:
    if ping is None:
        return T("speed_ping", v="—")
    p = f"{ping:.0f} ms"
    if jitter is None:
        return T("speed_ping", v=p)
    return T("speed_ping_jitter", p=p, j=f"{jitter:.1f} ms")


def _entry_text(e) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e["ts"]))
    f = lambda x: "—" if x is None else f"{x:.1f}"
    return f"{when}\n↓ {f(e['down'])} · ↑ {f(e['up'])} {T('speed_unit')}\n{_ping_line(e['ping'], e['jitter'])}"


def _clean_history(raw):
    """Keeps only well-formed entries (the file may have been edited or damaged)."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        try:
            down, ts = float(e["down"]), float(e["ts"])
            rest = {k: (None if e.get(k) is None else float(e[k])) for k in ("up", "ping", "jitter")}
            if all(math.isfinite(x) and x >= 0 for x in [down, ts] + [v for v in rest.values() if v is not None]):
                out.append({"ts": ts, "down": down, **rest})
        except Exception:
            continue
    return out[-_HISTORY_MAX:]


class _HistoryChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setMinimumWidth(120)
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent; border: none;")
        self._entries = []
        self._hover = -1
        attach_fluent_point_tip(self, self._tip_at)

    def set_entries(self, entries):
        entries = list(entries)
        if entries != self._entries:
            self._entries = entries
            self._hover = -1
            self.update()

    def _plot(self):
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5).adjusted(10, 16, -10, -8)

    def _x(self, i, plot):
        n = len(self._entries)
        return plot.left() + plot.width() * (0.5 if n == 1 else i / (n - 1))

    def _index_at(self, x) -> int:
        if not self._entries:
            return -1
        plot = self._plot()
        return min(range(len(self._entries)), key=lambda k: abs(self._x(k, plot) - x))

    def _tip_at(self, pos):
        i = self._index_at(pos.x())
        return (i, _entry_text(self._entries[i])) if i >= 0 else (-1, "")

    def mouseMoveEvent(self, event):
        i = self._index_at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_qcolor(DARK["border_faint"]), 1))
        p.setBrush(_qcolor(DARK["bg3"]))
        p.drawRoundedRect(frame, 4, 4)

        font = self.font()
        font.setPointSizeF(7)
        p.setFont(font)
        if not self._entries:
            p.setPen(_qcolor(DARK["fg2"]))
            p.drawText(frame, Qt.AlignCenter, T("speed_history_empty"))
            p.end()
            return

        plot = self._plot()
        ymax = _nice_max(max(e[k] for e in self._entries for k in ("down", "up") if e[k] is not None) * 1.1)
        p.setPen(QPen(_qcolor(DARK["border_faint"]), 1, Qt.DashLine))
        for frac in (0.0, 0.5, 1.0):
            y = plot.bottom() - frac * plot.height()
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(_qcolor(DARK["fg2"]))
        p.drawText(QRectF(frame.left() + 8, frame.top() + 2, 120, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, f"{ymax:g} {T('speed_unit')}")

        def point(i, v):
            return QPointF(self._x(i, plot), plot.bottom() - min(v / ymax, 1.0) * plot.height())

        for key, color in (("down", _qcolor(DARK["accent"])), ("up", _qcolor(DARK["green"]))):
            have = [(i, e[key]) for i, e in enumerate(self._entries) if e[key] is not None]
            line = QPainterPath()
            prev = None
            for i, v in have:
                if prev is not None and i == prev + 1:
                    line.lineTo(point(i, v))
                else:
                    line.moveTo(point(i, v))
                prev = i
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(line)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            for i, v in have:
                r = 3.5 if i == self._hover else 2.2
                p.drawEllipse(point(i, v), r, r)
        p.end()


class _SpeedSignals(QObject):
    ping = Signal(float, object)  # (ms, jitter ms or None)
    sample = Signal(str, float, float)
    result = Signal(str, object, str)
    done = Signal(object, str)


class _SpeedtestCardMixin:

    def _speedtest_init(self):
        self._speed_running = False
        self._speed_expanded = False
        self._speed_stop = threading.Event()
        self._speed_pts = {"down": [], "up": []}
        self._speed_ping = None
        self._speed_jitter = None
        self._speed_val = {"down": None, "up": None}
        self._speed_final = {"down": False, "up": False}
        self._speed_has_run = False
        self._speed_err = ""
        self._speed_429 = 0
        self._speed_block_until = 0.0
        self._speed_wait_left = 0.0
        self._speed_status_key = "speed_status_idle"
        self._speed_signals = None
        self._speed_w = {}
        self._speed_last = None  # last completed result, kept in settings.json between runs
        self._speed_history = []  # the last completed tests, oldest first, also kept in settings.json
        self._speed_load_saved()

    def _speed_load_saved(self):
        try:
            settings = load_settings()
        except Exception:
            return
        self._speed_history = _clean_history(settings.get("speedtest_history"))
        try:
            d = settings.get("speedtest_last")
            down = float(d["down"])
            up = None if d.get("up") is None else float(d["up"])
            ping = None if d.get("ping") is None else float(d["ping"])
            jitter = None if d.get("jitter") is None else float(d["jitter"])
            ts = float(d.get("ts") or 0)
            pts = {k: [(float(t), float(v)) for t, v in (d.get("pts") or {}).get(k, [])][:200]
                   for k in ("down", "up")}
            if not all(math.isfinite(x) and x >= 0
                       for x in (down, ts) + tuple(v for v in (up, ping, jitter) if v is not None)):
                return
        except Exception:
            return
        self._speed_last = {"down": down, "up": up, "ping": ping, "jitter": jitter, "ts": ts, "pts": pts}
        if not self._speed_history:  # a result saved before the history existed becomes its first entry
            self._speed_history = [{"ts": ts, "down": down, "up": up, "ping": ping, "jitter": jitter}]
        self._speed_apply_last()
        self._speed_has_run = True
        self._speed_status_key = "speed_status_done"

    def _speed_apply_last(self):
        last = self._speed_last
        if not last:
            self._speed_pts = {"down": [], "up": []}
            self._speed_val = {"down": None, "up": None}
            self._speed_final = {"down": False, "up": False}
            self._speed_ping = self._speed_jitter = None
            return
        self._speed_val = {"down": last["down"], "up": last["up"]}
        self._speed_final = {"down": True, "up": last["up"] is not None}
        self._speed_ping = last["ping"]
        self._speed_jitter = last.get("jitter")
        self._speed_pts = {k: list(last["pts"][k]) for k in ("down", "up")}

    def _speed_save_last(self):
        pts = {k: [(round(t, 2), round(v, 1)) for t, v in self._speed_pts[k]] for k in ("down", "up")}
        now = time.time()
        self._speed_last = {"down": self._speed_val["down"], "up": self._speed_val["up"],
                            "ping": self._speed_ping, "jitter": self._speed_jitter, "ts": now, "pts": pts}
        self._speed_history = (self._speed_history + [{
            "ts": now, "down": self._speed_val["down"], "up": self._speed_val["up"],
            "ping": self._speed_ping, "jitter": self._speed_jitter}])[-_HISTORY_MAX:]
        try:
            data = load_settings()
            data["speedtest_last"] = {**self._speed_last, "pts": {k: [list(p) for p in v] for k, v in pts.items()}}
            data["speedtest_history"] = self._speed_history
            save_settings(data)
        except Exception as exc:
            logger.warning("could not save the speed test result: %s", exc)

    def _make_speedtest_card(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(
            f"background: {DARK['panel_bg']}; border: 1px solid {DARK['border_faint']}; border-radius: 6px;"
        )
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background: transparent; border: none;")
        self._speedtest_header = header
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(10)

        icon = IconWidget(FIF.WIFI)
        icon.setFixedSize(20, 20)
        icon.setIcon(colored_svg_icon(FIF.WIFI, _qcolor(DARK["accent"]), sizes=(20,)))
        h.addWidget(icon, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(T("speed_card_title"))
        title.setStyleSheet(f"color: {DARK['fg']}; font-size: 12pt; background: transparent; border: none;")
        title_row.addWidget(title)
        title_row.addWidget(_InfoButton(T("speed_card_tooltip")))
        title_row.addStretch()
        text_col.addLayout(title_row)

        desc = QLabel(T("speed_card_desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8pt; background: transparent; border: none;")
        text_col.addWidget(desc)

        status = QLabel()
        status.setWordWrap(True)
        status.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8.5pt; background: transparent; border: none;")
        text_col.addWidget(status)
        h.addLayout(text_col, 1)

        side_col = QVBoxLayout()
        side_col.setSpacing(4)
        side_col.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toggle_btn = HOTSButton(
            _CHEVRON_OPEN if self._speed_expanded else _CHEVRON_CLOSED,
            DARK["fg2"], "", accent=False, glyph_color=DARK["accent"],
        )
        toggle_btn.setFixedWidth(44)
        side_col.addWidget(toggle_btn, 0, Qt.AlignRight)
        h.addLayout(side_col)
        outer_lay.addWidget(header)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {DARK['border_faint']}; border: none;")
        outer_lay.addWidget(sep)
        sep.setVisible(self._speed_expanded)

        panel = QWidget()
        panel.setStyleSheet("background: transparent; border: none;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(14)
        chart = _SpeedChart()
        body.addWidget(chart, 1)

        right = QVBoxLayout()
        right.setSpacing(2)
        right.setAlignment(Qt.AlignVCenter)
        vals = {}
        for phase, arrow, color in (("down", "↓", DARK["accent"]), ("up", "↑", DARK["green"])):
            row = QHBoxLayout()
            row.setSpacing(6)
            arr = QLabel(arrow)
            arr.setStyleSheet(f"color: {color}; font-size: 13pt; background: transparent; border: none;")
            val = QLabel("—")
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setMinimumWidth(70)
            unit = QLabel(T("speed_unit"))
            unit.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8.5pt; background: transparent; border: none;")
            row.addWidget(arr)
            row.addWidget(val, 1)
            row.addWidget(unit)
            right.addLayout(row)
            vals[phase] = val
        body.addLayout(right)
        v.addLayout(body)

        # ping on the left, icons in the middle, run button on the right: the two outer cells
        # have the same stretch, so the middle one stays centred
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        ping = QLabel()
        ping.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        ping.setStyleSheet(f"color: {DARK['fg']}; font-size: 9pt; background: transparent; border: none;")
        bottom.addWidget(ping, 1, Qt.AlignLeft | Qt.AlignVCenter)

        tiles = {}
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(10)
        for kind in ("web", "games", "stream", "live"):
            tiles[kind] = _QualityTile(kind)
            tiles_row.addWidget(tiles[kind], 0, Qt.AlignVCenter)
        bottom.addLayout(tiles_row)

        run_btn = HOTSButton(FIF.PLAY, DARK["gray"], "", accent=False, glyph_color=DARK["accent"])
        run_btn.setFixedWidth(44)
        bottom.addWidget(run_btn, 1, Qt.AlignRight | Qt.AlignVCenter)
        v.addLayout(bottom)

        v.addSpacing(4)
        hist_title = QLabel(T("speed_history_title"))
        hist_title.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8.5pt; background: transparent; border: none;")
        v.addWidget(hist_title)
        history = _HistoryChart()
        v.addWidget(history)

        outer_lay.addWidget(panel)
        panel.setVisible(self._speed_expanded)

        self._speed_w = {"status": status, "toggle_btn": toggle_btn, "btn": run_btn, "panel": panel,
                         "sep": sep, "chart": chart, "vals": vals, "ping": ping, "tiles": tiles, "history": history}
        toggle_btn.clicked.connect(self._speed_toggle_panel)
        run_btn.clicked.connect(self._speed_toggle)
        self._speed_update_ui()
        return outer

    def _speed_toggle_panel(self):
        w = self._speed_w
        if not w or not shiboken6.isValid(w["panel"]):
            return
        self._speed_expanded = not self._speed_expanded
        w["panel"].setVisible(self._speed_expanded)
        w["sep"].setVisible(self._speed_expanded)
        self._speed_update_ui()

    def _speed_header_text(self) -> str:
        key = self._speed_status_key
        text = T(key)
        if key == "speed_status_wait":
            return T("speed_status_wait", t=_mmss(self._speed_wait_left))
        if self._speed_err and not self._speed_running:
            if "429" in self._speed_err:
                return T("speed_status_429", t=_mmss(self._speed_block_until - time.time()))
            return f"{text} ({self._speed_err[:90]})"
        if key == "speed_status_done":
            d, u = self._speed_val["down"], self._speed_val["up"]
            fmt = lambda x: "—" if x is None else f"{x:.1f}"
            ping = T("speed_ping", v="—" if self._speed_ping is None else f"{self._speed_ping:.0f} ms")
            return f"↓ {fmt(d)} · ↑ {fmt(u)} {T('speed_unit')} · {ping}{self._speed_age_suffix()}"
        return text

    def _speed_age_suffix(self) -> str:
        """The date (" · 2026-10-06") when the shown result is not from today, so an old one does not look fresh."""
        last = self._speed_last
        if not last or not last.get("ts"):
            return ""
        day = time.strftime("%Y-%m-%d", time.localtime(last["ts"]))
        return "" if day == time.strftime("%Y-%m-%d") else f" · {day}"

    def _speed_ping_text(self) -> str:
        return _ping_line(self._speed_ping, self._speed_jitter)

    def _speed_update_ui(self):
        w = self._speed_w
        if not w or not shiboken6.isValid(w["chart"]):
            return
        running = self._speed_running
        w["status"].setText(self._speed_header_text())
        tip_lines = []
        if self._speed_last and not running:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self._speed_last["ts"]))
            tip_lines.append(T("speed_last_test", v=when))
        if self._speed_err:
            tip_lines.append(self._speed_err)
        attach_fluent_tip(w["status"], "\n".join(tip_lines))
        w["chart"].set_series(self._speed_pts["down"], self._speed_pts["up"])
        w["history"].set_entries(self._speed_history)

        for phase in ("down", "up"):
            val = self._speed_val[phase]
            color = DARK["fg"] if self._speed_final[phase] else DARK["fg2"]
            lbl = w["vals"][phase]
            lbl.setStyleSheet(f"color: {color}; font-size: 16pt; background: transparent; border: none;")
            lbl.setText("—" if val is None else f"{val:.1f}")
        w["ping"].setText(self._speed_ping_text())

        # only finished phases count, so the dots do not jump around while a phase is running
        levels = quality_levels(self._speed_val["down"] if self._speed_final["down"] else None,
                                self._speed_val["up"] if self._speed_final["up"] else None,
                                self._speed_ping, self._speed_jitter)
        for kind, tile in w["tiles"].items():
            level = levels[kind]
            tile.set_level(level)
            attach_fluent_tip(tile, T(f"speed_q_{kind}", n=level) if level else T("speed_q_none"), width=260)

        btn = w["btn"]
        if running:
            btn.set_icon(FIF.PAUSE, DARK["red"])
            tip = T("speed_btn_stop")
        elif self._speed_has_run:
            btn.set_icon(FIF.SYNC, DARK["gray"])
            tip = T("speed_btn_repeat")
        else:
            btn.set_icon(FIF.PLAY, DARK["gray"])
            tip = T("speed_btn_start")
        btn.set_accent(False)
        attach_fluent_tip(btn, tip)

        tb = w["toggle_btn"]
        attach_fluent_tip(tb, T("speed_btn_close") if self._speed_expanded else T("speed_btn_open"))
        tb.set_icon(_CHEVRON_OPEN if self._speed_expanded else _CHEVRON_CLOSED,
                    color=DARK["fg2"], glyph_color=DARK["accent"])

    def _speed_toggle(self):
        if self._speed_running:
            self._speed_stop.set()
            return

        # the server asked us to slow down (HTTP 429) - do not send anything until that time passes
        left = self._speed_block_until - time.time()
        if left > 0:
            self._speed_wait_left = left
            self._speed_err = ""
            self._speed_status_key = "speed_status_wait"
            self._speed_update_ui()
            return

        self._speed_running = True
        self._speed_has_run = True
        self._speed_stop = threading.Event()
        stop = self._speed_stop
        self._speed_pts = {"down": [], "up": []}
        self._speed_ping = self._speed_jitter = None
        self._speed_val = {"down": None, "up": None}
        self._speed_final = {"down": False, "up": False}
        self._speed_status_key = "speed_status_ping"
        self._speed_err = ""

        sig = _SpeedSignals(self)
        sig.ping.connect(self._speed_on_ping)
        sig.sample.connect(self._speed_on_sample)
        sig.result.connect(self._speed_on_result)
        sig.done.connect(self._speed_on_done)
        self._speed_signals = sig

        def worker():
            try:
                res = run_speedtest(sig.ping.emit, sig.sample.emit, sig.result.emit,
                                    lambda: stop.is_set() or is_shutting_down())
                sig.done.emit(res, "stopped" if res is None else "ok")
            except Exception as exc:
                logger.warning("speed test failed: %s", exc)
                sig.done.emit(str(exc), "error")

        if start_bg_thread(worker) is None:
            self._speed_running = False
            self._speed_status_key = "speed_status_idle"
        self._speed_update_ui()

    def _speed_note_error(self, err: str) -> str:
        """On HTTP 429 remember how long to stay away: the server's Retry-After if it sent one,
        otherwise 1, then 3, then 10 minutes for repeated refusals. Returns the text to show."""
        m = re.search(r"\s*\[retry-after=(\d+)\]", err)
        if "429" in err:
            self._speed_429 += 1
            wait = int(m.group(1)) if m else (60, 180, 600)[min(self._speed_429 - 1, 2)]
            self._speed_block_until = time.time() + wait
        return err[:m.start()] + err[m.end():] if m else err

    def _speed_on_ping(self, ms: float, jitter):
        self._speed_ping = ms
        self._speed_jitter = jitter
        self._speed_status_key = "speed_status_down"
        self._speed_update_ui()

    def _speed_on_sample(self, phase: str, t: float, mbps: float):
        self._speed_pts[phase].append((t, mbps))
        self._speed_val[phase] = mbps
        self._speed_update_ui()

    def _speed_on_result(self, phase: str, avg, err: str):
        self._speed_val[phase] = avg
        self._speed_final[phase] = avg is not None
        if err:
            self._speed_err = self._speed_note_error(err)
            self._speed_pts[phase] = []
            logger.warning("speed test: %s", err)
        if phase == "down":
            self._speed_status_key = "speed_status_up"
        self._speed_update_ui()

    def _speed_on_done(self, res, kind: str):
        if not shiboken6.isValid(self) or is_shutting_down():
            return
        self._speed_running = False
        if kind == "ok":
            if "429" not in self._speed_err:
                self._speed_429 = 0
            self._speed_status_key = ("speed_status_done" if self._speed_val["up"] is not None
                                      else "speed_status_up_failed")
            self._speed_save_last()
        elif kind == "stopped":
            self._speed_apply_last()  # a stopped test does not replace the last result
            self._speed_status_key = "speed_status_done" if self._speed_last else "speed_status_stopped"
        else:
            self._speed_err = self._speed_note_error(res) if isinstance(res, str) else ""
            self._speed_apply_last()  # nor does a failed one
            self._speed_status_key = "speed_status_error"
        self._speed_update_ui()
        self._retry_pending_refresh()

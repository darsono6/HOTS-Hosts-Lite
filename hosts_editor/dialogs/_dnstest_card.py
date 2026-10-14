import logging
import math
import sys
import threading
import time

from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QApplication
from PySide6.QtCore import Qt, Signal, QObject, QRectF, QEvent
from PySide6.QtGui import QPainter, QPen, QColor, QFontMetrics, QIcon, QTransform
import shiboken6

from ..icons import FIF
from ..ui_parts import IconWidget, TransparentToolButton
from ..constants import DARK, load_settings, save_settings
from ..widgets_qt import HOTSButton, HOTSDialog, attach_fluent_tip, attach_fluent_point_tip, colored_svg_icon
from ..i18n import T
from ..bg_tasks import start_bg_thread, is_shutting_down
from ..core_dnstest import (PUBLIC, ALTERNATE, FAST_MS, build_servers, system_dns_servers,
                            run_dns_test, verdict, set_system_dns, reset_system_dns,
                            saved_original_servers, system_dns_is_default)
from ._features_shared import _InfoButton
from ._speedtest_card import _qcolor, _AMBER, _CHEVRON_CLOSED, _CHEVRON_OPEN

logger = logging.getLogger("HOTS.dnstest")

_KEYS = {"system"} | {key for key, _, _ in PUBLIC}
_NAMES = {key: name for key, name, _ in PUBLIC}
_ADDRS = {key: addr for key, _, addr in PUBLIC}
_KEY_BY_NAME = {name: key for key, name, _ in PUBLIC}
_CAN_SET = sys.platform == "win32"


def _ms(value) -> str:
    return f"{value:.0f} ms"


def _label(key: str, name: str, provider: str, default: bool = False) -> str:
    if key == "system":
        text = f"{T('dns_system')} · {provider}" if provider else T("dns_system")
        return f"{text} {T('dns_default_suffix')}" if default else text
    return name


def _view(stats, pending: bool = False) -> dict:
    return {"key": stats["key"], "name": stats.get("name", ""), "provider": stats.get("provider", ""),
            "label": _label(stats["key"], stats.get("name", ""), stats.get("provider", ""), stats.get("default", False)),
            "default": bool(stats.get("default", False)),
            "addr": stats["addr"], "median": stats.get("median"), "best": stats.get("best"),
            "worst": stats.get("worst"), "lost": stats.get("lost", 0), "total": stats.get("total", 0),
            "pending": pending}


def _ms_color(ms: float) -> str:
    if ms <= FAST_MS:
        return DARK["green"]
    return _AMBER if ms <= 80 else DARK["red"]


def _provider_key(row) -> str:
    """Key of the well-known provider behind a row ("" for an unknown own DNS, e.g. a router)."""
    return row["key"] if row["key"] in _NAMES else _KEY_BY_NAME.get(row["provider"], "")


def _row_tip(row) -> str:
    head = f"{row['label']} ({row['addr']})\n"
    if row["pending"]:
        return ""
    if row["median"] is None:
        return head + T("dns_row_tip_none", total=row["total"])
    return head + T("dns_row_tip", m=_ms(row["median"]), b=_ms(row["best"]), w=_ms(row["worst"]),
                    lost=row["lost"], total=row["total"])


def _clean_stats(raw):
    """Validated copy of a saved result (the settings file may have been edited or damaged)."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        try:
            key = e["key"]
            if key not in _KEYS or key in {s["key"] for s in out}:
                continue
            nums = {k: (None if e.get(k) is None else float(e[k])) for k in ("median", "best", "worst")}
            lost, total = int(e["lost"]), int(e["total"])
            if lost < 0 or total < 0 or any(v is not None and not (math.isfinite(v) and v >= 0) for v in nums.values()):
                continue
            addr = str(e["addr"])[:45]
            out.append({"key": key, "name": _NAMES.get(key, ""), "provider": str(e.get("provider") or "")[:30],
                        "addr": addr, "default": bool(e.get("default")), "lost": lost, "total": total, **nums})
        except Exception:
            continue
    return out


class _DnsBars(QWidget):
    ROW_H = 30
    MAX_ROWS = 5
    BTN = 22
    BTN_COL = 30
    INFO_COL = 24

    set_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.ROW_H * self.MAX_ROWS)
        self.setMinimumWidth(240 + self.INFO_COL + self._btn_col())
        self.setStyleSheet("background: transparent; border: none;")
        self._rows = []
        self._kids = []
        self._can_set = True
        self._show_active = True
        attach_fluent_point_tip(self, self._tip_at)

    def _btn_col(self) -> int:
        return self.BTN_COL if _CAN_SET else 0

    def set_rows(self, rows, can_set=True, show_active=True):
        rows = list(rows)
        self._can_set = can_set
        if rows != self._rows or show_active != self._show_active:
            self._rows = rows
            self._show_active = show_active
            self._sync_children()
            self.update()
        for b in self._kids:
            if isinstance(b, TransparentToolButton) and not b.property("active"):
                b.setEnabled(can_set)

    def _sync_children(self):
        for w in self._kids:
            w.deleteLater()
        self._kids = []
        for i, row in enumerate(self._rows):
            key = _provider_key(row)
            if key:
                self._add_info(i, T("dns_info_" + key))
            if _CAN_SET and row["key"] in _ADDRS and not row["pending"] and row["median"] is not None:
                # only a provider that actually answered can be set
                tip = T("dns_btn_set", name=row["name"], a=_ADDRS[row["key"]], b=ALTERNATE[row["key"]])
                self._add_button(i, tip, row["key"])
            if _CAN_SET and self._show_active and row["key"] == "system" and not row["pending"] \
                    and row["median"] is not None:
                self._add_button(i, T("dns_active", a=row["addr"]), None)

    def _add_info(self, i: int, text: str):
        w = _InfoButton(text, self)
        w.installEventFilter(self)
        w.move(0, i * self.ROW_H + (self.ROW_H - w.height()) // 2)
        w.show()
        self._kids.append(w)

    def _add_button(self, i: int, tip: str, key):
        """key None = the marker of the DNS in use: the same icon turned by 180 degrees, not clickable."""
        b = TransparentToolButton(FIF.PLAY, self)
        b.setFixedSize(self.BTN, self.BTN)
        icon = colored_svg_icon(FIF.PLAY, QColor(DARK["accent"]), sizes=(22,))
        if key is None:
            b.setProperty("active", True)
            icon = colored_svg_icon(FIF.PLAY, QColor(DARK["green"]), sizes=(22,))
            icon = QIcon(icon.pixmap(22, 22).transformed(QTransform().rotate(180), Qt.SmoothTransformation))
        else:
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(self._can_set)
            b.clicked.connect(lambda _=False, k=key: self.set_requested.emit(k))
        b.setIcon(icon)
        b.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 5px; }"
            + ("" if key is None else
               "QToolButton:hover { background: rgba(128, 128, 128, 30); }"
               "QToolButton:pressed { background: rgba(128, 128, 128, 45); }")
        )
        b.installEventFilter(self)
        attach_fluent_tip(b, tip, width=260)
        b.setProperty("row", i)
        self._place(b)
        b.show()
        self._kids.append(b)

    def _place(self, b):
        b.move(self.width() - self.BTN, int(b.property("row")) * self.ROW_H + (self.ROW_H - self.BTN) // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for b in self._kids:
            if isinstance(b, TransparentToolButton):
                self._place(b)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Enter:
            QApplication.sendEvent(self, QEvent(QEvent.Leave))
        return False

    def _tip_at(self, pos):
        i = int(pos.y() // self.ROW_H)
        if pos.x() < self.INFO_COL or pos.x() >= self.width() - self._btn_col():
            return -1, ""  # the icon and button columns have tips of their own
        if 0 <= i < len(self._rows):
            return i, _row_tip(self._rows[i])
        return -1, ""

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        small = self.font()
        small.setPointSizeF(7.5)
        normal = self.font()
        normal.setPointSizeF(9)
        bold = self.font()
        bold.setPointSizeF(9)
        bold.setBold(True)

        if not self._rows:
            p.setFont(small)
            p.setPen(_qcolor(DARK["fg2"]))
            p.drawText(QRectF(0, 0, self.width(), self.ROW_H * 2), Qt.AlignCenter, T("dns_empty"))
            p.end()
            return

        name_x, name_w, val_w, gap = self.INFO_COL, 180, 90, 10
        right = self.width() - self._btn_col()
        bar_x = name_x + name_w + gap
        bar_w = max(20, right - bar_x - val_w - gap)
        answered = [r["median"] for r in self._rows if r["median"] is not None]
        scale = max(60.0, max(answered) if answered else 60.0) * 1.05
        fastest = min(answered) if answered else None

        def text(font, color, rect, flags, value):
            p.setFont(font)
            p.setPen(_qcolor(color))
            p.drawText(rect, flags, QFontMetrics(font).elidedText(value, Qt.ElideRight, int(rect.width())))

        for i, row in enumerate(self._rows):
            y = i * self.ROW_H
            text(normal, DARK["fg"], QRectF(name_x, y + 2, name_w, 15), Qt.AlignLeft | Qt.AlignVCenter, row["label"])
            text(small, DARK["fg2"], QRectF(name_x, y + 16, name_w, 12), Qt.AlignLeft | Qt.AlignVCenter, row["addr"])

            track = QRectF(bar_x, y + 10, bar_w, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor(DARK["border_faint"]))
            p.drawRoundedRect(track, 4, 4)

            value_rect = QRectF(right - val_w, y, val_w, self.ROW_H)
            if row["pending"]:
                p.setFont(normal)
                p.setPen(_qcolor(DARK["fg2"]))
                p.drawText(value_rect, Qt.AlignRight | Qt.AlignVCenter, "…")
            elif row["median"] is None:
                p.setFont(small)
                p.setPen(_qcolor(DARK["red"]))
                p.drawText(value_rect, Qt.AlignRight | Qt.AlignVCenter, T("dns_no_answer"))
            else:
                p.setBrush(_qcolor(_ms_color(row["median"])))
                p.drawRoundedRect(QRectF(bar_x, y + 10, max(6.0, bar_w * row["median"] / scale), 8), 4, 4)
                best = row["median"] == fastest
                p.setFont(bold if best else normal)
                p.setPen(_qcolor(DARK["accent"] if best else DARK["fg"]))
                p.drawText(value_rect, Qt.AlignRight | Qt.AlignVCenter, _ms(row["median"]))

        p.end()


class _DnsSignals(QObject):
    row = Signal(int, object)   # (server index, its statistics)
    done = Signal(object, str)  # (list of statistics or None, "ok" / "stopped" / "error")
    applied = Signal(str, str)  # (provider key, error text or "")


class _DnsTestCardMixin:

    def _dnstest_init(self):
        self._dns_running = False
        self._dns_expanded = False
        self._dns_stop = threading.Event()
        self._dns_rows = []
        self._dns_done = 0
        self._dns_total = 0
        self._dns_has_run = False
        self._dns_status_key = "dns_status_idle"
        self._dns_signals = None
        self._dns_w = {}
        self._dns_applying = False
        self._dns_reset_prev = False
        self._dns_active_known = True  # False after a change, until the next test shows what is in use
        self._dns_apply_signals = None
        self._dns_last = None  # {"ts", "stats"}: the last completed test, kept in settings.json
        self._dns_load_saved()

    def _dns_load_saved(self):
        try:
            d = load_settings().get("dnstest_last")
            ts = float(d["ts"])
            stats = _clean_stats(d["rows"])
            if not stats or not (math.isfinite(ts) and ts >= 0):
                return
        except Exception:
            return
        self._dns_last = {"ts": ts, "stats": stats}
        self._dns_apply_last()
        self._dns_has_run = True
        self._dns_status_key = "dns_status_done"

    def _dns_apply_last(self):
        last = self._dns_last
        self._dns_rows = [_view(s) for s in last["stats"]] if last else []

    def _dns_save_last(self, stats):
        self._dns_last = {"ts": time.time(), "stats": stats}
        try:
            data = load_settings()
            data["dnstest_last"] = {"ts": self._dns_last["ts"], "rows": stats}
            save_settings(data)
        except Exception as exc:
            logger.warning("could not save the DNS test result: %s", exc)

    def _make_dnstest_card(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(
            f"background: {DARK['panel_bg']}; border: 1px solid {DARK['border_faint']}; border-radius: 6px;"
        )
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background: transparent; border: none;")
        self._dnstest_header = header
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(10)

        icon = IconWidget(FIF.GLOBE)
        icon.setFixedSize(20, 20)
        icon.setIcon(colored_svg_icon(FIF.GLOBE, _qcolor(DARK["accent"]), sizes=(20,)))
        h.addWidget(icon, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(T("dns_card_title"))
        title.setStyleSheet(f"color: {DARK['fg']}; font-size: 12pt; background: transparent; border: none;")
        title_row.addWidget(title)
        title_row.addWidget(_InfoButton(T("dns_card_tooltip")))
        title_row.addStretch()
        text_col.addLayout(title_row)

        desc = QLabel(T("dns_card_desc"))
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
            _CHEVRON_OPEN if self._dns_expanded else _CHEVRON_CLOSED,
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
        sep.setVisible(self._dns_expanded)

        panel = QWidget()
        panel.setStyleSheet("background: transparent; border: none;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)
        bars = _DnsBars()
        bars.set_requested.connect(self._dns_set_requested)
        v.addWidget(bars)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        verdict_lbl = QLabel()
        verdict_lbl.setWordWrap(True)
        verdict_lbl.setStyleSheet(f"color: {DARK['fg']}; font-size: 9pt; background: transparent; border: none;")
        bottom.addWidget(verdict_lbl, 1, Qt.AlignVCenter)
        run_btn = HOTSButton(FIF.PLAY, DARK["gray"], "", accent=False, glyph_color=DARK["accent"])
        run_btn.setFixedWidth(44)
        reset_btn = None
        if _CAN_SET:
            reset_btn = HOTSButton(FIF.HISTORY, DARK["gray"], "", accent=False, glyph_color=DARK["accent"])
            reset_btn.setFixedWidth(44)
            bottom.addWidget(reset_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(run_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        v.addLayout(bottom)

        outer_lay.addWidget(panel)
        panel.setVisible(self._dns_expanded)

        self._dns_w = {"status": status, "toggle_btn": toggle_btn, "btn": run_btn, "panel": panel,
                       "sep": sep, "bars": bars, "verdict": verdict_lbl, "reset": reset_btn}
        toggle_btn.clicked.connect(self._dns_toggle_panel)
        run_btn.clicked.connect(self._dns_toggle)
        if reset_btn is not None:
            reset_btn.clicked.connect(lambda: self._dns_set_requested("reset"))
        self._dns_update_ui()
        return outer

    def _dns_toggle_panel(self):
        w = self._dns_w
        if not w or not shiboken6.isValid(w["panel"]):
            return
        self._dns_expanded = not self._dns_expanded
        w["panel"].setVisible(self._dns_expanded)
        w["sep"].setVisible(self._dns_expanded)
        self._dns_update_ui()

    def _dns_age_suffix(self) -> str:
        last = self._dns_last
        if not last or not last.get("ts"):
            return ""
        day = time.strftime("%Y-%m-%d", time.localtime(last["ts"]))
        return "" if day == time.strftime("%Y-%m-%d") else f" · {day}"

    def _dns_header_text(self) -> str:
        key = self._dns_status_key
        if key == "dns_status_running":
            return T(key, d=self._dns_done, n=self._dns_total)
        if key != "dns_status_done":
            return T(key)
        system = next((r for r in self._dns_rows if r["key"] == "system"), None)
        public = [r for r in self._dns_rows if r["key"] != "system" and r["median"] is not None]
        parts = []
        if system:
            parts.append(f"{T('dns_system')}: " + (_ms(system["median"]) if system["median"] is not None
                                                    else T("dns_no_answer")))
        if public:
            best = min(public, key=lambda r: r["median"])
            parts.append(f"{T('dns_fastest')}: {best['label']} {_ms(best['median'])}")
        return (" · ".join(parts) or T("dns_v_offline")) + self._dns_age_suffix()

    def _dns_verdict_text(self) -> str:
        if self._dns_running or not self._dns_last or any(r["pending"] for r in self._dns_rows):
            return ""
        kind, data = verdict(self._dns_rows)
        if kind == "fast":
            return T("dns_v_fast", a=_ms(data["ms"]))
        if kind == "better":
            return T("dns_v_better", name=data["name"], d=_ms(data["gain"]))
        return T(f"dns_v_{kind}")

    def _dns_update_ui(self):
        w = self._dns_w
        if not w or not shiboken6.isValid(w["bars"]):
            return
        w["status"].setText(self._dns_header_text())
        tip = ""
        if self._dns_last and not self._dns_running:
            tip = T("speed_last_test", v=time.strftime("%Y-%m-%d %H:%M", time.localtime(self._dns_last["ts"])))
        attach_fluent_tip(w["status"], tip)
        busy = self._dns_running or self._dns_applying
        w["bars"].set_rows(self._dns_rows, can_set=not busy, show_active=self._dns_active_known)
        if w["reset"] is not None:
            w["reset"].setEnabled(not busy)
            attach_fluent_tip(w["reset"], T("dns_reset_title"))
        w["verdict"].setText(self._dns_verdict_text())

        btn = w["btn"]
        if self._dns_running:
            btn.set_icon(FIF.PAUSE, DARK["red"])
            btn_tip = T("speed_btn_stop")
        elif self._dns_has_run:
            btn.set_icon(FIF.SYNC, DARK["gray"])
            btn_tip = T("speed_btn_repeat")
        else:
            btn.set_icon(FIF.PLAY, DARK["gray"])
            btn_tip = T("speed_btn_start")
        btn.set_accent(False)
        attach_fluent_tip(btn, btn_tip)

        tb = w["toggle_btn"]
        attach_fluent_tip(tb, T("dns_btn_close") if self._dns_expanded else T("dns_btn_open"))
        tb.set_icon(_CHEVRON_OPEN if self._dns_expanded else _CHEVRON_CLOSED,
                    color=DARK["fg2"], glyph_color=DARK["accent"])

    def _dns_toggle(self):
        if self._dns_running:
            self._dns_stop.set()
            return

        system = system_dns_servers()
        servers = build_servers(system[0] if system else None)
        self._dns_running = True
        self.begin_busy()
        self._dns_has_run = True
        self._dns_stop = threading.Event()
        stop = self._dns_stop
        self._dns_rows = [_view(s, pending=True) for s in servers]
        self._dns_done, self._dns_total = 0, len(servers)
        self._dns_status_key = "dns_status_running"

        sig = _DnsSignals(self)
        sig.row.connect(self._dns_on_row)
        sig.done.connect(self._dns_on_done)
        self._dns_signals = sig

        def worker():
            try:
                if system:
                    servers[0]["default"] = system_dns_is_default() is True
                res = run_dns_test(servers, lambda: stop.is_set() or is_shutting_down(), sig.row.emit)
                complete = not stop.is_set() and all(r is not None for r in res)
                sig.done.emit(res if complete else None, "ok" if complete else "stopped")
            except Exception as exc:
                logger.warning("DNS test failed: %s", exc)
                sig.done.emit(None, "error")

        if start_bg_thread(worker) is None:
            self._dns_running = False
            self.end_busy()
            self._dns_apply_last()
            self._dns_status_key = "dns_status_idle"
        self._dns_update_ui()

    def _dns_on_row(self, i: int, stats):
        if 0 <= i < len(self._dns_rows):
            self._dns_rows[i] = _view(stats)
            self._dns_done += 1
            self._dns_update_ui()

    def _dns_on_done(self, stats, kind: str):
        if not shiboken6.isValid(self) or is_shutting_down():
            return
        self._dns_running = False
        self.end_busy()
        if kind == "ok":
            self._dns_active_known = True
            self._dns_save_last(stats)
            self._dns_apply_last()
            self._dns_status_key = "dns_status_done"
        else:
            self._dns_apply_last()  # a stopped or failed test does not replace the last result
            if kind == "stopped":
                self._dns_status_key = "dns_status_done" if self._dns_last else "speed_status_stopped"
            else:
                self._dns_status_key = "speed_status_error"
        self._dns_update_ui()
        self._retry_pending_refresh()

    def _dns_set_requested(self, key: str):
        if self._dns_running or self._dns_applying or (key != "reset" and key not in _ADDRS):
            return
        if key == "reset":
            title = T("dns_reset_title")
            own = saved_original_servers()
            if own:
                pick = HOTSDialog.choose(self, title, T("dns_reset_choice_msg"),
                                         [T("dns_reset_choice_prev", servers=", ".join(own)),
                                          T("dns_reset_choice_auto")])
                if pick is None:
                    return
                to_auto = pick == 1
            elif not HOTSDialog.ask(self, title, T("dns_reset_confirm")):
                return
            else:
                to_auto = True
            self._dns_reset_prev = bool(own) and not to_auto
            action = lambda: reset_system_dns(to_auto)
        else:
            name, a, b = _NAMES[key], _ADDRS[key], ALTERNATE[key]
            title, confirm = T("dns_set_title", name=name), T("dns_set_confirm", name=name, a=a, b=b)
            action = lambda: set_system_dns(a, b)
            if not HOTSDialog.ask(self, title, confirm):
                return
        self._dns_applying = True
        self.begin_busy()
        self._dns_update_ui()

        sig = _DnsSignals(self)
        sig.applied.connect(self._dns_on_applied)
        self._dns_apply_signals = sig

        def worker():
            try:
                err = action()
            except Exception as exc:
                err = str(exc)
            sig.applied.emit(key, err)

        if start_bg_thread(worker) is None:
            self._dns_applying = False
            self.end_busy()
            self._dns_update_ui()

    def _dns_on_applied(self, key: str, err: str):
        if not shiboken6.isValid(self) or is_shutting_down():
            return
        self._dns_applying = False
        self.end_busy()
        self._dns_update_ui()
        if key == "reset":
            title = T("dns_reset_title")
            ok = T("dns_reset_ok") if self._dns_reset_prev else T("dns_reset_ok_auto")
        else:
            name = _NAMES[key]
            title, ok = T("dns_set_title", name=name), T("dns_set_ok", name=name, a=_ADDRS[key], b=ALTERNATE[key])
        if err:
            HOTSDialog.error(self, title, T("dns_set_err", err=err))
        else:
            self._dns_active_known = False
            self._dns_update_ui()
            HOTSDialog.info(self, title, ok)
            if shiboken6.isValid(self) and not is_shutting_down() and not self._dns_running:
                self._dns_toggle()

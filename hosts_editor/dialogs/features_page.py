import logging
import os
import traceback
from typing import Dict

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QWidget, QScrollArea,
    QLineEdit, QCompleter, QFileDialog, QFileIconProvider, QFrame,
)
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QFileInfo
from PySide6.QtGui import QColor, QIcon, QTransform
import shiboken6

from qfluentwidgets import FluentIcon as FIF, IconWidget, TransparentToolButton

from ..constants import DARK
from ..core_hosts_lock import HostsLockError, HostsLockManager
from ..core_firewall import FirewallAppBlocker
from ..core_installed_apps import scan_installed_apps
from ..widgets_qt import HOTSPage, HOTSDialog, HOTSButton, h_separator, attach_fluent_tip, colored_svg_icon, attach_line_edit_context_menu
from ..i18n import T
from ..bg_tasks import start_bg_thread, is_shutting_down

from ._features_shared import _InfoButton, _FeatureCardMixin, CUSTOM_CATEGORY, any_info_popup_open, info_popup_bus

logger = logging.getLogger("HOTS.firewall")

_fw_icon_provider = QFileIconProvider()
_fw_icon_cache: Dict[str, QIcon] = {}


def _fw_program_icon(path: str) -> QIcon:
    key = os.path.normcase(path)
    cached = _fw_icon_cache.get(key)
    if cached is not None:
        return cached
    try:
        icon = _fw_icon_provider.icon(QFileInfo(path))
        if icon and not icon.isNull():
            _fw_icon_cache[key] = icon
            return icon
    except Exception:
        pass
    return QIcon()


_FW_CHEVRON_CLOSED = FIF.CHEVRON_RIGHT_MED if hasattr(FIF, "CHEVRON_RIGHT_MED") else FIF.RIGHT_ARROW
_FW_CHEVRON_OPEN = FIF.CHEVRON_DOWN_MED if hasattr(FIF, "CHEVRON_DOWN_MED") else FIF.DOWN


def _fw_row_separator() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background-color: {DARK['border_faint']}; border: none;")
    return f


def _fw_rotated_triangle_icon(color: QColor, degrees: int, size: int = 22) -> QIcon:
    base = colored_svg_icon(FIF.PLAY, color, sizes=(size,))
    pixmap = base.pixmap(size, size)
    rotated = pixmap.transformed(QTransform().rotate(degrees), Qt.SmoothTransformation)
    return QIcon(rotated)


class _FwSearchEdit(QLineEdit):

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        completer = self.completer()
        if completer is not None and completer.popup() is not None and completer.popup().isVisible():
            return
        if self.text():
            self.clear()


class _FirewallRow(QWidget):

    def __init__(self, path: str, name: str, blocked_out: bool, blocked_in: bool, missing: bool, removable: bool,
                 on_toggle, on_toggle_all, on_remove, parent=None):
        super().__init__(parent)
        self._path = path
        self._blocked_out = blocked_out
        self._blocked_in = blocked_in
        self._on_toggle = on_toggle
        self._on_toggle_all = on_toggle_all
        self._on_remove = on_remove

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)

        fully_blocked = blocked_out and blocked_in
        toggle_icon = FIF.PAUSE if fully_blocked else FIF.PLAY
        toggle_color = DARK["red"] if fully_blocked else DARK["green"]
        toggle_btn = TransparentToolButton(toggle_icon)
        toggle_btn.setFixedSize(22, 22)
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setIcon(colored_svg_icon(toggle_icon, QColor(toggle_color), sizes=(22,)))
        toggle_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 5px; }"
            "QToolButton:hover { background: rgba(128, 128, 128, 30); }"
            "QToolButton:pressed { background: rgba(128, 128, 128, 45); }"
        )
        attach_fluent_tip(toggle_btn, T("fw_btn_unblock") if fully_blocked else T("fw_btn_block"))
        toggle_btn.clicked.connect(lambda: self._on_toggle_all(self._path, self._blocked_out and self._blocked_in))
        h.addWidget(toggle_btn, 0, Qt.AlignVCenter)

        h.addSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        icon = _fw_program_icon(path)
        if not icon.isNull():
            icon_lbl.setPixmap(icon.pixmap(16, 16))
        h.addWidget(icon_lbl, 0, Qt.AlignVCenter)

        if missing and removable:
            suffix = T("fw_missing_removable_suffix")
            tooltip = T("fw_missing_removable_tooltip")
        elif missing:
            suffix = T("fw_missing_suffix")
            tooltip = T("fw_missing_tooltip")
        else:
            suffix = None
            tooltip = path

        name_lbl = QLabel(f"{name}  {suffix}" if suffix else name)
        name_lbl.setStyleSheet(f"color: {DARK['fg2'] if missing else DARK['fg']}; font-size: 9pt; background: transparent; border: none;")
        attach_fluent_tip(name_lbl, tooltip, width=260)
        h.addWidget(name_lbl, 1)

        self._out_btn = self._make_arrow_btn(-90, blocked_out)
        attach_fluent_tip(self._out_btn, T("fw_btn_unblock_out") if blocked_out else T("fw_btn_block_out"))
        self._out_btn.clicked.connect(lambda: self._on_toggle(self._path, "out", self._blocked_out))
        h.addWidget(self._out_btn, 0, Qt.AlignVCenter)

        self._in_btn = self._make_arrow_btn(90, blocked_in)
        attach_fluent_tip(self._in_btn, T("fw_btn_unblock_in") if blocked_in else T("fw_btn_block_in"))
        self._in_btn.clicked.connect(lambda: self._on_toggle(self._path, "in", self._blocked_in))
        h.addWidget(self._in_btn, 0, Qt.AlignVCenter)

        remove_btn = TransparentToolButton(FIF.DELETE)
        remove_btn.setFixedSize(22, 22)
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.setIcon(colored_svg_icon(FIF.DELETE, QColor(DARK["fg2"]), sizes=(22,)))
        remove_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 5px; }"
            "QToolButton:hover { background: rgba(128, 128, 128, 30); }"
            "QToolButton:pressed { background: rgba(128, 128, 128, 45); }"
        )
        attach_fluent_tip(remove_btn, T("fw_btn_remove"))
        remove_btn.clicked.connect(lambda: self._on_remove(self._path))
        h.addWidget(remove_btn, 0, Qt.AlignVCenter)

        self.setStyleSheet(f"background: {DARK['bg3']}; border-radius: 4px;")

    @staticmethod
    def _make_arrow_btn(rotation_degrees: int, blocked: bool) -> TransparentToolButton:
        color = DARK["red"] if blocked else DARK["green"]
        btn = TransparentToolButton(FIF.PLAY)
        btn.setFixedSize(22, 22)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(_fw_rotated_triangle_icon(QColor(color), rotation_degrees))
        btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 5px; }"
            "QToolButton:hover { background: rgba(128, 128, 128, 30); }"
            "QToolButton:pressed { background: rgba(128, 128, 128, 45); }"
        )
        return btn


class _HostsLockToggleSignals(QObject):
    done = Signal(bool)


class _FirewallOpSignals(QObject):
    done = Signal(bool, str, str)


class _FirewallScanSignals(QObject):
    done = Signal(list)


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()
        else:
            child_layout = item.layout()
            if child_layout:
                _clear_layout(child_layout)


class FeaturesPage(_FeatureCardMixin, HOTSPage):

    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        import re as _re_title
        clean_title = _re_title.sub(r"[^\w\s/.:,!?()-]", "", T("features_title")).strip()
        super().__init__("featuresInterface", FIF.FILTER, clean_title, parent)
        self._parent_win = parent
        self._states = {}
        self._toggle_signal_refs = []
        self._manual_ops_active = 0
        self._pending_refresh = False
        self._fw_expanded = False
        self._fw_missing_prompted = False
        self._fw_installed_cache = None
        self._fw_installed_map = {}
        info_popup_bus.popup_closed.connect(self._retry_pending_refresh)
        self._build()

    def _retry_pending_refresh(self):
        if self._pending_refresh:
            self.refresh_content()

    def _sync_card_heights(self, hosts_lock_card, other_cards):
        if not shiboken6.isValid(self) or not shiboken6.isValid(hosts_lock_card):
            return
        h = hosts_lock_card.height()
        for card in other_cards:
            if shiboken6.isValid(card):
                card.setFixedHeight(h)

    def refresh_content(self):
        if self._manual_ops_active > 0 or any_info_popup_open():
            self._pending_refresh = True
            return
        self._pending_refresh = False
        self._states = {}
        _clear_layout(self.content_layout)
        self._build()

    def _mark_op_start(self):
        was_idle = self._manual_ops_active == 0
        self._manual_ops_active += 1
        if was_idle:
            self.busy_changed.emit(True)
        self.begin_busy()

    def _mark_op_end(self):
        self._manual_ops_active = max(0, self._manual_ops_active - 1)
        self.end_busy()
        if self._manual_ops_active == 0:
            self.busy_changed.emit(False)
            self._retry_pending_refresh()

    def _set_categories_busy(self, busy: bool):
        if busy:
            self._mark_op_start()
        else:
            self._mark_op_end()

    def _build(self):
        rl = self.content_layout

        sub_row = QHBoxLayout()
        sub = QLabel(T("features_subheader"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {DARK['fg2']}; font-size: 9pt; background: transparent;")
        sub_row.addWidget(sub, 1)

        rl.addLayout(sub_row)
        rl.addSpacing(10)
        rl.addWidget(h_separator())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(12, 10, 12, 10)
        inner_lay.setSpacing(6)

        hosts_lock_card = self._make_hosts_lock_card()
        self._hosts_lock_card = hosts_lock_card
        inner_lay.addWidget(hosts_lock_card)
        inner_lay.addSpacing(6)

        custom_domains_module = self._make_custom_domains_module()
        self._custom_domains_card = custom_domains_module
        inner_lay.addWidget(custom_domains_module)
        inner_lay.addSpacing(6)

        profiles_card = self._make_profiles_card()
        self._profiles_card = profiles_card
        inner_lay.addWidget(profiles_card)
        inner_lay.addSpacing(6)

        firewall_card = self._make_firewall_card()
        self._firewall_card = firewall_card
        inner_lay.addWidget(firewall_card)
        inner_lay.addSpacing(6)
        if self._fw_expanded:
            self._fw_refresh_list()
            self._fw_ensure_installed_apps_loaded()

        QTimer.singleShot(
            0,
            lambda hc=hosts_lock_card, others=(custom_domains_module, profiles_card, self._firewall_header):
                self._sync_card_heights(hc, others),
        )

        inner_lay.addStretch()
        scroll.setWidget(inner)
        rl.addWidget(scroll, 1)


    def _make_hosts_lock_card(self) -> QWidget:
        active = HostsLockManager.is_active()
        outer = QWidget()
        outer.setStyleSheet(
            f"background: {DARK['panel_bg']}; border: 1px solid {DARK['border_faint']}; border-radius: 6px;"
        )
        h = QHBoxLayout(outer)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(10)

        icon = IconWidget(FIF.CERTIFICATE)
        icon.setFixedSize(20, 20)
        icon.setIcon(colored_svg_icon(FIF.CERTIFICATE, QColor(DARK["accent"]), sizes=(20,)))
        h.addWidget(icon, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(T("hosts_lock_title"))
        title.setStyleSheet(f"color: {DARK['fg']}; font-size: 12pt; background: transparent; border: none;")
        title_row.addWidget(title)
        title_row.addWidget(_InfoButton(T("hosts_lock_tooltip")))
        title_row.addStretch()
        text_col.addLayout(title_row)

        desc = QLabel(T("hosts_lock_desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8pt; background: transparent; border: none;")
        text_col.addWidget(desc)

        status_lbl = QLabel(T("hosts_lock_status_locked") if active else T("hosts_lock_status_unlocked"))
        status_lbl.setStyleSheet(
            f"color: {DARK['green'] if active else DARK['fg2']}; font-size: 8.5pt; background: transparent; border: none;"
        )
        text_col.addWidget(status_lbl)
        h.addLayout(text_col, 1)

        side_col = QVBoxLayout()
        side_col.setSpacing(4)
        side_col.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        btn_color = DARK["green"] if active else DARK["gray"]
        btn_label = T("hosts_lock_btn_disable") if active else T("hosts_lock_btn_enable")
        btn_icon = FIF.ACCEPT if active else FIF.CLOSE
        btn = HOTSButton(btn_icon, btn_color, "", accent=False)
        btn.setFixedWidth(44)
        attach_fluent_tip(btn, btn_label)
        side_col.addWidget(btn, 0, Qt.AlignRight)

        h.addLayout(side_col)

        state = {
            "active": active, "btn": btn, "status_lbl": status_lbl,
            "busy": False,
        }
        self._hosts_lock_state = state
        btn.clicked.connect(lambda _c=False, s=state: self._toggle_hosts_lock(s))

        return outer

    def _toggle_hosts_lock(self, state: dict):
        if state["busy"]:
            return
        target = not state["active"]
        state["busy"] = True

        btn = state["btn"]
        btn.setEnabled(False)
        self.begin_busy()

        signals = _HostsLockToggleSignals(self)
        signals.done.connect(lambda ok, s=state, t=target: self._on_hosts_lock_toggle_done(s, t, ok))
        self._hosts_lock_signals = signals

        def worker(t=target):
            try:
                ok = HostsLockManager.enable() if t else HostsLockManager.disable()
            except Exception as e:
                ok = False
                HostsLockManager.last_error = str(e)
            signals.done.emit(ok)

        start_bg_thread(worker)

    def _on_hosts_lock_toggle_done(self, state: dict, target: bool, ok: bool):
        if not shiboken6.isValid(self) or is_shutting_down():
            return
        state["busy"] = False
        btn = state["btn"]
        btn.setEnabled(True)
        self.end_busy()

        if ok:
            state["active"] = target
            self._refresh_hosts_lock_card(state)
            if self._parent_win and hasattr(self._parent_win, "_refresh_toolbar_status_ui"):
                self._parent_win._refresh_toolbar_status_ui()
            msg_key = "hosts_lock_on_ok" if target else "hosts_lock_off_ok"
            HOTSDialog.info(self, T("hosts_lock_title"), T(msg_key))
        else:
            self._refresh_hosts_lock_card(state)
            err_msg = T("hosts_lock_on_fail") if target else T("hosts_lock_off_fail")
            if HostsLockManager.last_error:
                err_msg += f"\n\n{HostsLockManager.last_error}"
            HOTSDialog.error(self, T("hosts_lock_title"), err_msg)

    def _refresh_hosts_lock_card(self, state: dict):
        active = state["active"]
        btn = state["btn"]
        attach_fluent_tip(btn, T("hosts_lock_btn_disable") if active else T("hosts_lock_btn_enable"))
        btn.set_icon(FIF.ACCEPT if active else FIF.CLOSE, DARK["green"] if active else DARK["gray"])
        btn.set_accent(False)

        status_lbl = state["status_lbl"]
        status_lbl.setText(T("hosts_lock_status_locked") if active else T("hosts_lock_status_unlocked"))
        status_lbl.setStyleSheet(
            f"color: {DARK['green'] if active else DARK['fg2']}; font-size: 8.5pt; background: transparent; border: none;"
        )

    def apply_hosts_lock_watchdog_result(self, result):
        if not shiboken6.isValid(self) or is_shutting_down():
            return
        state = getattr(self, "_hosts_lock_state", None)
        if not state or result is None:
            return
        state["active"] = HostsLockManager.is_active()
        self._refresh_hosts_lock_card(state)

        if result == "regressed":
            status_lbl = state["status_lbl"]
            status_lbl.setText(T("hosts_lock_drift_regressed"))
            status_lbl.setStyleSheet(f"color: {DARK['red']}; font-size: 8.5pt; background: transparent; border: none;")
        elif result == "restored":
            status_lbl = state["status_lbl"]
            status_lbl.setText(T("hosts_lock_drift_restored"))
            status_lbl.setStyleSheet(f"color: {DARK['green']}; font-size: 8.5pt; background: transparent; border: none;")


    def _make_custom_domains_module(self) -> QWidget:
        return self._make_card(CUSTOM_CATEGORY)


    def _make_profiles_card(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(
            f"background: {DARK['panel_bg']}; border: 1px solid {DARK['border_faint']}; border-radius: 6px;"
        )
        h = QHBoxLayout(outer)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(10)

        icon = IconWidget(FIF.FOLDER)
        icon.setFixedSize(20, 20)
        icon.setIcon(colored_svg_icon(FIF.FOLDER, QColor(DARK["accent"]), sizes=(20,)))
        h.addWidget(icon, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(T("profiles_card_title"))
        title.setStyleSheet(f"color: {DARK['fg']}; font-size: 12pt; background: transparent; border: none;")
        title_row.addWidget(title)
        title_row.addWidget(_InfoButton(T("profiles_card_tooltip")))
        title_row.addStretch()
        text_col.addLayout(title_row)

        desc = QLabel(T("profiles_card_desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8pt; background: transparent; border: none;")
        text_col.addWidget(desc)
        h.addLayout(text_col, 1)

        active = getattr(self._parent_win, "_active_profile", 1)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        for n in (1, 2, 3):
            is_active = (n == active)
            color = DARK["accent"] if is_active else DARK["gray"]
            btn = HOTSButton(FIF.FOLDER, color, str(n), accent=is_active)
            btn.fit_to_content(min_width=48)
            tip_key = "profiles_btn_tooltip_active" if is_active else "profiles_btn_tooltip"
            attach_fluent_tip(btn, T(tip_key, n=n))
            btn.clicked.connect(lambda _c=False, num=n: self._on_profile_clicked(num))
            btn_row.addWidget(btn)
        h.addLayout(btn_row)

        return outer

    def _on_profile_clicked(self, n: int):
        if hasattr(self._parent_win, "_switch_profile"):
            self._parent_win._switch_profile(n)


    def _make_firewall_card(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(
            f"background: {DARK['panel_bg']}; border: 1px solid {DARK['border_faint']}; border-radius: 6px;"
        )
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background: transparent; border: none;")
        self._firewall_header = header
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(10)

        icon = IconWidget(FIF.VPN)
        icon.setFixedSize(20, 20)
        icon.setIcon(colored_svg_icon(FIF.VPN, QColor(DARK["accent"]), sizes=(20,)))
        h.addWidget(icon, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(T("fw_card_title"))
        title.setStyleSheet(f"color: {DARK['fg']}; font-size: 12pt; background: transparent; border: none;")
        title_row.addWidget(title)
        title_row.addWidget(_InfoButton(T("fw_card_tooltip")))
        title_row.addStretch()
        text_col.addLayout(title_row)

        desc = QLabel(T("fw_card_desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8pt; background: transparent; border: none;")
        text_col.addWidget(desc)

        status_lbl = QLabel(T("fw_card_count", n=sum(1 for a in FirewallAppBlocker.list_apps() if a["blocked_out"] or a["blocked_in"])))
        status_lbl.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8.5pt; background: transparent; border: none;")
        text_col.addWidget(status_lbl)
        h.addLayout(text_col, 1)

        side_col = QVBoxLayout()
        side_col.setSpacing(4)
        side_col.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        toggle_btn = HOTSButton(
            _FW_CHEVRON_OPEN if self._fw_expanded else _FW_CHEVRON_CLOSED,
            DARK["fg2"], "", accent=False, glyph_color=DARK["accent"],
        )
        toggle_btn.setFixedWidth(44)
        attach_fluent_tip(toggle_btn, T("fw_btn_collapse") if self._fw_expanded else T("fw_btn_manage"))
        side_col.addWidget(toggle_btn, 0, Qt.AlignRight)

        h.addLayout(side_col)

        outer_lay.addWidget(header)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {DARK['border_faint']}; border: none;")
        self._firewall_sep = sep
        outer_lay.addWidget(sep)
        sep.setVisible(self._fw_expanded)

        panel = self._make_firewall_panel()
        self._firewall_panel = panel
        outer_lay.addWidget(panel)
        panel.setVisible(self._fw_expanded)

        self._firewall_state = {"status_lbl": status_lbl, "toggle_btn": toggle_btn}
        toggle_btn.clicked.connect(self._toggle_firewall_panel)

        return outer

    def _refresh_firewall_card(self):
        state = getattr(self, "_firewall_state", None)
        if not state:
            return
        lbl = state.get("status_lbl")
        if lbl is not None and shiboken6.isValid(lbl):
            lbl.setText(T("fw_card_count", n=sum(1 for a in FirewallAppBlocker.list_apps() if a["blocked_out"] or a["blocked_in"])))
        btn = state.get("toggle_btn")
        if btn is not None and shiboken6.isValid(btn):
            attach_fluent_tip(btn, T("fw_btn_collapse") if self._fw_expanded else T("fw_btn_manage"))
            btn.set_icon(
                _FW_CHEVRON_OPEN if self._fw_expanded else _FW_CHEVRON_CLOSED,
                color=DARK["fg2"], glyph_color=DARK["accent"],
            )


    def _toggle_firewall_panel(self):
        panel = getattr(self, "_firewall_panel", None)
        if panel is None or not shiboken6.isValid(panel):
            return
        self._fw_expanded = not self._fw_expanded
        panel.setVisible(self._fw_expanded)
        sep = getattr(self, "_firewall_sep", None)
        if sep is not None and shiboken6.isValid(sep):
            sep.setVisible(self._fw_expanded)
        self._refresh_firewall_card()
        if self._fw_expanded:
            self._fw_refresh_list()
            self._fw_ensure_installed_apps_loaded()

    def _make_firewall_panel(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet("background: transparent; border: none;")
        v = QVBoxLayout(outer)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        search_edit = _FwSearchEdit()
        search_edit.setPlaceholderText(T("fw_search_placeholder"))
        search_edit.setFixedHeight(34)
        search_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {DARK['bg3']}; color: {DARK['fg']}; "
            f"border: 1px solid {DARK['border']}; border-radius: 6px; padding: 0 8px; }}"
            f"QLineEdit:focus {{ border: 1px solid {DARK['accent']}; }}"
        )
        attach_line_edit_context_menu(search_edit, include=("paste", "clear"))
        completer = QCompleter([], search_edit)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        search_edit.setCompleter(completer)
        completer.activated[str].connect(self._fw_on_search_pick)
        search_edit.returnPressed.connect(lambda: self._fw_on_search_pick(search_edit.text()))
        search_row.addWidget(search_edit, 1)
        self._fw_search_edit = search_edit
        self._fw_completer = completer

        add_btn = HOTSButton(FIF.ADD, "#ffffff", T("fw_btn_add"), accent=True)
        add_btn.fit_to_content(min_width=150)
        add_btn.clicked.connect(self._fw_browse_and_add)
        search_row.addWidget(add_btn)

        v.addLayout(search_row)

        list_container = QVBoxLayout()
        list_container.setSpacing(4)
        v.addLayout(list_container)
        self._fw_list_container = list_container

        status_lbl = QLabel("")
        status_lbl.setStyleSheet(f"color: {DARK['fg2']}; font-size: 8pt; background: transparent;")
        v.addWidget(status_lbl)
        self._fw_status_lbl = status_lbl

        self._fw_op_active = 0
        return outer


    def _fw_ensure_installed_apps_loaded(self):
        if self._fw_installed_cache is not None:
            self._fw_populate_completer(self._fw_installed_cache)
            if shiboken6.isValid(self._fw_status_lbl):
                self._fw_status_lbl.setText(T("fw_status_count", n=len(FirewallAppBlocker.list_apps())))
            return
        if shiboken6.isValid(self._fw_status_lbl):
            self._fw_status_lbl.setText(T("fw_scan_working"))
        self._mark_op_start()

        signals = _FirewallScanSignals(self)
        self._toggle_signal_refs.append(signals)

        def _handle(apps):
            if signals in self._toggle_signal_refs:
                self._toggle_signal_refs.remove(signals)
            self._mark_op_end()
            if not shiboken6.isValid(self) or is_shutting_down():
                return
            self._fw_installed_cache = apps
            self._fw_populate_completer(apps)
            if shiboken6.isValid(self._fw_status_lbl):
                self._fw_status_lbl.setText(T("fw_status_count", n=len(FirewallAppBlocker.list_apps())))

        signals.done.connect(_handle)

        def worker():
            try:
                apps = scan_installed_apps()
            except Exception:
                logger.error("scan_installed_apps failed: %s", traceback.format_exc(limit=3))
                apps = []
            signals.done.emit(apps)

        start_bg_thread(worker)

    def _fw_populate_completer(self, apps):
        seen_names = {}
        for a in apps:
            seen_names.setdefault(a["name"], 0)
            seen_names[a["name"]] += 1

        label_map = {}
        for a in apps:
            if seen_names[a["name"]] > 1:
                folder = os.path.basename(os.path.dirname(a["path"])) or a["name"]
                label = f'{a["name"]} ({folder})'
            else:
                label = a["name"]
            label_map[label] = a["path"]

        self._fw_installed_map = label_map
        if shiboken6.isValid(self._fw_search_edit):
            self._fw_reset_completer_list(list(label_map.keys()))

    def _fw_reset_completer_list(self, labels):
        new_completer = QCompleter(labels, self._fw_search_edit)
        new_completer.setCaseSensitivity(Qt.CaseInsensitive)
        new_completer.setFilterMode(Qt.MatchContains)
        new_completer.setCompletionMode(QCompleter.PopupCompletion)
        new_completer.activated[str].connect(self._fw_on_search_pick)
        self._fw_search_edit.setCompleter(new_completer)
        self._fw_completer = new_completer

    def _fw_on_search_pick(self, label: str):
        path = self._fw_installed_map.get(label)
        if not path:
            return
        self._fw_add_path(path)
        QTimer.singleShot(0, self._fw_clear_search_edit)

    def _fw_clear_search_edit(self):
        edit = getattr(self, "_fw_search_edit", None)
        if edit is not None and shiboken6.isValid(edit):
            edit.clear()


    def _fw_browse_and_add(self):
        path, _ = QFileDialog.getOpenFileName(
            self, T("fw_pick_dialog_title"), "",
            f"{T('fw_filetypes_exe')} (*.exe);;{T('import_filetypes_all')} (*.*)",
        )
        if not path:
            return
        self._fw_add_path(path)

    def _fw_add_path(self, path: str):
        if FirewallAppBlocker.is_added(path):
            HOTSDialog.info(self, T("fw_card_title"), T("fw_already_added"))
            return
        ok = FirewallAppBlocker.add(path)
        if ok:
            self._fw_refresh_list()
        else:
            err = FirewallAppBlocker.last_error or T("fw_err_generic")
            HOTSDialog.error(self, T("fw_err_add_title"), f"{os.path.basename(path)}\n\n{err}")


    def _fw_refresh_list(self):
        container = getattr(self, "_fw_list_container", None)
        if container is None:
            return
        while container.count():
            item = container.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()

        apps = FirewallAppBlocker.list_apps()

        if not apps:
            empty = QLabel(T("fw_empty_list"))
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color: {DARK['fg2']}; font-size: 9pt; background: transparent; padding: 12px;")
            container.addWidget(empty)
        else:
            for i, entry in enumerate(apps):
                if i > 0:
                    container.addWidget(_fw_row_separator())
                row = _FirewallRow(
                    entry["path"], entry["name"], entry["blocked_out"], entry["blocked_in"],
                    entry["missing"], entry["removable"],
                    on_toggle=self._fw_on_row_toggle,
                    on_toggle_all=self._fw_on_row_toggle_all,
                    on_remove=lambda p: self._fw_run_op(FirewallAppBlocker.remove, p, T("fw_err_remove_title")),
                )
                container.addWidget(row)

        if shiboken6.isValid(self._fw_status_lbl):
            self._fw_status_lbl.setText(T("fw_status_count", n=len(apps)))
        self._refresh_firewall_card()
        self._fw_check_missing_once(apps)

    def _fw_check_missing_once(self, apps):
        if self._fw_missing_prompted:
            return
        self._fw_missing_prompted = True

        missing = [a for a in apps if a["missing"]]
        if not missing:
            return

        removable = [a for a in missing if a["removable"]]
        cleanable = [a for a in missing if not a["removable"]]
        if not cleanable:
            return

        shown = "\n".join(f"• {a['name']}" for a in cleanable[:10])
        if len(cleanable) > 10:
            shown += f"\n… (+{len(cleanable) - 10})"
        msg = T("fw_missing_cleanup_msg") + f"\n\n{shown}"
        if removable:
            msg += "\n\n" + T("fw_missing_cleanup_removable_note", n=len(removable))
        if HOTSDialog.ask(self, T("fw_missing_cleanup_title"), msg):
            self._fw_cleanup_missing([a["path"] for a in cleanable])

    def _fw_cleanup_missing(self, paths):
        panel = getattr(self, "_firewall_panel", None)
        if panel is not None and shiboken6.isValid(panel):
            panel.setEnabled(False)
        if shiboken6.isValid(self._fw_status_lbl):
            self._fw_status_lbl.setText(T("fw_status_working"))
        self._mark_op_start()

        signals = _FirewallOpSignals(self)
        self._toggle_signal_refs.append(signals)

        def _cleanup_and_handle(ok, _p, err_msg):
            if signals in self._toggle_signal_refs:
                self._toggle_signal_refs.remove(signals)
            self._mark_op_end()
            if is_shutting_down() or not shiboken6.isValid(self):
                return
            panel2 = getattr(self, "_firewall_panel", None)
            if panel2 is not None and shiboken6.isValid(panel2):
                panel2.setEnabled(True)
            self._fw_refresh_list()
            if err_msg:
                HOTSDialog.error(self, T("fw_err_remove_title"), err_msg)

        signals.done.connect(_cleanup_and_handle)

        def worker():
            failures = []
            for p in paths:
                try:
                    if not FirewallAppBlocker.remove(p):
                        failures.append(f"{os.path.basename(p)}: {FirewallAppBlocker.last_error}")
                except Exception:
                    failures.append(f"{os.path.basename(p)}:\n{traceback.format_exc(limit=2)}")
            signals.done.emit(not failures, "", "\n\n".join(failures))

        start_bg_thread(worker)

    def _fw_on_row_toggle(self, path: str, direction: str, blocked: bool):
        if blocked:
            self._fw_run_op(FirewallAppBlocker.unblock, path, T("fw_err_unblock_title"), direction)
        else:
            self._fw_run_op(FirewallAppBlocker.block, path, T("fw_err_block_title"), direction)

    def _fw_on_row_toggle_all(self, path: str, fully_blocked: bool):
        if fully_blocked:
            self._fw_run_op(FirewallAppBlocker.unblock_all, path, T("fw_err_unblock_title"))
        else:
            self._fw_run_op(FirewallAppBlocker.block_all, path, T("fw_err_block_title"))

    def _fw_run_op(self, func, path: str, on_fail_title: str, *extra_args):
        self._fw_op_active = getattr(self, "_fw_op_active", 0) + 1
        panel = getattr(self, "_firewall_panel", None)
        if panel is not None and shiboken6.isValid(panel):
            panel.setEnabled(False)
        if shiboken6.isValid(self._fw_status_lbl):
            self._fw_status_lbl.setText(T("fw_status_working"))
        self._mark_op_start()

        signals = _FirewallOpSignals(self)
        self._toggle_signal_refs.append(signals)

        def _cleanup_and_handle(ok, p, err_msg):
            if signals in self._toggle_signal_refs:
                self._toggle_signal_refs.remove(signals)
            if not shiboken6.isValid(self):
                return
            self._fw_on_op_done(ok, p, err_msg, on_fail_title)

        signals.done.connect(_cleanup_and_handle)

        def worker():
            try:
                ok = func(path, *extra_args)
                err_msg = "" if ok else (FirewallAppBlocker.last_error or "")
            except Exception:
                ok = False
                err_msg = traceback.format_exc(limit=3)
                logger.error("Firewall op worker exception: %s", err_msg)
            signals.done.emit(ok, path, err_msg)

        start_bg_thread(worker)

    def _fw_on_op_done(self, ok: bool, path: str, err_msg: str, on_fail_title: str):
        self._mark_op_end()
        if is_shutting_down() or not shiboken6.isValid(self):
            return
        self._fw_op_active = max(0, getattr(self, "_fw_op_active", 1) - 1)
        panel = getattr(self, "_firewall_panel", None)
        if panel is not None and shiboken6.isValid(panel):
            panel.setEnabled(True)
        self._fw_refresh_list()
        if not ok:
            err = err_msg or T("fw_err_generic")
            HOTSDialog.error(self, on_fail_title, f"{os.path.basename(path)}\n\n{err}")

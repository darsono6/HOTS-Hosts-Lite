import os

from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QColor
import shiboken6

from qfluentwidgets import FluentIcon as FIF, IconWidget

from ..constants import DARK, load_settings, save_settings, accent_rgba, custom_domains_path
from ..core import toggle_hosts_lock, HostsLimitExceeded, HostsBusyError, MAX_ACTIVE_ENTRIES
from ..core_hosts_lock import HostsLockError
from ..widgets_qt import HOTSButton, HOTSDialog, attach_fluent_tip, colored_svg_icon
from ..i18n import T
from ..bg_tasks import start_bg_thread, is_shutting_down


CUSTOM_CATEGORY = {
    "label_key": "par_cat_custom",
    "icon": "📝", "fif_icon": FIF.EDIT, "color": DARK["accent"],
    "file": "custom_domains.txt", "editable": True,
    "path": custom_domains_path(),
}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _comment_for_category(cat: dict) -> str:
    suffix = cat["file"][:-4] if cat["file"].endswith(".txt") else cat["file"]
    name_key = f"par_comment_name_{suffix}"
    name = T(name_key)
    if name == name_key:
        name = T(cat["label_key"])
    return name


class _ToggleSignals(QObject):
    done = Signal(bool, object)


def _read_custom_domains(path: str) -> list:
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line.split()[-1].lower())
    except Exception:
        pass
    return out


def _category_file_ok(cat: dict, path: str) -> bool:
    if cat.get("editable"):
        return len(_read_custom_domains(path)) > 0
    return os.path.exists(path)


class _FeatureCardMixin:

    def _make_card(self, cat: dict, active: bool = None) -> QWidget:
        list_path = cat.get("path") or os.path.join(self._bdir, cat["file"])
        is_editable = bool(cat.get("editable"))

        file_ok = _category_file_ok(cat, list_path)
        if is_editable:
            domain_count = len(_read_custom_domains(list_path))

        card = self._card_frame(cat["color"])
        info_lay = card.property("info_lay")

        title_row = QHBoxLayout()
        fif_icon = cat.get("fif_icon")
        if fif_icon is not None:
            title_row.setSpacing(10)
            ico = IconWidget(fif_icon)
            ico.setFixedSize(20, 20)
            ico.setIcon(colored_svg_icon(fif_icon, QColor(DARK["accent"]), sizes=(20,)))
            info_lay.setSpacing(2)
            info_lay.setContentsMargins(16, 14, 0, 14)
        else:
            ico = QLabel(cat["icon"])
            ico.setStyleSheet(f"color: {_hex_to_rgba(cat['color'], 0.85)}; font-size: 18px; background: transparent;")
        title_row.addWidget(ico)
        t = QLabel(T(cat["label_key"]))
        t.setStyleSheet(f"color: {DARK['fg']}; font-size: 12pt; background: transparent;")
        title_row.addWidget(t)
        if is_editable:
            title_row.addWidget(_InfoButton(T("par_custom_domains_tooltip")))
        elif cat.get("tooltip_key"):
            title_row.addWidget(_InfoButton(T(cat["tooltip_key"])))
        title_row.addStretch()
        info_lay.addLayout(title_row)

        if is_editable:
            file_txt = T("par_custom_count", n=domain_count) if file_ok else T("par_custom_empty")
            file_fg  = DARK["fg2"]
        else:
            file_txt = T("par_file_ok", file=cat["file"]) if file_ok else T("par_file_missing", file=cat["file"])
            file_fg  = DARK["fg2"] if file_ok else DARK["red"]
        fl = QLabel(file_txt)
        fl.setWordWrap(True)
        status_font_size = "8pt" if fif_icon is not None else "9pt"
        fl.setStyleSheet(f"color: {file_fg}; font-size: {status_font_size}; background: transparent;")
        if fif_icon is not None:
            fl_row = QHBoxLayout()
            fl_row.setContentsMargins(0, 0, 0, 0)
            fl_row.setSpacing(0)
            fl_row.addSpacing(30)
            fl_row.addWidget(fl)
            info_lay.addLayout(fl_row)
        else:
            info_lay.addWidget(fl)

        if active is None:
            from ..core import is_hosts_lock_active
            active = is_hosts_lock_active(tag_suffix=cat["file"])
        is_disabled = not active and not file_ok
        btn_color = DARK["green"] if active else (DARK["gray"] if not is_disabled else DARK["bg3"])
        btn_label = (T("par_btn_disable") if active
                     else (T("par_btn_enable") if not is_disabled
                           else (T("par_custom_empty_btn") if is_editable else T("par_btn_no_file"))))
        btn_icon  = FIF.ACCEPT if active else FIF.CLOSE

        btn = HOTSButton(btn_icon, btn_color, "", accent=False)
        btn.setFixedWidth(44)
        attach_fluent_tip(btn, btn_label)
        btn.setEnabled(not is_disabled)

        state = {
            "active":    active,
            "cat":       cat,
            "path":      list_path,
            "btn":       btn,
            "count_lbl": fl if is_editable else None,
        }
        self._states[cat["file"]] = state
        btn.clicked.connect(lambda _checked=False, s=state: self._toggle(s))

        btn_row = card.property("btn_lay")

        if is_editable:
            edit_btn = HOTSButton(FIF.EDIT, DARK["fg2"], "", accent=False, glyph_color=DARK["accent"])
            edit_btn.setFixedWidth(44)
            attach_fluent_tip(edit_btn, T("par_custom_edit_btn"))
            edit_btn.clicked.connect(lambda _checked=False, s=state: self._open_custom_domains_editor(s))
            btn_row.addWidget(edit_btn)

        btn_row.addWidget(btn)

        return card

    def _card_frame(self, accent_color: str, height: int = 64) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(f"background: {DARK['panel_bg']}; border: 1px solid {DARK['border_faint']}; border-radius: 6px;")
        outer.setFixedHeight(height)
        outer_lay = QHBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        info = QWidget()
        info.setStyleSheet("background: transparent; border: none;")
        info_lay = QVBoxLayout(info)
        info_lay.setContentsMargins(16, 10, 0, 10)
        info_lay.setSpacing(3)
        outer_lay.addWidget(info, 1)

        btn_area = QWidget()
        btn_area.setStyleSheet("background: transparent; border: none;")
        btn_lay = QHBoxLayout(btn_area)
        btn_lay.setContentsMargins(8, 0, 16, 0)
        btn_lay.setSpacing(8)
        btn_lay.setAlignment(Qt.AlignCenter)
        outer_lay.addWidget(btn_area)

        outer.setProperty("info_lay", info_lay)
        outer.setProperty("btn_lay",  btn_lay)
        return outer

    def _toggle(self, state: dict):
        if getattr(self, "_toggle_op_active", False):
            return

        cat    = state["cat"]
        target = not state["active"]
        path   = state["path"]

        if target and not _category_file_ok(cat, path):
            if cat.get("editable"):
                HOTSDialog.error(self, T("par_err_no_file_title"),
                                 T("par_err_no_custom_file_msg"))
            else:
                HOTSDialog.error(self, T("par_err_no_file_title"),
                                 T("par_err_no_file_msg", path=path, file=cat["file"]))
            return

        if not self._confirm_overwrite_unsaved():
            return

        self._run_toggle(state, target)

    def _refresh_custom_domains_card(self, state: dict):
        if not shiboken6.isValid(self):
            return
        path = state["path"]
        domain_count = len(_read_custom_domains(path))
        file_ok = _category_file_ok(state["cat"], path)

        lbl = state.get("count_lbl")
        if lbl is not None and shiboken6.isValid(lbl):
            lbl.setText(T("par_custom_count", n=domain_count) if file_ok else T("par_custom_empty"))

        btn = state["btn"]
        if btn is None or not shiboken6.isValid(btn):
            return
        if state["active"]:
            self._refresh_btn(state)
            return

        is_disabled = not file_ok
        btn.setEnabled(not is_disabled)
        attach_fluent_tip(btn, T("par_custom_empty_btn") if is_disabled else T("par_btn_enable"))
        btn.set_icon(FIF.CLOSE, DARK["bg3"] if is_disabled else DARK["gray"])
        btn.set_accent(False)

    def _open_custom_domains_editor(self, state: dict):
        if getattr(self, "_toggle_op_active", False):
            return
        from .custom_domains_dialog import CustomDomainsDialog
        dlg = CustomDomainsDialog(self, state["path"])
        if not dlg.exec():
            return
        if state["active"]:
            self._run_toggle(state, True, on_done=self._refresh_custom_domains_card, notify=False)
        else:
            self._refresh_custom_domains_card(state)

    def _confirm_overwrite_unsaved(self) -> bool:
        if self._parent_win and getattr(self._parent_win, "_dirty", False):
            return HOTSDialog.ask(self, T("par_dirty_warn_title"), T("par_dirty_warn_msg"))
        return True

    def _set_categories_busy(self, busy: bool):
        if busy:
            self.begin_busy()
        else:
            self.end_busy()

    def _button_should_be_enabled(self, state: dict) -> bool:
        if state["active"]:
            return True
        return _category_file_ok(state["cat"], state["path"])

    def _set_all_category_buttons_enabled(self, enabled: bool):
        for s in getattr(self, "_states", {}).values():
            btn = s.get("btn")
            if btn is not None and shiboken6.isValid(btn):
                btn.setEnabled(enabled and self._button_should_be_enabled(s))

    def _run_toggle(self, state: dict, target: bool, on_done=None, notify: bool = True):
        cat  = state["cat"]
        path = state["path"]
        comment = _comment_for_category(cat)

        if target and not os.path.exists(path):
            self._on_toggle_done(state, target, True, None, on_done=on_done, notify=notify)
            return

        self._toggle_op_active = True
        self._set_all_category_buttons_enabled(False)
        self._set_categories_busy(True)

        signals = _ToggleSignals(self)
        self._toggle_signal_refs.append(signals)

        def _cleanup_and_handle(ok, extra):
            if signals in self._toggle_signal_refs:
                self._toggle_signal_refs.remove(signals)
            if not shiboken6.isValid(self):
                return
            self._on_toggle_done(state, target, ok, extra, on_done=on_done, notify=notify)

        signals.done.connect(_cleanup_and_handle)

        def worker():
            ok = True
            extra = None
            try:
                ok = toggle_hosts_lock(target, path, tag_suffix=cat["file"],
                                             comment=comment,
                                             profile=getattr(self._parent_win, "_active_profile", 1))
            except HostsLimitExceeded as exc:
                extra = exc
                ok = False
            except HostsBusyError as exc:
                extra = str(exc)
                ok = False
            except HostsLockError as exc:
                extra = str(exc)
                ok = False
            signals.done.emit(ok, extra)

        start_bg_thread(worker)

    def _on_toggle_done(self, state: dict, target: bool, ok: bool, extra, on_done=None, notify: bool = True):
        self._finish_toggle(state, target, ok, extra, on_done=on_done, notify=notify)

    def _finish_toggle(self, state: dict, target: bool, ok: bool, extra, on_done=None, notify: bool = True):
        if not shiboken6.isValid(self):
            return
        if is_shutting_down():
            return
        self._toggle_op_active = False
        self._set_categories_busy(False)
        self._set_all_category_buttons_enabled(True)

        if isinstance(extra, HostsLimitExceeded):
            self._refresh_btn(state)
            HOTSDialog.error(self, T("save_limit_title"),
                             T("save_limit_msg", n=extra.would_be_count, max=MAX_ACTIVE_ENTRIES))
        elif ok:
            state["active"] = target
            self._refresh_btn(state)
            self._refresh_categories_count()
            if self._parent_win and hasattr(self._parent_win, "_load"):
                self._parent_win._load()
            if notify:
                msg_key = "par_success_on" if target else "par_success_off"
                HOTSDialog.info(self, T("par_success_title"),
                                T(msg_key, label=T(state["cat"]["label_key"])))
        else:
            self._refresh_btn(state)
            msg = extra if isinstance(extra, str) and extra else T("par_err_hosts_msg")
            HOTSDialog.error(self, T("par_err_hosts_title"), msg)

        if on_done is not None and shiboken6.isValid(self):
            on_done(state)

    def _refresh_categories_count(self):
        lbl = getattr(self, "_categories_count_lbl", None)
        categories = getattr(self, "_categories_list", None)
        if lbl is None or categories is None:
            return
        n_active = sum(
            1 for cat in categories
            if self._states.get(cat["file"], {}).get("active")
        )
        lbl.setText(T("par_categories_count", n=n_active, total=len(categories)))

    def _refresh_btn(self, state: dict):
        btn = state["btn"]
        if btn is None or not shiboken6.isValid(btn):
            return
        active = state["active"]
        color  = DARK["green"] if active else DARK["gray"]
        label  = T("par_btn_disable") if active else T("par_btn_enable")
        icon   = FIF.ACCEPT if active else FIF.CLOSE

        attach_fluent_tip(btn, label)
        btn.set_icon(icon, color)
        btn.set_accent(False)


class _InfoPopupBus(QObject):
    popup_closed = Signal()


info_popup_bus = _InfoPopupBus()
_open_info_popups = 0


def any_info_popup_open() -> bool:
    return _open_info_popups > 0


class _InfoButton(QWidget):

    _SIZE = 16
    _POPUP_W = 260
    _COLOR_ON  = DARK["accent"]
    _COLOR_OFF = DARK["border"]

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._popup: "QWidget | None" = None
        self._hovered = False
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._lbl = QLabel("?")
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setFixedSize(self._SIZE, self._SIZE)
        self._lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self._lbl)

        self._refresh_style()

    def _refresh_style(self):
        color = DARK["accent"] if self._hovered else DARK["fg2"]
        border = DARK["accent"] if self._hovered else self._COLOR_OFF
        self._lbl.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 600; background: transparent; "
            f"border: 1px solid {border}; border-radius: {self._SIZE // 2}px;"
        )

    def enterEvent(self, event):
        self._hovered = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        else:
            self._show_popup()
        super().mousePressEvent(event)

    def _show_popup(self):
        popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        popup.destroyed.connect(self._on_popup_destroyed)

        outer = QFrame(popup)
        outer.setObjectName("infoPopup")
        outer.setStyleSheet(
            "QFrame#infoPopup {"
            f"  background-color: {DARK['popup_bg']};"
            f"  border: 1px solid {accent_rgba(0.40)};"
            "  border-radius: 8px;"
            "}"
        )
        v = QVBoxLayout(outer)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(0)

        top_line = QFrame()
        top_line.setFixedHeight(2)
        top_line.setStyleSheet(f"background: {DARK['accent']}; border: none; border-radius: 1px;")
        v.addWidget(top_line)

        spacer = QWidget()
        spacer.setFixedHeight(8)
        spacer.setStyleSheet("background: transparent;")
        v.addWidget(spacer)

        msg = QLabel(self._text)
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        msg.setFixedWidth(self._POPUP_W - 32)
        msg.setStyleSheet(f"color: {DARK['fg']}; font-size: 9pt; background: transparent; border: none;")
        v.addWidget(msg)

        top_line.ensurePolished()
        spacer.ensurePolished()
        msg.ensurePolished()
        outer.ensurePolished()
        outer.adjustSize()
        popup.resize(outer.size())

        gpos_tr = self.mapToGlobal(self.rect().topRight())
        gpos_tl = self.mapToGlobal(self.rect().topLeft())
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        x = gpos_tr.x() + 6
        y = gpos_tr.y() - popup.height() // 2
        if x + popup.width() > screen.right():
            x = gpos_tl.x() - popup.width() - 6
        if y + popup.height() > screen.bottom():
            y = screen.bottom() - popup.height() - 4
        if y < screen.top():
            y = screen.top() + 4
        popup.move(x, y)
        popup.show()
        self._popup = popup

        global _open_info_popups
        _open_info_popups += 1

    def _on_popup_destroyed(self):
        self._popup = None

        global _open_info_popups
        _open_info_popups = max(0, _open_info_popups - 1)
        info_popup_bus.popup_closed.emit()

    def hideEvent(self, event):
        if self._popup is not None:
            self._popup.close()
        super().hideEvent(event)

    def closeEvent(self, event):
        if self._popup is not None:
            self._popup.close()
        super().closeEvent(event)

"""Main window: unlock screen, live code list, and all the lock plumbing."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_DISPLAY_NAME, __version__, autopaste, qrscan
from ..config import Settings
from ..lockwatch import SessionWatcher
from ..singleinstance import SingleInstance, instance_key, raise_to_foreground
from ..totp import InvalidSecret
from ..vault import BadPassword, CryptoError, OtpEntry, Vault, VaultFormatError
from . import icons
from .codes import CodeFilterProxy, CodeTableModel, CodeTableView, CountdownDelegate
from .dialogs import (
    ChangePasswordDialog,
    EntryDialog,
    ImportVaultDialog,
    SettingsDialog,
    choose_vault_path,
)
from .screengrab import select_region
from .styles import muted_style
from .unlock import UnlockPage

log = logging.getLogger(__name__)

REFRESH_MS = 500
COPY_MESSAGE_MS = 6000
FAILED_ATTEMPT_LOCKOUT_MS = 3000
FAILED_ATTEMPTS_BEFORE_DELAY = 3


class MainWindow(QMainWindow):
    """Owns the vault, the lock policy, and the two UI pages."""

    def __init__(
        self,
        vault: Vault,
        settings: Settings,
        path_overridden: bool = False,
        instance_guard: SingleInstance | None = None,
    ) -> None:
        super().__init__()
        self._vault = vault
        self._settings = settings
        # Held so the guard can be re-keyed when the vault path changes.
        self._instance_guard = instance_guard
        # True when --vault was passed: the location is fixed for this run.
        self._path_overridden = path_overridden
        self._failed_attempts = 0
        self._copied_text = ""
        self._quitting = False

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(620, 520)
        self.setMinimumSize(520, 420)

        self._build_pages()
        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_statusbar()
        self._build_tray()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_MS)
        self._refresh_timer.timeout.connect(self._model.refresh)

        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.timeout.connect(self._clear_clipboard)

        # Auto-paste runs in three steps (focus, paste, Enter) with a wait
        # between each. The timers belong to the window so they cannot outlive
        # it and send keystrokes into whatever is in front later on.
        self._pending_paste: tuple[str, str] | None = None
        self._paste_timer = QTimer(self)
        self._paste_timer.setSingleShot(True)
        self._paste_timer.timeout.connect(self._finish_auto_paste)
        self._enter_timer = QTimer(self)
        self._enter_timer.setSingleShot(True)
        self._enter_timer.timeout.connect(self._submit_auto_paste)

        # Samples the foreground window so a right-click can hand the code back
        # to whatever the user was working in.
        self._foreground_tracker = autopaste.ForegroundTracker(self)

        self._watcher = SessionWatcher(
            idle_seconds=settings.idle_lock_seconds,
            watch_session_lock=settings.lock_on_session_lock,
            watch_suspend=settings.lock_on_suspend,
            parent=self,
        )
        self._watcher.lockRequested.connect(self._on_lock_requested)

        self._show_locked_ui()

    # ------------------------------------------------------------------ build

    def _build_pages(self) -> None:
        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        self._unlock_page = UnlockPage(self._vault.path, self)
        self._unlock_page.unlockRequested.connect(self._on_unlock_requested)
        self._unlock_page.createRequested.connect(self._on_create_requested)
        self._unlock_page.changePathRequested.connect(self._choose_vault_location)
        self._unlock_page.importRequested.connect(self._import_existing_vault)
        self._stack.addWidget(self._unlock_page)

        vault_page = QWidget(self)
        self._vault_page = vault_page
        layout = QVBoxLayout(vault_page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search accounts…  (Ctrl+F)")
        self._search.setClearButtonEnabled(True)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._model = CodeTableModel(self)
        self._model.set_hide_codes(self._settings.hide_codes_until_hover)
        self._proxy = CodeFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._search.textChanged.connect(self._proxy.set_search_text)

        self._table = CodeTableView(vault_page)
        self._table.setModel(self._proxy)
        self._table.setItemDelegateForColumn(CodeTableModel.COL_TIMER, CountdownDelegate(self._table))
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(CodeTableModel.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(CodeTableModel.COL_CODE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(CodeTableModel.COL_TIMER, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(CodeTableModel.COL_TIMER, 96)
        self._table.copyRequested.connect(self._copy_index)
        self._table.contextMenuRequested.connect(self._show_context_menu)
        self._table.autoPasteRequested.connect(self._auto_paste)
        self._table.set_auto_paste(self._auto_paste_active())
        self._table.selectionModel().selectionChanged.connect(self._update_action_state)
        layout.addWidget(self._table, 1)

        self._empty_hint = QLabel("No codes yet — press Ctrl+N to add your first one.")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet(muted_style(self))
        layout.addWidget(self._empty_hint)

        self._stack.addWidget(vault_page)

    def _build_actions(self) -> None:
        self._action_add = QAction("&Add code…", self)
        self._action_add.setShortcut(QKeySequence.StandardKey.New)
        self._action_add.triggered.connect(lambda: self._add_entry())

        self._action_scan = QAction("Add by &scanning a QR code…", self)
        self._action_scan.setShortcut("Ctrl+Shift+N")
        self._action_scan.triggered.connect(self._scan_qr_code)
        if not qrscan.is_available():
            self._action_scan.setEnabled(False)
            self._action_scan.setToolTip(f"QR decoding is unavailable ({qrscan.unavailable_reason()})")
        else:
            self._action_scan.setToolTip(
                "Select the part of the screen showing a QR code (Ctrl+Shift+N)"
            )

        self._action_edit = QAction("&Edit code…", self)
        self._action_edit.setShortcut("Ctrl+E")
        self._action_edit.triggered.connect(self._edit_entry)

        self._action_delete = QAction("&Delete code", self)
        self._action_delete.setShortcut(QKeySequence.StandardKey.Delete)
        self._action_delete.triggered.connect(self._delete_entry)

        self._action_copy = QAction("&Copy code", self)
        self._action_copy.setShortcut(QKeySequence.StandardKey.Copy)
        self._action_copy.triggered.connect(lambda: self._copy_index(self._table.currentIndex()))

        self._action_copy_uri = QAction("Copy otpauth &URI", self)
        self._action_copy_uri.triggered.connect(self._copy_uri)

        self._action_lock = QAction("&Lock now", self)
        self._action_lock.setShortcut("Ctrl+L")
        self._action_lock.setToolTip("Lock the vault and wipe secrets from memory (Ctrl+L)")
        self._action_lock.triggered.connect(lambda: self.lock("you locked it"))

        self._action_change_password = QAction("Change master &password…", self)
        self._action_change_password.triggered.connect(self._change_password)

        self._action_export = QAction("Export encrypted &backup…", self)
        self._action_export.triggered.connect(self._export_backup)

        self._action_settings = QAction("&Settings…", self)
        self._action_settings.setShortcut(QKeySequence.StandardKey.Preferences)
        self._action_settings.triggered.connect(self._open_settings)

        self._action_find = QAction("&Find", self)
        self._action_find.setShortcut(QKeySequence.StandardKey.Find)
        self._action_find.triggered.connect(lambda: self._search.setFocus())
        self.addAction(self._action_find)

        self._action_about = QAction("&About", self)
        self._action_about.triggered.connect(self._show_about)

        self._action_quit = QAction("&Quit", self)
        self._action_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self._action_quit.triggered.connect(self._quit)

        self._vault_actions = [
            self._action_add,
            self._action_scan,
            self._action_edit,
            self._action_delete,
            self._action_copy,
            self._action_copy_uri,
            self._action_lock,
            self._action_change_password,
            self._action_export,
        ]

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.addAction(self._action_add)
        toolbar.addAction(self._action_scan)
        toolbar.addAction(self._action_edit)
        toolbar.addAction(self._action_delete)
        toolbar.addSeparator()
        toolbar.addAction(self._action_copy)
        spacer = QWidget(toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addAction(self._action_lock)
        self.addToolBar(toolbar)
        self._toolbar = toolbar

    def _build_menus(self) -> None:
        vault_menu = self.menuBar().addMenu("&Vault")
        vault_menu.addAction(self._action_add)
        vault_menu.addAction(self._action_scan)
        vault_menu.addAction(self._action_edit)
        vault_menu.addAction(self._action_delete)
        vault_menu.addSeparator()
        vault_menu.addAction(self._action_copy)
        vault_menu.addAction(self._action_copy_uri)
        vault_menu.addSeparator()
        vault_menu.addAction(self._action_change_password)
        vault_menu.addAction(self._action_export)
        vault_menu.addSeparator()
        vault_menu.addAction(self._action_lock)
        vault_menu.addAction(self._action_quit)

        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self._action_settings)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action_about)

    def _build_statusbar(self) -> None:
        self._status_label = QLabel()
        self._status_label.setStyleSheet(muted_style(self))
        self._status_label.setToolTip(str(self._vault.path))
        self.statusBar().addPermanentWidget(self._status_label)
        # Transient messages (such as "copied to the clipboard") are the point of
        # the status bar; yield the room to them instead of overlapping.
        self.statusBar().messageChanged.connect(lambda text: self._status_label.setVisible(not text))
        self.statusBar().showMessage("Locked")

    def _build_tray(self) -> None:
        self._tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable() or not self._settings.minimize_to_tray:
            return
        tray = QSystemTrayIcon(icons.app_icon(), self)
        tray.setToolTip(APP_DISPLAY_NAME)
        menu = QMenu(self)
        show_action = QAction("Show window", self)
        show_action.triggered.connect(self.activate)
        menu.addAction(show_action)
        menu.addAction(self._action_lock)
        menu.addSeparator()
        menu.addAction(self._action_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray = tray

    # ------------------------------------------------------------ lock state

    @property
    def locked(self) -> bool:
        return self._vault.locked

    def _show_locked_ui(self, message: str = "") -> None:
        self._stack.setCurrentWidget(self._unlock_page)
        self._unlock_page.reset(message)
        self._unlock_page.focus_password()
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — locked")
        self.setWindowIcon(icons.locked_icon())
        self._toolbar.setVisible(False)
        self._update_action_state()
        self._status_label.setText(f"Locked · {self._vault.path.name}")
        if self._tray is not None:
            self._tray.setToolTip(f"{APP_DISPLAY_NAME} — locked")

    def _show_unlocked_ui(self) -> None:
        self._stack.setCurrentWidget(self._vault_page)
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(icons.app_icon())
        self._toolbar.setVisible(True)
        self._reload_model()
        self._refresh_timer.start()
        self._watcher.start()
        self._sync_foreground_tracker()
        self._table.setFocus()
        if self._model.rowCount() and self._table.currentIndex().row() < 0:
            self._table.selectRow(0)
        self._update_action_state()
        backend = self._watcher.backend_name
        detail = {
            "windows-wts": "auto-lock: workstation lock + idle",
            "dbus": "auto-lock: session lock + idle",
            "none": "auto-lock: idle only",
        }.get(backend, backend)
        self._status_label.setText(f"Unlocked · {detail}")
        self._status_label.setToolTip(f"{self._vault.path}\nlock detection: {backend}")
        self.statusBar().showMessage("Vault unlocked — click a row to copy its code", 6000)
        if self._tray is not None:
            self._tray.setToolTip(f"{APP_DISPLAY_NAME} — unlocked")

    def lock(self, reason: str = "") -> None:
        """Wipe keys and secrets from memory and return to the unlock screen."""
        if self._vault.locked:
            return
        self._refresh_timer.stop()
        self._watcher.stop()
        self._cancel_pending_paste()
        self._foreground_tracker.stop()
        self._foreground_tracker.forget()
        self._clear_clipboard()
        self._search.clear()
        self._model.clear()
        self._vault.lock()
        message = f"Locked because {reason}." if reason else "Locked."
        self._show_locked_ui(message)
        if self._tray is not None and reason and not self.isActiveWindow():
            self._tray.showMessage(APP_DISPLAY_NAME, message, icons.locked_icon(), 4000)
        log.info("vault locked (%s)", reason or "manual")

    @Slot(str)
    def _on_lock_requested(self, reason: str) -> None:
        self.lock(reason)

    # ------------------------------------------------------------ unlock flow

    @Slot(str)
    def _on_unlock_requested(self, password: str) -> None:
        self._unlock_page.set_busy(True)
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._vault.unlock(password)
        except BadPassword:
            self._failed_attempts += 1
            self._unlock_page.clear_password()
            self._unlock_page.set_message("Wrong password.")
            if self._failed_attempts >= FAILED_ATTEMPTS_BEFORE_DELAY:
                self._unlock_page.set_message(
                    f"Wrong password. Waiting {FAILED_ATTEMPT_LOCKOUT_MS // 1000}s before the next try."
                )
                QTimer.singleShot(FAILED_ATTEMPT_LOCKOUT_MS, lambda: self._unlock_page.set_busy(False))
                return
        except VaultFormatError as exc:
            self._unlock_page.set_message(f"This file is not a usable vault: {exc}")
        except OSError as exc:
            self._unlock_page.set_message(f"Could not read the vault: {exc}")
        except CryptoError as exc:
            self._unlock_page.set_message(f"Could not open the vault: {exc}")
        else:
            self._failed_attempts = 0
            self._unlock_page.clear_password()
            self._show_unlocked_ui()
        finally:
            QGuiApplication.restoreOverrideCursor()
            password = ""  # drop our reference promptly
            if self._failed_attempts < FAILED_ATTEMPTS_BEFORE_DELAY:
                self._unlock_page.set_busy(False)

    @Slot(str)
    def _on_create_requested(self, password: str) -> None:
        self._unlock_page.set_busy(True)
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._vault.create(password)
        except (OSError, CryptoError, ValueError, FileExistsError) as exc:
            self._unlock_page.set_message(f"Could not create the vault: {exc}")
        else:
            self._unlock_page.clear_password()
            self._show_unlocked_ui()
            self.statusBar().showMessage(f"Created {self._vault.path}", 6000)
        finally:
            QGuiApplication.restoreOverrideCursor()
            self._unlock_page.set_busy(False)

    # ------------------------------------------------------------- entry CRUD

    def _reload_model(self) -> None:
        self._model.set_entries(self._vault.entries)
        self._empty_hint.setVisible(self._model.rowCount() == 0)
        self._update_action_state()

    def _selected_entry(self) -> OtpEntry | None:
        index = self._table.currentIndex()
        if not index.isValid():
            return None
        return self._model.entry_at(self._proxy.mapToSource(index).row())

    def _select_entry(self, entry_id: str) -> None:
        row = self._model.row_of(entry_id)
        if row < 0:
            return
        proxy_index = self._proxy.mapFromSource(self._model.index(row, CodeTableModel.COL_NAME))
        if proxy_index.isValid():
            self._table.setCurrentIndex(proxy_index)

    def _add_entry(self, prefill: OtpEntry | None = None) -> None:
        """Add a code by hand, or starting from values read off a QR code."""
        if self.locked:
            return
        dialog = EntryDialog(self, prefill=prefill)
        if dialog.exec() != EntryDialog.DialogCode.Accepted:
            return
        entry = dialog.entry()
        try:
            self._vault.add(entry)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save", f"The vault could not be written:\n{exc}")
            return
        self._reload_model()
        self._select_entry(entry.id)
        self.statusBar().showMessage(f"Added {entry.label}", 4000)

    def _scan_qr_code(self) -> None:
        """Read an account off a QR code shown anywhere on screen."""
        if self.locked or not qrscan.is_available():
            return

        # Get out of the way: the QR is usually behind this window.
        was_visible = self.isVisible()
        self.hide()
        QApplication.processEvents()
        try:
            image = select_region()
        finally:
            if was_visible:
                self.show()
                self.activate()

        if image is None or image.isNull():
            self.statusBar().showMessage("Nothing captured", 4000)
            return

        try:
            uris = qrscan.find_otpauth_uris(image)
            migration = [] if uris else qrscan.has_migration_payload(image)
        except qrscan.QrScanError as exc:
            QMessageBox.critical(self, "Could not read the screen", str(exc))
            return

        if not uris:
            self._report_no_qr_found(image, bool(migration))
            return
        if len(uris) == 1:
            self._add_scanned_uri(uris[0])
        else:
            self._add_scanned_uris(uris)

    def _report_no_qr_found(self, image, migration: bool) -> None:
        if migration:
            QMessageBox.information(
                self,
                "That is a Google Authenticator export",
                "The QR code holds a batch export (otpauth-migration://), which this app "
                "cannot read.\n\nIn Google Authenticator, use the per-account QR instead: "
                "tap an account, then Export, and scan the single code it shows.",
            )
            return
        QMessageBox.information(
            self,
            "No QR code found",
            f"Nothing readable in that {image.width()}×{image.height()} pixel area.\n\n"
            "A QR code needs a bit of room to be read: select it with a little margin "
            "around it, and if it is shown small on the page, zoom in first.",
        )

    def _add_scanned_uri(self, uri: str) -> None:
        """One code: open the add dialog prefilled, so it can be checked first."""
        try:
            scanned = OtpEntry.from_uri(uri)
        except (ValueError, InvalidSecret) as exc:
            QMessageBox.warning(
                self, "Could not use that QR code", f"The code was read, but: {exc}"
            )
            return
        self._add_entry(prefill=scanned)

    def _add_scanned_uris(self, uris: list[str]) -> None:
        """Several codes in one shot: confirm the list, then add them all."""
        scanned: list[OtpEntry] = []
        rejected: list[str] = []
        for uri in uris:
            try:
                scanned.append(OtpEntry.from_uri(uri))
            except (ValueError, InvalidSecret) as exc:
                rejected.append(str(exc))
        if not scanned:
            QMessageBox.warning(
                self, "Could not use those QR codes", "\n".join(rejected) or "No usable accounts."
            )
            return

        listing = "\n".join(f"  • {entry.label}" for entry in scanned)
        note = f"\n\n{len(rejected)} could not be read." if rejected else ""
        confirm = QMessageBox.question(
            self,
            f"Add {len(scanned)} accounts?",
            f"Found {len(scanned)} accounts in that area:\n\n{listing}{note}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        added = 0
        for entry in scanned:
            try:
                self._vault.add(entry)
                added += 1
            except OSError as exc:
                QMessageBox.critical(self, "Could not save", f"The vault could not be written:\n{exc}")
                break
        self._reload_model()
        if added:
            self._select_entry(scanned[added - 1].id)
        self.statusBar().showMessage(f"Added {added} scanned account(s)", 6000)

    def _edit_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        dialog = EntryDialog(self, entry=entry)
        if dialog.exec() != EntryDialog.DialogCode.Accepted:
            return
        updated = dialog.entry()
        try:
            self._vault.update(updated)
        except (OSError, KeyError) as exc:
            QMessageBox.critical(self, "Could not save", f"The vault could not be written:\n{exc}")
            return
        self._reload_model()
        self._select_entry(updated.id)
        self.statusBar().showMessage(f"Updated {updated.label}", 4000)

    def _delete_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete code",
            f"Delete “{entry.label}”?\n\nYou will lose access to this account unless you have "
            "another copy of the secret or its recovery codes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._vault.remove(entry.id)
        except (OSError, KeyError) as exc:
            QMessageBox.critical(self, "Could not save", f"The vault could not be written:\n{exc}")
            return
        self._reload_model()
        self.statusBar().showMessage(f"Deleted {entry.label}", 4000)

    # ---------------------------------------------------------------- copying

    def _code_at(self, index: QModelIndex) -> tuple[OtpEntry, str] | None:
        """The entry and its current code, or None if the row has neither."""
        if not index.isValid() or self.locked:
            return None
        source = self._proxy.mapToSource(index)
        code = self._model.data(source.siblingAtColumn(CodeTableModel.COL_CODE), CodeTableModel.CodeRole)
        entry = self._model.entry_at(source.row())
        if not code or code == "error" or entry is None:
            return None
        return entry, str(code)

    def _copy_index(self, index: QModelIndex) -> None:
        found = self._code_at(index)
        if found is None:
            return
        entry, code = found
        self._set_clipboard(str(code))
        seconds = self._settings.clipboard_clear_seconds
        suffix = f" · clipboard clears in {seconds}s" if seconds else ""
        self.statusBar().showMessage(
            f"Copied the code for {entry.label} to the clipboard{suffix}", COPY_MESSAGE_MS
        )
        self._watcher.note_activity()

    def _auto_paste_active(self) -> bool:
        return bool(self._settings.right_click_auto_paste) and autopaste.is_supported()

    def _sync_foreground_tracker(self) -> None:
        """Only watch the foreground while it could actually be needed."""
        if self._auto_paste_active() and not self.locked:
            self._foreground_tracker.start()
        else:
            self._foreground_tracker.stop()

    @Slot(QModelIndex)
    def _auto_paste(self, index: QModelIndex) -> None:
        """Copy the code, hand focus back to the previous window, paste it there."""
        found = self._code_at(index)
        if found is None:
            return
        entry, code = found
        self._set_clipboard(code)
        self._watcher.note_activity()

        if not autopaste.is_supported():
            self.statusBar().showMessage(
                f"Copied the code for {entry.label} — auto-paste needs Windows", COPY_MESSAGE_MS
            )
            return

        target = self._foreground_tracker.last_external_window()
        if not target:
            self.statusBar().showMessage(
                f"Copied the code for {entry.label} — no other window to paste into",
                COPY_MESSAGE_MS,
            )
            return

        title = autopaste.window_title(target) or "the previous window"
        if not autopaste.focus_window(target):
            self.statusBar().showMessage(
                f"Copied the code for {entry.label} — could not switch to {title}",
                COPY_MESSAGE_MS,
            )
            return
        # Windows needs a moment to move the foreground before keys will land.
        self._pending_paste = (entry.label, title)
        self._paste_timer.start(autopaste.FOCUS_SETTLE_MS)

    def _cancel_pending_paste(self) -> None:
        self._paste_timer.stop()
        self._enter_timer.stop()
        self._pending_paste = None

    @Slot()
    def _finish_auto_paste(self) -> None:
        if self._pending_paste is None or self.locked:
            # Locked in the meantime: whatever is in front should not be typed into.
            self._cancel_pending_paste()
            return
        label, title = self._pending_paste
        if not autopaste.send_paste():
            self._pending_paste = None
            self.statusBar().showMessage(
                f"Copied the code for {label} — could not paste into {title}", COPY_MESSAGE_MS
            )
            return
        log.info("pasted a code into %r", title)
        # Enter goes in a second step, and only now that the paste has landed:
        # submitting a form that never received the code would be worse than
        # not submitting at all.
        self._enter_timer.start(autopaste.ENTER_DELAY_MS)

    @Slot()
    def _submit_auto_paste(self) -> None:
        if self._pending_paste is None:
            return
        label, title = self._pending_paste
        self._pending_paste = None
        # Named on purpose: the user should be able to see where the code went,
        # and that this step also pressed Enter there.
        if autopaste.send_enter():
            self.statusBar().showMessage(
                f"Pasted the code for {label} into {title} and pressed Enter", COPY_MESSAGE_MS
            )
        else:
            self.statusBar().showMessage(
                f"Pasted the code for {label} into {title} — but could not press Enter",
                COPY_MESSAGE_MS,
            )

    def _copy_uri(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self._set_clipboard(entry.to_uri())
        self.statusBar().showMessage(
            f"Copied the otpauth URI for {entry.label} to the clipboard", COPY_MESSAGE_MS
        )

    def _set_clipboard(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(text)
        self._copied_text = text
        seconds = self._settings.clipboard_clear_seconds
        if seconds > 0:
            self._clipboard_timer.start(seconds * 1000)

    def _clear_clipboard(self) -> None:
        """Clear the clipboard, but only if it still holds what we put there."""
        self._clipboard_timer.stop()
        if not self._copied_text:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None and clipboard.text() == self._copied_text:
            clipboard.clear()
        self._copied_text = ""

    # ---------------------------------------------------------------- context

    def _show_context_menu(self, position) -> None:
        index = self._table.indexAt(position)
        if index.isValid():
            self._table.setCurrentIndex(index)
        menu = QMenu(self)
        menu.addAction(self._action_copy)
        menu.addAction(self._action_copy_uri)
        menu.addSeparator()
        menu.addAction(self._action_edit)
        menu.addAction(self._action_delete)
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _update_action_state(self, *_args) -> None:
        unlocked = not self.locked
        has_selection = unlocked and self._table.currentIndex().isValid()
        self._action_add.setEnabled(unlocked)
        self._action_scan.setEnabled(unlocked and qrscan.is_available())
        self._action_lock.setEnabled(unlocked)
        self._action_change_password.setEnabled(unlocked)
        self._action_export.setEnabled(unlocked and self._vault.exists)
        for action in (self._action_edit, self._action_delete, self._action_copy, self._action_copy_uri):
            action.setEnabled(bool(has_selection))

    # --------------------------------------------------------------- vault ops

    def _change_password(self) -> None:
        if self.locked:
            return
        dialog = ChangePasswordDialog(self)
        while dialog.exec() == ChangePasswordDialog.DialogCode.Accepted:
            if not self._vault.verify_password(dialog.current_password):
                dialog.set_error("That is not the current master password.")
                continue
            try:
                self._vault.change_password(dialog.new_password)
            except (OSError, CryptoError, ValueError) as exc:
                QMessageBox.critical(self, "Could not change password", str(exc))
                return
            QMessageBox.information(
                self, "Password changed", "The vault has been re-encrypted with the new password."
            )
            return

    def _export_backup(self) -> None:
        if self.locked:
            return
        suggestion = str(Path.home() / f"{self._vault.path.stem}-backup{self._vault.path.suffix}")
        target, _ = QFileDialog.getSaveFileName(self, "Export encrypted backup", suggestion, "Vault (*.otpv)")
        if not target:
            return
        try:
            self._vault.export_copy(target)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Encrypted backup written to {target}", 6000)

    # ------------------------------------------------------- vault location

    def _choose_vault_location(self) -> None:
        """First-run 'Change…' button on the create screen."""
        chosen = choose_vault_path(self, self._vault.path)
        if chosen is not None:
            self._relocate_vault(chosen)

    def _relocate_vault(self, target: Path) -> bool:
        """Point the app at `target`, moving an existing vault file there.

        Returns False (leaving everything as it was) if the user backs out or
        the move fails.
        """
        target = Path(target).expanduser()
        if target.is_dir():
            target = target / self._vault.path.name
        if target == self._vault.path:
            return True

        moving = self._vault.exists
        overwrite = False
        if target.exists():
            confirm = QMessageBox.warning(
                self,
                "Replace that file?",
                f"{target}\n\nalready exists. Replacing it destroys whatever is there — "
                "if that is another vault, its codes are gone for good.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return False
            overwrite = True

        old_path = self._vault.path
        try:
            self._vault.move_to(target, overwrite=overwrite)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Could not move the vault",
                f"The vault is still at\n{old_path}\n\n{exc}",
            )
            return False

        self._adopt_vault_path(
            f"Vault moved to {self._vault.path}"
            if moving
            else f"Vault will be created at {self._vault.path}"
        )
        log.info("vault location is now %s (moved=%s)", self._vault.path, moving)
        return True

    def _adopt_vault_path(self, status_message: str, persist: bool = True) -> None:
        """Settle everything that follows the vault living somewhere new."""
        if persist:
            # An explicit choice in the UI is meant to stick, even in a --vault run.
            self._settings.vault_path = str(self._vault.path)
            self._settings.save()
        # The single-instance key is derived from the path, so the guard has to
        # follow: otherwise it defends a vault this window no longer has open.
        if self._instance_guard is not None and not self._instance_guard.rebind(
            instance_key(self._vault.path)
        ):
            QMessageBox.warning(
                self,
                "Another copy has this vault",
                f"{self._vault.path}\n\nis already open in another window. Two copies of "
                "one vault can overwrite each other's changes — close one of them.",
            )
        self._unlock_page.set_vault_path(self._vault.path)
        self._status_label.setToolTip(str(self._vault.path))
        if self.locked:
            self._status_label.setText(f"Locked · {self._vault.path.name}")
        self.statusBar().showMessage(status_message, 8000)

    def _import_existing_vault(self) -> None:
        """First-run import: adopt a vault file the user already has."""
        dialog = ImportVaultDialog(self._vault.path, self)
        if dialog.exec() != ImportVaultDialog.DialogCode.Accepted:
            return
        source = dialog.source_path
        if source is None:
            return

        copying = dialog.copy_into_place
        try:
            if copying:
                self._vault.import_from(source)
            else:
                self._vault.point_at(source)
        except (OSError, CryptoError, ValueError, FileExistsError) as exc:
            QMessageBox.critical(
                self,
                "Could not import that vault",
                f"{source}\n\n{exc}\n\nNothing has been changed.",
            )
            return

        if copying:
            message = f"Imported {source.name} — unlock it with its own master password"
        else:
            message = f"Opened {self._vault.path} — unlock it with its own master password"
        # Copying leaves the location alone, so there is nothing new to persist.
        self._adopt_vault_path(message, persist=not copying)
        self._unlock_page.set_message(
            "Vault imported. Unlock it with the master password it already had.", error=False
        )
        self._unlock_page.focus_password()
        log.info("imported a vault from %s (copied=%s)", source, copying)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self._settings, self._vault.path, self, path_overridden=self._path_overridden
        )
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        if dialog.vault_path != self._vault.path and not self._relocate_vault(dialog.vault_path):
            return  # keep the old location and the old settings together
        updated = dialog.result_settings(self._settings)
        # _relocate_vault owns vault_path. Taking it from there rather than from
        # the dialog keeps a --vault run from writing its one-off path into the
        # settings, and keeps an unset path unset (= follow the platform default).
        updated.vault_path = self._settings.vault_path
        self._settings = updated
        self._settings.save()
        self._watcher.set_idle_seconds(self._settings.idle_lock_seconds)
        self._watcher.set_watch_session_lock(self._settings.lock_on_session_lock)
        self._watcher.set_watch_suspend(self._settings.lock_on_suspend)
        self._model.set_hide_codes(self._settings.hide_codes_until_hover)
        self._table.set_auto_paste(self._auto_paste_active())
        self._sync_foreground_tracker()
        if self._settings.minimize_to_tray and self._tray is None:
            self._build_tray()
        elif not self._settings.minimize_to_tray and self._tray is not None:
            self._tray.hide()
            self._tray = None
        if self._settings.clipboard_clear_seconds == 0:
            self._clipboard_timer.stop()

    def _show_about(self) -> None:
        # Built by hand rather than with QMessageBox.about(), which always uses
        # the window icon: this one shows the larger About artwork instead.
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_DISPLAY_NAME}")
        box.setIconPixmap(icons.about_pixmap(ratio=self.devicePixelRatioF()))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<b>{APP_DISPLAY_NAME} {__version__}</b>"
            "<p>TOTP codes kept in a single file encrypted with AES-256-GCM, "
            "keyed from your master password with scrypt.</p>"
            f"<p>Vault: {self._vault.path}<br>"
            f"Lock detection: {self._watcher.backend_name}</p>"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    # ---------------------------------------------------------------- window

    @Slot()
    def activate(self) -> None:
        """Bring the window forward and give it focus.

        Called from the tray, and by a second launch that found this instance
        already running.
        """
        raise_to_foreground(self)
        if self.locked:
            self._unlock_page.focus_password()
        else:
            self._table.setFocus()
        self._watcher.note_activity()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.activate()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self._settings.lock_on_minimize
        ):
            self.lock("the window was minimized")
        super().changeEvent(event)

    def _quit(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._tray is not None and not self._quitting:
            event.ignore()
            self.hide()
            self._tray.showMessage(
                APP_DISPLAY_NAME,
                "Still running in the tray. Use Quit to exit.",
                icons.app_icon(),
                3000,
            )
            return
        self._refresh_timer.stop()
        self._clear_clipboard()
        self._cancel_pending_paste()
        self._foreground_tracker.stop()
        self._watcher.shutdown()
        if not self._vault.locked:
            self._vault.lock()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
        QApplication.quit()

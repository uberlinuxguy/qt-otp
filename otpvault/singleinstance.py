"""One running copy per vault, with activation of the existing window.

The first process to start listens on a local socket (a named pipe on Windows).
A later process finds that socket, asks the running copy to come to the front,
and exits without opening a second window.

The socket name is derived from the user and the vault path, so two instances
can still work on two *different* vaults — while a second copy of the *same*
vault, the case that can actually lose data (both hold the whole vault in
memory and each save rewrites the file), is refused.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

ACTIVATE_MESSAGE = b"activate"
ACK_MESSAGE = b"ok"
CONNECT_TIMEOUT_MS = 1500
# Generous: the running copy may be busy deriving a key when the request lands.
ACK_TIMEOUT_MS = 3000


def instance_key(vault_path: Path | str) -> str:
    """A socket name unique to this user and vault.

    Hashed because socket names have length limits and a restricted character
    set, and because a path is nobody else's business: on Windows the pipe
    namespace is machine-wide and readable by other sessions.
    """
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - unusual environments have no login name
        user = "unknown"
    # resolve() collapses '..' and symlinks so the same vault written two ways
    # lands on one key; normcase folds case where the filesystem ignores it.
    resolved = Path(vault_path).expanduser().resolve()
    path = os.path.normcase(str(resolved))
    digest = hashlib.sha256(f"{user}|{path}".encode("utf-8")).hexdigest()
    return f"qt-otp-{digest[:32]}"


def allow_foreground_handoff() -> None:
    """Let the running copy take the foreground from us.

    Windows only lets the foreground process hand focus over; without this the
    other process's SetForegroundWindow is downgraded to a taskbar flash.
    Called by the *second* process, which has the foreground, just before it
    asks the first one to come forward.
    """
    if sys.platform != "win32":
        return
    import ctypes

    ASFW_ANY = 0xFFFFFFFF
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:  # noqa: BLE001 - cosmetic; the worst case is a flashing taskbar button
        log.debug("AllowSetForegroundWindow failed", exc_info=True)


def raise_to_foreground(window) -> None:
    """Show, unminimize and focus `window`, as far as the OS allows."""
    if window.isMinimized():
        window.showNormal()  # not show(): that would drop a maximized window
    elif not window.isVisible():
        window.show()
    window.raise_()
    window.activateWindow()

    if sys.platform != "win32":
        return
    import ctypes

    try:
        hwnd = int(window.winId())
        if not ctypes.windll.user32.SetForegroundWindow(hwnd):
            # Focus was refused (no handoff granted); flash instead of failing silently.
            from PySide6.QtWidgets import QApplication

            QApplication.alert(window)
    except Exception:  # noqa: BLE001
        log.debug("SetForegroundWindow failed", exc_info=True)


class SingleInstance(QObject):
    """Guards a key, and relays activation requests from later processes."""

    activateRequested = Signal()

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        self._mutex = None  # Windows named-mutex handle, while we hold the key

    @property
    def key(self) -> str:
        return self._key

    @property
    def is_primary(self) -> bool:
        return self._server is not None

    # ------------------------------------------------------------- acquiring

    def try_acquire(self) -> bool:
        """Become the one running instance.

        Returns False when another process already holds the key. A socket left
        behind by a crash is cleaned up and taken over rather than blocking
        startup forever.

        Note that listening is *not* the exclusion test: on Windows a second
        QLocalServer can listen on a name already in use, because named pipes
        allow multiple instances. So the order is: ask whether anyone answers,
        then take an OS-level lock, and only then listen.
        """
        if self._existing_instance_responds():
            return False
        if not self._claim_process_lock():
            return False

        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._key):
            log.info("removing a socket left behind by an earlier run: %s", self._key)
            QLocalServer.removeServer(self._key)
            if not server.listen(self._key):
                # No activation channel. Drop the lock so later launches still
                # start, and run without enforcement rather than refusing to.
                log.warning("could not listen on %s: %s", self._key, server.errorString())
                server.setParent(None)
                server.deleteLater()
                self._release_process_lock()
                return True

        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server.setParent(None)
            self._server.deleteLater()
            self._server = None
        self._release_process_lock()

    # ------------------------------------------------------- the OS-level lock

    def _claim_process_lock(self) -> bool:
        """Win the key even against a simultaneous launch.

        A named mutex settles the race that the connect-then-listen probe
        cannot: two processes starting at the same moment would both find
        nobody listening. Elsewhere the probe plus an exclusive socket file is
        enough, so this is a no-op.
        """
        if sys.platform != "win32":
            return True
        import ctypes
        from ctypes import wintypes

        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        # Local\ keeps the name inside this logon session, where it belongs.
        handle = kernel32.CreateMutexW(None, False, f"Local\\{self._key}")
        if not handle:
            log.warning("could not create the instance mutex: %s", ctypes.get_last_error())
            return True  # do not let a lock failure stop the app from starting
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._mutex = handle
        return True

    def _release_process_lock(self) -> None:
        if getattr(self, "_mutex", None) is None:
            return
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._mutex)
        self._mutex = None

    def _existing_instance_responds(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        connected = socket.waitForConnected(CONNECT_TIMEOUT_MS)
        socket.abort()
        return connected

    # ------------------------------------------------------------- notifying

    def notify_existing(self) -> bool:
        """Ask the running copy to come to the front.

        False means nobody answered, or something is holding the key without
        servicing it — a wedged process, say. The caller can then tell the user
        rather than silently doing nothing.
        """
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            log.info("no instance answered on %s: %s", self._key, socket.errorString())
            return False
        try:
            allow_foreground_handoff()
            socket.write(ACTIVATE_MESSAGE)
            socket.flush()
            # No point interrogating waitForBytesWritten: on a Windows named
            # pipe it reports on a queue that may already have been handed to
            # the OS. The reply is the only honest proof — it means the other
            # event loop is alive and has handled the request, not merely that
            # a pipe exists.
            if not socket.waitForReadyRead(ACK_TIMEOUT_MS):
                log.warning("an instance holds the key but did not acknowledge the request")
                return False
            reply = bytes(socket.readAll().data())
            if ACK_MESSAGE.strip() not in reply:
                log.warning("unexpected reply from the running instance: %r", reply[:64])
                return False
            return True
        finally:
            socket.abort()

    # -------------------------------------------------------------- incoming

    def _on_new_connection(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            if connection is None:
                break
            connection.readyRead.connect(lambda c=connection: self._on_ready_read(c))
            connection.disconnected.connect(connection.deleteLater)
            # A process that connects and says nothing still means "come forward".
            if connection.bytesAvailable():
                self._on_ready_read(connection)

    def _on_ready_read(self, connection: QLocalSocket) -> None:
        payload = bytes(connection.readAll().data())
        if payload and ACTIVATE_MESSAGE.strip() not in payload:
            log.debug("ignoring an unrecognised message: %r", payload[:64])
            return
        log.info("another launch asked us to come to the front")
        self.activateRequested.emit()
        # Acknowledge only after handling, so the reply proves we acted.
        connection.write(ACK_MESSAGE)
        connection.flush()
        connection.disconnectFromServer()


__all__ = ["SingleInstance", "instance_key", "raise_to_foreground"]

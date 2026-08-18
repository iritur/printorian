"""Bambu LAN transports: MQTT for control, implicit FTPS for plates.

Both carry a correction that a real machine forced on us in Phase 0, and both are
written so the mistake cannot be made again silently:

* **MQTT** is TLS on 8883 with a self-signed certificate, username ``bblp`` and the
  LAN access code as the password.
* **FTPS** is *implicit* TLS on 990, and the printer's vsftpd requires
  ``require_ssl_reuse`` — the data connection must resume the control connection's
  TLS session. Without that, every listing comes back empty and every upload fails
  with ``553 Could not create file``, a message that points at permissions when the
  cause is the handshake.
"""

from __future__ import annotations

import contextlib
import ftplib
import io
import ssl
from pathlib import PurePosixPath
from typing import Any

from printorian.drivers.base import DriverStorageError, DriverUnavailableError

#: Everything ftplib can raise, plus socket errors, as one catchable tuple.
FTP_ERRORS: tuple[type[BaseException], ...] = (OSError, *ftplib.all_errors)

MQTT_PORT = 8883
FTPS_PORT = 990
USERNAME = "bblp"

#: Where sliced plates live on the machine's storage.
PLATE_DIRECTORY = "/cache"


def lan_tls_context() -> ssl.SSLContext:
    """TLS that accepts the printer's self-signed certificate.

    Verification is off by design: the machine presents a certificate nobody has
    signed, and the LAN access code is the authentication factor. This is scoped to
    printer connections and must never be reused for anything reachable off the LAN.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS speaking implicit TLS, with data-channel session reuse."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._wrapped_socket: Any = None
        super().__init__(*args, **kwargs)

    @property
    def sock(self) -> Any:
        return self._wrapped_socket

    @sock.setter
    def sock(self, value: Any) -> None:
        # Implicit TLS: the socket is encrypted from the first byte, before any
        # command is sent. The stdlib class only does explicit (AUTH TLS) mode.
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._wrapped_socket = value

    def ntransfercmd(self, cmd: str, rest: Any = None) -> tuple[Any, Any]:
        """Resume the control session on the data connection (vsftpd requirement)."""
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if getattr(self, "_prot_p", False):
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=self.sock.session
            )
        return conn, size


def connect_ftps(host: str, access_code: str, *, timeout: int = 20) -> ImplicitFTPS:
    """Open an authenticated, protected FTPS connection."""
    ftps = ImplicitFTPS(context=lan_tls_context())
    try:
        ftps.connect(host=host, port=FTPS_PORT, timeout=timeout)
        ftps.login(USERNAME, access_code)
        ftps.prot_p()
    except FTP_ERRORS as exc:
        raise DriverUnavailableError(
            "error.driver.unavailable", printer=host, detail=str(exc)
        ) from exc
    return ftps


def upload_plate(ftps: ImplicitFTPS, filename: str, content: bytes) -> str:
    """Store a plate and return its remote path.

    A refusal is reported as a **storage** failure, not a generic rejection: on real
    hardware with no card inserted the connection authenticates, directories list
    cleanly, and only the write fails. The remedy is physical, and an operator needs
    to be told which machine to walk to.
    """
    remote = str(PurePosixPath(PLATE_DIRECTORY) / filename)
    _ensure_directory(ftps, PLATE_DIRECTORY)
    try:
        ftps.storbinary(f"STOR {remote}", io.BytesIO(content))
    except FTP_ERRORS as exc:
        raise DriverStorageError(
            "error.driver.storage_unavailable", path=remote, detail=str(exc)
        ) from exc
    return remote


def _ensure_directory(ftps: ImplicitFTPS, path: str) -> None:
    try:
        ftps.cwd(path)
    except FTP_ERRORS:
        try:
            ftps.mkd(path)
        except FTP_ERRORS as exc:
            raise DriverStorageError(
                "error.driver.storage_unavailable", path=path, detail=str(exc)
            ) from exc
    finally:
        with contextlib.suppress(*FTP_ERRORS):
            ftps.cwd("/")


def close_ftps(ftps: ImplicitFTPS) -> None:
    with contextlib.suppress(*FTP_ERRORS):
        ftps.quit()

"""FTPS transfer to a Bambu printer, and the diagnostics for when it refuses.

Split out of ``bambu_spike.py`` to keep that file under the 400-line gate.

Two things about this protocol bite immediately:

* Port 990 is **implicit** TLS — the socket is wrapped from the first byte. The
  stdlib ``FTP_TLS`` only does explicit TLS (plaintext greeting, then ``AUTH TLS``),
  so the socket has to be wrapped before any command is sent.
* The writable root is the printer's storage (a microSD on most models). If no card
  is mounted, or the target directory does not exist, ``STOR`` fails with
  ``553 Could not create file`` — which reads like a permissions problem and is
  usually a missing-directory or missing-card problem.
"""

from __future__ import annotations

import contextlib
import ftplib
import io
import ssl
from pathlib import Path
from typing import Any

FTPS_PORT = 990
USERNAME = "bblp"

#: Where sliced plates are expected to live, most likely first.
CANDIDATE_DIRS = ("/cache", "cache", "/")


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS speaking implicit TLS, with TLS session reuse on data channels."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._wrapped_socket: Any = None
        super().__init__(*args, **kwargs)

    @property  # type: ignore[misc]
    def sock(self) -> Any:
        return self._wrapped_socket

    @sock.setter
    def sock(self, value: Any) -> None:
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._wrapped_socket = value

    def ntransfercmd(self, cmd: str, rest: Any = None) -> tuple[Any, Any]:
        """Resume the control connection's TLS session on the data connection.

        The printer runs vsftpd, which defaults to ``require_ssl_reuse=YES``: a data
        connection that negotiates a *fresh* TLS session is rejected. ``ftplib`` does
        not reuse sessions, so without this every ``LIST`` comes back empty and every
        ``STOR`` fails with ``553 Could not create file`` — a message that points at
        permissions when the real cause is the TLS handshake.
        """
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=self.sock.session
            )
        return conn, size


def connect(host: str, code: str, *, timeout: int = 20) -> ImplicitFTPS:
    """Open an authenticated, protected connection."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    ftps = ImplicitFTPS(context=context)
    ftps.connect(host=host, port=FTPS_PORT, timeout=timeout)
    ftps.login(USERNAME, code)
    ftps.prot_p()
    return ftps


def listdir(ftps: ImplicitFTPS, path: str) -> tuple[list[str], str | None]:
    """Entries under ``path``, plus the error if listing failed.

    The error is returned rather than swallowed: an empty directory and a broken
    data channel are completely different problems, and collapsing both to ``[]``
    sends you looking for a missing SD card when the real fault is TLS.
    """
    try:
        return sorted(ftps.nlst(path)), None
    except ftplib.all_errors as exc:
        return [], str(exc)


#: Places a plate might live. Deliberately broad: newer machines have internal
#: storage instead of a card, and the login directory is not always the root.
PROBE_PATHS = (
    "/",
    "/cache",
    "/model",
    "/data",
    "/sdcard",
    "/mnt",
    "/mnt/sdcard",
    "/mnt/emmc",
    "/internal",
    "/storage",
    "/upload",
    "/gcodes",
    "/timelapse",
    "/logger",
    "/image",
)


def server_info(ftps: ImplicitFTPS) -> dict[str, str]:
    """Where the login actually lands, and what the server admits to supporting.

    ``PWD`` is the most informative single call: if the account is chrooted to a
    storage device that is not mounted, the working directory reveals it far faster
    than probing paths one at a time.
    """
    info: dict[str, str] = {"welcome": ftps.getwelcome() or ""}
    for label, call in (("pwd", ftps.pwd), ("system", ftps.sendcmd)):
        try:
            info[label] = call("SYST") if label == "system" else call()  # type: ignore[operator]
        except ftplib.all_errors as exc:
            info[label] = f"ERROR: {exc}"
    try:
        info["features"] = " | ".join(line.strip() for line in ftps.sendcmd("FEAT").splitlines())
    except ftplib.all_errors as exc:
        info["features"] = f"ERROR: {exc}"
    return info


def probe_writable(ftps: ImplicitFTPS, path: str) -> str | None:
    """Try to write a tiny file to ``path``. Returns the error, or None on success.

    Writability is what actually matters — a directory that lists fine can still
    refuse ``STOR``, which is exactly the failure this spike hit.
    """
    probe = f"{path.rstrip('/')}/.printorian-probe" if path != "/" else "/.printorian-probe"
    try:
        ftps.storbinary(f"STOR {probe}", io.BytesIO(b"probe"))
    except ftplib.all_errors as exc:
        return str(exc)
    with contextlib.suppress(*ftplib.all_errors):
        ftps.delete(probe)
    return None


def survey(ftps: ImplicitFTPS) -> dict[str, tuple[list[str], str | None]]:
    """Look at the places a plate could go, so a 553 can be explained."""
    return {path: listdir(ftps, path) for path in PROBE_PATHS}


def raw_listing(ftps: ImplicitFTPS, path: str = "/") -> tuple[list[str], str | None]:
    """Long-form ``LIST`` rather than ``NLST``.

    Some vsftpd configurations answer ``NLST`` with an empty body while ``LIST``
    returns the real directory contents, so an empty ``NLST`` is not proof that a
    directory is empty.
    """
    lines: list[str] = []
    try:
        ftps.retrlines(f"LIST {path}", lines.append)
    except ftplib.all_errors as exc:
        return lines, str(exc)
    return lines, None


def directory_exists(ftps: ImplicitFTPS, path: str) -> str:
    """Ask the server directly whether it can change into ``path``."""
    try:
        ftps.cwd(path)
    except ftplib.all_errors as exc:
        return f"NO ({exc})"
    finally:
        with contextlib.suppress(*ftplib.all_errors):
            ftps.cwd("/")
    return "yes"


def ensure_dir(ftps: ImplicitFTPS, path: str) -> bool:
    """Create ``path`` if it is missing. False when it cannot be created."""
    if path in ("/", ""):
        return True
    try:
        ftps.cwd(path)
        ftps.cwd("/")
    except ftplib.all_errors:
        try:
            ftps.mkd(path)
        except ftplib.all_errors:
            return False
    return True


def upload(ftps: ImplicitFTPS, source: Path, target_dir: str) -> str:
    """Store ``source`` under ``target_dir`` and return the remote path."""
    remote = f"{target_dir.rstrip('/')}/{source.name}" if target_dir != "/" else f"/{source.name}"
    with source.open("rb") as handle:
        ftps.storbinary(f"STOR {remote}", handle)
    return remote


def upload_anywhere(ftps: ImplicitFTPS, source: Path) -> tuple[str | None, list[str]]:
    """Try each candidate directory in turn.

    Returns the remote path that worked (or ``None``) plus one line per attempt, so
    a failure reports what was tried rather than just the last error.
    """
    attempts: list[str] = []
    for target in CANDIDATE_DIRS:
        if not ensure_dir(ftps, target):
            attempts.append(f"  {target:<10} directory missing and could not be created")
            continue
        try:
            remote = upload(ftps, source, target)
        except ftplib.all_errors as exc:
            attempts.append(f"  {target:<10} {exc}")
            continue
        attempts.append(f"  {target:<10} OK -> {remote}")
        return remote, attempts
    return None, attempts

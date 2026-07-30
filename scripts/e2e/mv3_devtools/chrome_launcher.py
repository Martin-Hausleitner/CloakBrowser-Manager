"""Launch disposable Chromium with the unpacked cloak-profile-sync extension."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.e2e.mv3_devtools import EXTENSION_ID
from scripts.e2e.mv3_devtools.redaction import redact_home

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXTENSION_DIR = REPO_ROOT / "extensions" / "cloak-profile-sync"


def free_loopback_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def free_display(start: int = 340, end: int = 520) -> str:
    for number in range(start, end):
        if not os.path.exists(f"/tmp/.X{number}-lock"):
            return f":{number}"
    raise RuntimeError("no free X display for Xvfb")


def resolve_chromium_binary(explicit: str | Path | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"chromium binary not found: {path}")
        return path

    env = os.environ.get("CBM_MV3_CHROMIUM_BINARY") or os.environ.get("CHROMIUM_BINARY")
    if env:
        path = Path(env).expanduser().resolve()
        if path.is_file():
            return path

    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        candidates = sorted(
            cache.glob("chromium-*/chrome-linux*/chrome"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0].resolve()

    for name in ("chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()

    # Google Chrome branded builds ignore --load-extension; keep as last resort.
    for name in ("google-chrome-stable", "google-chrome"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()

    raise FileNotFoundError(
        "No Chromium binary found. Install Chromium or set CBM_MV3_CHROMIUM_BINARY."
    )


def build_chromium_args(
    *,
    user_data_dir: Path,
    extension_dir: Path,
    debugging_port: int,
    extra_args: list[str] | None = None,
    start_url: str = "about:blank",
) -> list[str]:
    if debugging_port < 1024 or debugging_port > 65535:
        raise ValueError("debugging port must be unprivileged")
    extension_dir = Path(extension_dir).resolve()
    if not (extension_dir / "manifest.json").is_file():
        raise FileNotFoundError(f"extension manifest missing under {extension_dir}")
    user_data_dir = Path(user_data_dir).resolve()
    args = [
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={debugging_port}",
        "--remote-allow-origins=*",
        # Google Chrome 137+ and modern Chromium may disable --load-extension by default.
        "--disable-features=DisableLoadExtensionCommandLineSwitch",
        f"--load-extension={extension_dir}",
        f"--disable-extensions-except={extension_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-popup-blocking",
        "--password-store=basic",
        "--disable-gpu",
        "--no-sandbox",
        start_url,
    ]
    if extra_args:
        args[0:0] = list(extra_args)
    return args


@dataclass
class DisposableChromium:
    binary: Path
    user_data_dir: Path
    extension_dir: Path
    debugging_port: int
    process: subprocess.Popen[bytes]
    xvfb_process: subprocess.Popen[bytes] | None = None
    display: str | None = None
    owned_user_data_dir: bool = True
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def browser_ws_discovery_url(self) -> str:
        return f"http://127.0.0.1:{self.debugging_port}/json/version"

    @property
    def extension_id(self) -> str:
        return EXTENSION_ID

    def wait_until_ready(self, timeout_seconds: float = 30.0) -> dict[str, Any]:
        deadline = time.time() + max(1.0, timeout_seconds)
        last_error: Exception | None = None
        while time.time() < deadline:
            if self.process.poll() is not None:
                stderr = ""
                if self.process.stderr:
                    stderr = self.process.stderr.read().decode("utf-8", "replace")[-800:]
                raise RuntimeError(
                    f"chromium exited early code={self.process.returncode}: {redact_home(stderr)}"
                )
            try:
                with urllib.request.urlopen(self.browser_ws_discovery_url, timeout=1) as resp:
                    import json

                    payload = json.load(resp)
                if isinstance(payload, dict) and payload.get("webSocketDebuggerUrl"):
                    return payload
            except Exception as exc:  # noqa: BLE001 - readiness poll.
                last_error = exc
                time.sleep(0.2)
        raise TimeoutError(
            f"chromium CDP not ready on port {self.debugging_port}: {last_error!r}"
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.xvfb_process and self.xvfb_process.poll() is None:
            self.xvfb_process.terminate()
            try:
                self.xvfb_process.wait(timeout=3)
            except Exception:
                self.xvfb_process.kill()
        if self.owned_user_data_dir:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)

    def __enter__(self) -> DisposableChromium:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def launch_disposable_chromium(
    *,
    extension_dir: Path | None = None,
    binary: str | Path | None = None,
    start_url: str = "about:blank",
    headless: bool = False,
    ready_timeout_seconds: float = 30.0,
) -> DisposableChromium:
    extension = Path(extension_dir or DEFAULT_EXTENSION_DIR).resolve()
    chromium = resolve_chromium_binary(binary)
    user_data_dir = Path(tempfile.mkdtemp(prefix="cbm-mv3-devtools-e2e-"))
    debugging_port = free_loopback_port()

    xvfb_process = None
    display = None
    env = os.environ.copy()
    if not headless:
        display = free_display()
        xvfb_process = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1280x800x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.35)
        env["DISPLAY"] = display

    args = build_chromium_args(
        user_data_dir=user_data_dir,
        extension_dir=extension,
        debugging_port=debugging_port,
        start_url=start_url,
    )
    if headless:
        # Prefer new headless; extension support varies by build.
        args.insert(0, "--headless=new")

    process = subprocess.Popen(
        [str(chromium), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
    )
    session = DisposableChromium(
        binary=chromium,
        user_data_dir=user_data_dir,
        extension_dir=extension,
        debugging_port=debugging_port,
        process=process,
        xvfb_process=xvfb_process,
        display=display,
        owned_user_data_dir=True,
    )
    try:
        session.wait_until_ready(timeout_seconds=ready_timeout_seconds)
    except Exception:
        session.close()
        raise
    return session

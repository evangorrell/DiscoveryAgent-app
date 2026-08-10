"""Stable local production-process contract for the DiscoveryAgent frontend."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Sequence, TextIO

from werkzeug.serving import BaseWSGIServer, make_server

from .app import app as application


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
STARTUP_PREFIX = "DISCOVERYAGENT_FRONTEND_STARTUP="
ERROR_PREFIX = "DISCOVERYAGENT_FRONTEND_ERROR="


@dataclass(frozen=True)
class LaunchConfig:
    host: str
    port: int


def _port_number(raw: str | int) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def parse_launch_config(
    argv: Sequence[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> LaunchConfig:
    """Parse the narrow manager-to-frontend launch contract."""
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        prog="python -m da_selection_ui",
        description="Start the local DiscoveryAgent frontend.",
    )
    parser.add_argument(
        "--host",
        default=env.get("DISCOVERY_AGENT_HOST", DEFAULT_HOST),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--port",
        default=env.get("DISCOVERY_AGENT_PORT", str(DEFAULT_PORT)),
        type=_port_number,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    host = str(args.host or "").strip()
    if not host:
        parser.error("host must not be empty")
    return LaunchConfig(host=host, port=args.port)


def _url_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::"}:
        return DEFAULT_HOST
    if ":" in bind_host and not bind_host.startswith("["):
        return f"[{bind_host}]"
    return bind_host


class ProductionFrontendServer:
    """Own one threaded loopback WSGI server and its shutdown lifecycle."""

    def __init__(
        self,
        config: LaunchConfig,
        *,
        app=application,
        server_factory: Callable[..., BaseWSGIServer] | None = None,
        install_signal_handlers: bool = True,
    ) -> None:
        app.debug = False
        app.config["DEBUG"] = False
        app.config["TESTING"] = False
        factory = make_server if server_factory is None else server_factory
        self._server = factory(
            config.host,
            config.port,
            app,
            threaded=True,
            passthrough_errors=False,
        )
        self.host = config.host
        self.port = int(self._server.server_port)
        self._install_signal_handlers = install_signal_handlers
        self._shutdown_requested = threading.Event()

    @property
    def readiness_url(self) -> str:
        return f"http://{_url_host(self.host)}:{self.port}/api/health"

    def startup_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "host": self.host,
            "port": self.port,
            "url": f"http://{_url_host(self.host)}:{self.port}/",
            "readiness_url": self.readiness_url,
            "pid": os.getpid(),
        }

    def shutdown(self) -> None:
        """Stop the server; callers must invoke this from outside its serve thread."""
        self._server.shutdown()

    def _shutdown_coordinator(self) -> None:
        self._shutdown_requested.wait()
        self.shutdown()

    def serve_forever(self) -> None:
        """Serve until Ctrl-C, SIGTERM, or a programmatic shutdown request."""
        previous_sigterm = None
        signal_handler_installed = (
            self._install_signal_handlers
            and threading.current_thread() is threading.main_thread()
            and hasattr(signal, "SIGTERM")
        )
        if signal_handler_installed:
            previous_sigterm = signal.getsignal(signal.SIGTERM)

            def request_shutdown(_signum, _frame):
                self._shutdown_requested.set()

            signal.signal(signal.SIGTERM, request_shutdown)

        coordinator = threading.Thread(
            target=self._shutdown_coordinator,
            daemon=True,
            name="frontend-shutdown",
        )
        coordinator.start()
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            # Werkzeug normally handles Ctrl-C itself. This also covers test or
            # alternate server implementations that allow it to propagate.
            pass
        finally:
            self._shutdown_requested.set()
            self._server.server_close()
            coordinator.join(timeout=1.0)
            if signal_handler_installed:
                signal.signal(signal.SIGTERM, previous_sigterm)


def run_frontend(
    config: LaunchConfig,
    *,
    app=application,
    server_factory: Callable[..., BaseWSGIServer] | None = None,
    install_signal_handlers: bool = True,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Bind and serve the frontend, returning a process-style exit status."""
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        server = ProductionFrontendServer(
            config,
            app=app,
            server_factory=server_factory,
            install_signal_handlers=install_signal_handlers,
        )
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) and exc.code else 1
        print(
            ERROR_PREFIX + json.dumps({"error": "startup_failed", "exit_code": code}),
            file=errors,
            flush=True,
        )
        return code
    except Exception as exc:
        print(
            ERROR_PREFIX + json.dumps({
                "error": "startup_failed",
                "message": str(exc),
            }),
            file=errors,
            flush=True,
        )
        return 1

    print(
        STARTUP_PREFIX + json.dumps(server.startup_payload(), sort_keys=True),
        file=output,
        flush=True,
    )
    try:
        server.serve_forever()
    except Exception as exc:
        print(
            ERROR_PREFIX + json.dumps({
                "error": "server_failed",
                "message": str(exc),
            }),
            file=errors,
            flush=True,
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_launch_config(argv)
    return run_frontend(config)

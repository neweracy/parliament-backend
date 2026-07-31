#!/usr/bin/env python3
"""Local development launcher for the Postprocessing Service.

Exists for one reason: on Windows, asyncio defaults to ``ProactorEventLoop``,
which psycopg cannot use in async mode. The Dataset_Cache then fails every
load with::

    psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run
    in async mode.

leaving /health at 503 forever. Installing ``WindowsSelectorEventLoopPolicy``
before Uvicorn creates its loop fixes it. On Linux and macOS this script is a
thin passthrough, so the container entrypoint stays unchanged.

Environment variables are read from ``.env`` when present. The service's own
``Settings`` deliberately sets ``env_file=None`` so production never reads a
dotenv file; this loader is dev-only and populates ``os.environ`` before the
app imports its settings.

Usage::

    cd services/postprocess
    python scripts/run_local.py
    python scripts/run_local.py --reload
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = SERVICE_ROOT / ".env"


def install_selector_event_loop() -> None:
    """Force the selector event loop policy on Windows (psycopg requirement)."""
    if sys.platform != "win32":
        return
    policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy is not None:
        asyncio.set_event_loop_policy(policy())
        print("[run_local] installed WindowsSelectorEventLoopPolicy")


def load_dotenv(path: Path) -> int:
    """Load KEY=VALUE pairs from *path* into os.environ without overriding.

    Real environment variables win, so `SERVICE_TOKEN=x python scripts/run_local.py`
    still overrides the file. Blank lines and `#` comments are skipped; surrounding
    single or double quotes are stripped.
    """
    if not path.is_file():
        print(f"[run_local] no {path.name} found — relying on the ambient environment")
        return 0

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1

    print(f"[run_local] loaded {loaded} variable(s) from {path.name}")
    return loaded


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Postprocessing Service locally (Windows-safe)."
    )
    parser.add_argument("--host", default=None, help="Bind host (default: $HOST or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: $PORT or 8082)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload on code changes")
    return parser.parse_args(argv)


def ensure_service_root_on_path() -> None:
    """Put the service root on sys.path so ``app.main`` imports from any CWD."""
    root = str(SERVICE_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    # Uvicorn's reloader spawns a fresh interpreter, which does not inherit
    # sys.path edits — PYTHONPATH carries them across.
    existing = os.environ.get("PYTHONPATH", "")
    if root not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [root, existing]))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    ensure_service_root_on_path()
    load_dotenv(ENV_FILE)
    install_selector_event_loop()

    # Import after the environment is populated so Settings sees the values.
    import uvicorn

    host = args.host or os.environ.get("HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("PORT", "8082"))
    workers = int(os.environ.get("UVICORN_WORKERS", "1"))

    print(f"[run_local] starting uvicorn on {host}:{port} (workers={workers})")

    # loop="asyncio" keeps Uvicorn on the policy installed above instead of
    # selecting its own platform default.
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=workers if workers > 1 else None,
        reload=args.reload,
        loop="asyncio",
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

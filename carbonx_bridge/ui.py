"""Local control-panel page + helpers.

The bridge serves a small, single-file HTML panel at ``/`` so Windows users
can manage the bridge without editing JSON. It is stdlib-only (no CDN, no
frameworks) and shows/edits nothing that leaves the machine:

*  GET  /                 -> this page (no secrets in the markup)
*  GET  /ui/status        -> same shape as /health
*  GET  /ui/token         -> all configured tokens              (loopback only)
*  POST /ui/token         -> generate + persist a new token     (loopback only)
*  POST /ui/token {action:"delete", token: t} -> remove a token
                             (the last token cannot be deleted) (loopback only)
*  GET  /ui/config        -> sanitized config view              (loopback only;
                             api_key values are NEVER returned)
*  POST /ui/config        -> apply provider/token edits         (loopback only)
*  GET  /ui/lumen-script  -> paste-ready Lumen script that keeps pushing
                             the game snapshot (see scripts/)     (loopback only)

All ``/ui/*`` endpoints are gated on the client being on-loopback, and the
whole admin surface (including ``/``) additionally rejects bad ``Host``
headers (DNS-rebinding) and cross-site requests (CSRF).
"""

from __future__ import annotations

import os

from . import config as cfg_mod


def is_loopback(ip):
    return ip in ("127.0.0.1", "::1", "localhost")


def host_is_loopback(host_header):
    """True if a Host header names a loopback interface (rebinding guard)."""
    h = (host_header or "").strip()
    if h.startswith("["):  # [::1]:8787
        h = h[1 : h.find("]")]
    else:
        h = h.rsplit(":", 1)[0]  # strip :port
    return h.lower() in ("127.0.0.1", "localhost", "::1")


def load_lumen_script():
    """Return the Lumen game-context script text, or None.

    Resolution order (so the script can be updated without rebuilding the exe):

    1. ``scripts/lumen_game_context.luau`` next to the project root / the exe
       (``config.PROJECT_DIR``) -- the live copy people edit.
    2. The copy bundled inside the package by the PyInstaller build.
    """
    candidates = [
        os.path.join(cfg_mod.PROJECT_DIR, "scripts", "lumen_game_context.luau"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "lumen_game_context.luau"),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            continue
    return None


def load_panel_html():
    """Return the panel page (``panel.html``), or None.

    Resolution order:

    1. ``carbonx_bridge/panel.html`` next to the project root / the exe
       (``config.PROJECT_DIR``) -- the live copy people edit.
    2. The copy bundled inside the package by the PyInstaller build.
    """
    candidates = [
        os.path.join(cfg_mod.PROJECT_DIR, "carbonx_bridge", "panel.html"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html"),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            continue
    return None


PAGE = load_panel_html() or ""
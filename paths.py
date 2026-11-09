# SPDX-License-Identifier: AGPL-3.0-only
"""Location rules for source and movable, frozen desktop releases."""

from pathlib import Path
import os, sys

APP_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)


def writable_root():
    override = os.environ.get("ZBOOK_DATA_DIR") or os.environ.get("BOOKFORGE_DATA_DIR")
    if override:
        return Path(override).resolve()
    try:
        target = APP_ROOT / "data"
        target.mkdir(exist_ok=True)
        probe = target / ".write-probe"
        # Reuse a tiny marker; no repeated create/delete cleanup.
        with probe.open("ab"):
            pass
        return APP_ROOT
    except OSError:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ZBook"


ROOT = writable_root()
DATA = ROOT / "data"
ARCHIVE = ROOT / "archive"

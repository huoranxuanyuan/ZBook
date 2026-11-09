# SPDX-License-Identifier: AGPL-3.0-only
"""Default reflow typography for ZBook novels."""

PROFILE = "zbook-reading-1"
BODY_SIZE = 1.0
TITLE_SIZE = 1.2
LINE_HEIGHT = 1.2
PARAGRAPH_GAP = 0.0
TEXT_INDENT = 2.0


def migrate_settings(settings):
    """Replace only previous built-in defaults; retain deliberately custom values."""
    if settings.get("layout_profile"):
        settings["layout_profile"] = PROFILE
        return settings
    if settings.get("layout_profile") != PROFILE:
        for key, old, new in [
            ("line_height", 1.65, LINE_HEIGHT),
            ("paragraph_gap", 0.5, PARAGRAPH_GAP),
        ]:
            try:
                if float(settings.get(key, old)) == old:
                    settings[key] = f"{new:g}"
            except TypeError, ValueError:
                settings[key] = f"{new:g}"
        settings["layout_profile"] = PROFILE
    return settings

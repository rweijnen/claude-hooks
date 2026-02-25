#!/usr/bin/env python
"""PreToolUse hook for Write and Edit tools.

Blocks emoji and decorative unicode characters in file content.
These cause encoding issues, look AI-generated, and serve no purpose
in code or documentation.
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_HOOK_DIR = Path(__file__).resolve().parent
_config_cache = None


def _load_config():
    """Load config.json from the same directory as the hook script.

    Keys are lowercased for case-insensitive matching.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_path = _HOOK_DIR / "config.json"
    _config_cache = {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            _config_cache = {
                k.lower(): v for k, v in raw.items()
                if not k.startswith("_") and isinstance(v, bool)
            }
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return _config_cache


def _is_enabled(check_id, default=None):
    """Check whether a given check is enabled."""
    config = _load_config()
    if check_id in config:
        return config[check_id]
    if default is not None:
        return default
    return False  # file_content_unicode defaults to off


# Unicode ranges to block
BLOCKED_RANGES = [
    (0x1F000, 0x1FFFF, "emoji/symbols"),
    (0x2600, 0x27BF, "misc symbols/dingbats"),
    (0xFE00, 0xFE0F, "variation selectors"),
    (0x2500, 0x257F, "box drawing"),
    (0x2190, 0x21FF, "arrows"),
    (0x2700, 0x27BF, "dingbats"),
]


def find_blocked_char(text):
    """Return (char, codepoint, category) for the first blocked character, or None."""
    for char in text:
        cp = ord(char)
        for low, high, category in BLOCKED_RANGES:
            if low <= cp <= high:
                return char, cp, category
    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name == "Write":
        content = tool_input.get("content", "")
    elif tool_name == "Edit":
        content = tool_input.get("new_string", "")
    else:
        sys.exit(0)

    if not content:
        sys.exit(0)

    if not _is_enabled("file_content_unicode", default=False):
        sys.exit(0)

    result = find_blocked_char(content)
    if result:
        char, cp, category = result
        print(
            f"Blocked: found {category} character U+{cp:04X} in file content. "
            f"Use plain ASCII unless specifically requested by the user.",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()

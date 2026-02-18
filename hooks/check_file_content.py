#!/usr/bin/env python
"""PreToolUse hook for Write and Edit tools.

Blocks emoji and decorative unicode characters in file content.
These cause encoding issues, look AI-generated, and serve no purpose
in code or documentation.
"""

import json
import re
import sys

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

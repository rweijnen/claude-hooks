#!/usr/bin/env python
"""PreToolUse hook for Bash commands.

Intercepts commands before execution and applies fixes for common
Git Bash / MSYS2 mistakes on Windows.

Tier 1 (auto-fix): silently rewrites the command via updatedInput.
Tier 2 (suggest):  blocks with exit 2 and a message showing the proposed fix.
                   Logs to ~/.claude/hooks/fixups.log for review.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

FIXUPS_LOG = Path.home() / ".claude" / "hooks" / "fixups.log"


def log_fixup(original, proposed, fix_type):
    """Append a tier-2 suggestion to the fixups log."""
    FIXUPS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "timestamp": datetime.now().isoformat(),
        "fix_type": fix_type,
        "original": original,
        "proposed": proposed,
    })
    with open(FIXUPS_LOG, "a", encoding="utf-8") as f:
        f.write(entry + "\n")


def block(message):
    """Print a message to stderr and exit with code 2 (deny)."""
    print(message, file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Emoji detection (shared by Fix C and standalone)
# ---------------------------------------------------------------------------

EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000027BF"  # misc symbols & dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "]"
)


# ---------------------------------------------------------------------------
# Tier 2 -- blocking checks (run first so bad commands never execute)
# ---------------------------------------------------------------------------

def check_git_commit(cmd):
    """Fix C: block git commit messages with AI attribution or emoji."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return
    # Scan the whole command (covers -m "...", heredoc, etc.)
    if re.search(r"co-authored-by", cmd, re.IGNORECASE):
        block("Commit message contains Co-Authored-By. "
              "Remove AI attribution from commit messages.")
    if re.search(r"generated with", cmd, re.IGNORECASE):
        block("Commit message contains 'Generated with'. "
              "Remove AI attribution from commit messages.")
    if EMOJI_RE.search(cmd):
        block("Commit message contains emoji. "
              "Use plain text in commit messages.")


def check_doubled_flags(cmd):
    """Fix E: detect unnecessary // flag doubling for Windows commands.

    Testing on this system confirmed:
      - tasklist /fi   -> works
      - tasklist //fi  -> FAILS
      - ipconfig /all  -> works
      - ipconfig //all -> FAILS
    Single-slash flags are NOT converted by MSYS2 for short flag names.
    Doubled slashes break the commands.
    """
    # Skip URLs
    if re.search(r"https?://", cmd):
        return
    # Exception: cmd //c is a legitimate MSYS2 escape (/c = C: drive letter)
    stripped = cmd.lstrip()
    if re.match(r"cmd(\.exe)?\s+//c\b", stripped, re.IGNORECASE):
        return
    for m in re.finditer(r"(?:^|\s)(//([a-zA-Z]{1,4}))(?=\s|$|\")", cmd):
        flag_full = m.group(1)
        flag_name = m.group(2)
        # Skip if it looks like a UNC path (//server/share)
        after = cmd[m.end(1):]
        if after.startswith("/"):
            continue
        proposed = cmd[:m.start(1)] + "/" + flag_name + cmd[m.end(1):]
        log_fixup(cmd, proposed, "doubled_flag")
        block(f"Doubled // flags break Windows commands in Git Bash. "
              f"Single / works.\n"
              f"Original:  {cmd}\n"
              f"Suggested: {proposed}")


def check_backslash_paths(cmd):
    r"""Fix F: detect Windows backslash paths in bash context.

    Matches patterns like C:\Users, D:\temp outside single-quoted strings.
    """
    for m in re.finditer(r"([A-Za-z]):\\([A-Za-z])", cmd):
        # Skip if inside single quotes (count odd quotes before match)
        before = cmd[:m.start()]
        if before.count("'") % 2 == 1:
            continue
        proposed = re.sub(
            r"[A-Za-z]:\\[^\s'\"]*",
            lambda m: m.group(0).replace("\\", "/"),
            cmd,
        )
        log_fixup(cmd, proposed, "backslash_path")
        block(f"Windows backslash paths don't work reliably in Git Bash.\n"
              f"Original:  {cmd}\n"
              f"Suggested: {proposed}")


def check_cmd_workaround(cmd):
    """Block cmd /c usage unless clearly necessary.

    Claude often spawns cmd /c as a workaround when it can't figure out
    correct slash usage or escaping. This is fragile and should be avoided.
    """
    stripped = cmd.lstrip()
    if re.match(r"cmd(\.exe)?\s+(//c|/c)\b", stripped, re.IGNORECASE):
        block("Avoid cmd /c as a workaround. "
              "Run the command directly in Git Bash instead. "
              "If a Windows built-in (dir, type, etc.) is needed, "
              "consider a PowerShell or Python alternative.")


def check_powershell_file_for_oneliner(cmd):
    """Block powershell.exe -File for simple oneliners.

    If the command is just running a short inline snippet via a temp script,
    use pwsh -Command instead.  Also discourage powershell.exe (use pwsh).
    """
    stripped = cmd.lstrip()
    # Flag powershell.exe (Windows PowerShell 5.1) -- prefer pwsh (7+)
    if re.match(r"powershell(\.exe)?\s", stripped, re.IGNORECASE):
        block("Use pwsh (PowerShell 7+) instead of powershell.exe. "
              "powershell.exe invokes the legacy Windows PowerShell 5.1.")


# ---------------------------------------------------------------------------
# Tier 1 -- auto-fixes (silently rewrite the command)
# ---------------------------------------------------------------------------

def fix_nul_redirect(cmd):
    """Fix A: > nul -> > /dev/null"""
    return re.sub(
        r"(?<!/dev/)((?:&|[012])?>)\s*nul\b",
        r"\1 /dev/null",
        cmd,
    )


def fix_python3(cmd):
    """Fix B: python3 -> python (Windows Store alias, not real Python)."""
    return re.sub(r"\bpython3\b", "python", cmd)


def fix_pwsh_quoting(cmd):
    """Fix D: pwsh -Command "...$..." -> single quotes.

    Returns (fixed_cmd, error_message_or_None).
    When the content has both $ and embedded single quotes, we can't
    auto-fix -- block with advice to use -File instead.
    """
    m = re.search(
        r'(pwsh(?:\.exe)?\s+(?:-Command|-c)\s+)"([^"]*)"',
        cmd,
        re.IGNORECASE,
    )
    if not m:
        return cmd, None

    content = m.group(2)
    if "$" not in content:
        return cmd, None

    if "'" in content:
        return cmd, (
            "pwsh -Command with $ and embedded single quotes: "
            "bash will expand $ in double quotes and single quotes "
            "prevent nesting. Use pwsh -File script.ps1 instead."
        )

    fixed = cmd[:m.start()] + m.group(1) + "'" + content + "'" + cmd[m.end():]
    return fixed, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    original = command
    fixes = []

    # -- Tier 2 checks (blocking) ------------------------------------------
    check_git_commit(command)
    check_cmd_workaround(command)
    check_powershell_file_for_oneliner(command)
    check_doubled_flags(command)
    check_backslash_paths(command)

    # -- Tier 1 auto-fixes --------------------------------------------------

    command = fix_nul_redirect(command)
    if command != original:
        fixes.append("replaced > nul with > /dev/null")

    prev = command
    command = fix_python3(command)
    if command != prev:
        fixes.append("replaced python3 with python")

    prev = command
    command, pwsh_err = fix_pwsh_quoting(command)
    if pwsh_err:
        block(pwsh_err)
    if command != prev:
        fixes.append("swapped pwsh -Command quotes from double to single")

    # -- Emit result --------------------------------------------------------
    if fixes:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": {
                    "command": command,
                    "description": tool_input.get("description", ""),
                },
                "additionalContext": "Hook auto-fixed: " + ", ".join(fixes),
            }
        }
        json.dump(output, sys.stdout)

    sys.exit(0)


if __name__ == "__main__":
    main()

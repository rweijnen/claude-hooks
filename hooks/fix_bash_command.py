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

# Log next to this script (project-local hooks get their own log)
_HOOK_DIR = Path(__file__).resolve().parent
FIXUPS_LOG = _HOOK_DIR / "fixups.log"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Built-in defaults: safety checks ON, style checks OFF
_DEFAULTS = {
    "nul_redirect": True,
    "msys2_drive_paths": True,
    "backslash_paths": True,
    "unc_paths": True,
    "wsl_paths": True,
    "reserved_names": True,
    "python3": True,
    "dir_windows_flags": True,
    "doubled_flags": True,
    "dir_in_pwsh": True,
    "pwsh_quoting": True,
    "cmd_workaround": True,
    "powershell_legacy": True,
    "wsl_invocation": True,
    "git_commit_attribution": False,
    "git_commit_generated": False,
    "git_commit_emoji": False,
}

_config_cache = None


def _load_config():
    """Load config.json from the same directory as the hook script.

    Returns a dict of check_id -> bool. Missing file or invalid JSON
    falls back to an empty dict (all checks use built-in defaults).
    Keys starting with '_' (comment keys) are ignored.
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
                k: v for k, v in raw.items()
                if not k.startswith("_") and isinstance(v, bool)
            }
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return _config_cache


def _is_enabled(check_id, default=None):
    """Check whether a given check is enabled.

    Looks up check_id in config.json first, then falls back to built-in
    defaults. If the check_id is unknown, uses the provided default
    (or True if not specified).
    """
    config = _load_config()
    if check_id in config:
        return config[check_id]
    if default is not None:
        return default
    return _DEFAULTS.get(check_id, True)

MAX_LOG_LINES = 500
TRIM_TO_LINES = 250


def _trim_log_if_needed():
    """Keep the log file bounded. When it exceeds MAX_LOG_LINES, trim to TRIM_TO_LINES."""
    try:
        lines = FIXUPS_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) > MAX_LOG_LINES:
        FIXUPS_LOG.write_text(
            "\n".join(lines[-TRIM_TO_LINES:]) + "\n",
            encoding="utf-8",
        )


def _log_entry(entry_type, fix_type, original, proposed=None, fixes=None):
    """Append a structured log entry."""
    FIXUPS_LOG.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    entry = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "type": entry_type,
        "fix": fix_type,
        "cwd": os.getcwd(),
        "original": original,
    }
    if proposed is not None:
        entry["proposed"] = proposed
    if fixes is not None:
        entry["fixes"] = fixes
    with open(FIXUPS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    _trim_log_if_needed()


def log_fixup(original, proposed, fix_type):
    """Log a tier-2 suggestion (blocked, not auto-fixed)."""
    _log_entry("suggest", fix_type, original, proposed=proposed)


def log_autofix(original, fixed, fixes):
    """Log a tier-1 auto-fix (silently applied)."""
    _log_entry("autofix", ",".join(fixes), original, proposed=fixed, fixes=fixes)


def block(message):
    """Print a message to stderr and exit with code 2 (deny)."""
    print(message, file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Windows reserved device names (creating these as files is destructive)
# https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
# ---------------------------------------------------------------------------

WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}

# Pattern matching reserved names as redirect targets or file arguments.
# Matches: > con, 2> prn, &> aux, also con.txt (with extension).
_RESERVED_RE = re.compile(
    r"(?<!/dev/)((?:&|[012])?>)\s*("
    + "|".join(WINDOWS_RESERVED_NAMES)
    + r")(?:\.[\w.]+)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Emoji detection (shared by Fix C and standalone)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# WSL environment detection (cached)
# ---------------------------------------------------------------------------

_in_wsl = "WSL_DISTRO_NAME" in os.environ
_wsl_installed = None


def _is_wsl_installed():
    global _wsl_installed
    if _wsl_installed is None:
        _wsl_installed = Path("C:/Windows/System32/wsl.exe").exists()
    return _wsl_installed


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

def check_git_commit_attribution(cmd):
    """Block git commit messages with Co-Authored-By."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return
    if re.search(r"co-authored-by", cmd, re.IGNORECASE):
        block("Commit message contains Co-Authored-By. "
              "Remove AI attribution from commit messages.")


def check_git_commit_generated(cmd):
    """Block git commit messages with 'Generated with'."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return
    if re.search(r"generated with", cmd, re.IGNORECASE):
        block("Commit message contains 'Generated with'. "
              "Remove AI attribution from commit messages.")


def check_git_commit_emoji(cmd):
    """Block git commit messages containing emoji."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return
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


def check_unc_paths(cmd):
    r"""Fix H: detect UNC paths with backslashes (\\server\share).

    Backslash UNC paths don't work in Git Bash. Forward-slash UNC
    paths (//server/share) work in both Git Bash and Python.
    """
    for m in re.finditer(r"\\\\([A-Za-z0-9._-]+)\\([^\s'\"]*)", cmd):
        # Skip if inside single quotes
        before = cmd[:m.start()]
        if before.count("'") % 2 == 1:
            continue
        proposed = re.sub(
            r"\\\\([A-Za-z0-9._-]+)\\([^\s'\"]*)",
            lambda m: "//" + m.group(1) + "/" + m.group(2).replace("\\", "/"),
            cmd,
        )
        log_fixup(cmd, proposed, "unc_path")
        block(f"UNC paths with backslashes don't work in Git Bash.\n"
              f"Original:  {cmd}\n"
              f"Suggested: {proposed}")


def check_wsl_invocation(cmd):
    """Block bare 'wsl' commands -- Claude is in Git Bash, not WSL."""
    if not re.match(r"wsl(\.exe)?\s", cmd.lstrip(), re.IGNORECASE):
        return
    # Allow full-path invocations as an intentional escape hatch
    stripped = cmd.lstrip()
    if re.match(r"[A-Za-z]:/", stripped):
        return
    if _is_wsl_installed():
        block(
            "You are running in Git Bash on native Windows, not inside WSL.\n"
            "Run the command directly instead of prefixing it with wsl.\n"
            "If you specifically need to run a command inside WSL, "
            "use the full path: C:/Windows/System32/wsl.exe"
        )
    else:
        block(
            "WSL is not installed. You are running in Git Bash on native Windows.\n"
            "Run the command directly instead of prefixing it with wsl."
        )


def check_wsl_paths(cmd):
    """Block /mnt/c/ style paths -- these are WSL paths, not Git Bash paths."""
    if _in_wsl:
        return
    m = re.search(r"/mnt/([a-zA-Z])/", cmd)
    if not m:
        return
    proposed = re.sub(
        r"/mnt/([a-zA-Z])/",
        lambda m: m.group(1).upper() + ":/",
        cmd,
    )
    log_fixup(cmd, proposed, "wsl_path")
    block(
        f"/mnt/{m.group(1)}/ is a WSL mount path. "
        f"You are in Git Bash on native Windows.\n"
        f"Original:  {cmd}\n"
        f"Suggested: {proposed}"
    )


def check_reserved_names(cmd):
    """Block redirects to Windows reserved device names (other than NUL).

    NUL is handled by the tier-1 auto-fix (rewritten to /dev/null).
    Other reserved names (CON, PRN, AUX, COM1-9, LPT1-9) would create
    undeletable files in Git Bash or redirect to hardware devices.
    Also catches reserved names used as file arguments (touch con, etc.).
    """
    # Check redirects: > con, 2> prn, &> aux, > lpt1.txt, etc.
    m = _RESERVED_RE.search(cmd)
    if m:
        name = m.group(2).lower()
        if name != "nul":  # nul is auto-fixed in tier 1
            log_fixup(cmd, None, "reserved_name_redirect")
            block(f"'{m.group(2)}' is a Windows reserved device name. "
                  f"Redirecting to it will either send output to a hardware "
                  f"device or create an undeletable file.\n"
                  f"Use > /dev/null to discard output.")

    # Check file arguments: touch con, mkdir prn, etc.
    # Look for reserved names as bare arguments after file-creating commands
    file_cmds = r"\b(?:touch|mkdir|cp|mv|cat\s*>|tee)\s+"
    fm = re.search(file_cmds + r"(\S+)", cmd)
    if fm:
        filename = fm.group(1).strip("\"'")
        basename = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        # Strip extension: con.txt -> con
        stem = basename.split(".", 1)[0].lower()
        if stem in WINDOWS_RESERVED_NAMES:
            log_fixup(cmd, None, "reserved_name_file")
            block(f"'{basename}' uses Windows reserved name '{stem}'. "
                  f"This will create an undeletable file on Windows. "
                  f"Choose a different filename.")


def check_cmd_workaround(cmd):
    """Block bare cmd /c workarounds; allow full-path for legitimate cmd use.

    Bare 'cmd /c' / 'cmd.exe /c' is blocked. Full-path invocations
    (C:/Windows/System32/cmd.exe /c) are allowed as an intentional escape
    hatch for cases that genuinely require a cmd.exe environment (.bat files,
    Windows built-ins with no bash/pwsh equivalent, legacy tooling).
    """
    stripped = cmd.lstrip()
    if re.match(r"cmd(\.exe)?\s+(//c|/c)\b", stripped, re.IGNORECASE):
        block(
            "Avoid cmd /c as a workaround. "
            "Run the command directly in Git Bash instead. "
            "If a Windows built-in (dir, type, etc.) is needed, "
            "consider a PowerShell or Python alternative.\n"
            "If you specifically need a cmd.exe environment (.bat files, "
            "legacy tooling), use the full path: "
            "C:/Windows/System32/cmd.exe /c \"...\""
        )


def check_powershell_file_for_oneliner(cmd):
    """Block bare powershell.exe; prefer pwsh (PowerShell 7+).

    Only bare 'powershell' / 'powershell.exe' is blocked. Full-path
    invocations (C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe)
    are allowed as an intentional escape hatch for PS 5.1 legacy use.
    """
    stripped = cmd.lstrip()
    if re.match(r"powershell(\.exe)?\s", stripped, re.IGNORECASE):
        block(
            "Use pwsh (PowerShell 7+) instead of powershell.exe. "
            "powershell.exe invokes the legacy Windows PowerShell 5.1.\n"
            "If you specifically need PowerShell 5.1 for legacy compatibility, "
            "use the full path: "
            "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        )


def check_dir_in_pwsh(cmd):
    """Fix J: block cmd.exe-style 'dir /flag' inside pwsh -Command.

    PowerShell's dir is an alias for Get-ChildItem and does not accept
    cmd.exe flags. /b is resolved as a path under the current drive root
    (e.g. C:\\b), producing a confusing 'Cannot find path' error.
    """
    if not re.search(r"\bpwsh(?:\.exe)?\s+(?:-Command|-c)\b", cmd, re.IGNORECASE):
        return
    dm = re.search(r"\bdir\s+(/[a-zA-Z])", cmd, re.IGNORECASE)
    if not dm:
        return
    flag = dm.group(1).lower()
    suggestions = {
        "/b": "Get-ChildItem path | Select-Object -ExpandProperty Name",
        "/s": "Get-ChildItem path -Recurse",
        "/a": "Get-ChildItem path -Force",
    }
    suggestion = suggestions.get(flag, "Get-ChildItem path")
    log_fixup(cmd, None, "dir_in_pwsh")
    block(
        f"'dir {flag}' is a cmd.exe flag; PowerShell's dir (Get-ChildItem) "
        f"does not accept it and will treat it as a path.\n"
        f"Use: {suggestion}"
    )


# ---------------------------------------------------------------------------
# Tier 1 -- auto-fixes (silently rewrite the command)
# ---------------------------------------------------------------------------

def fix_nul_redirect(cmd):
    """Fix A: > nul -> > /dev/null (case-insensitive: NUL, Nul, nul)"""
    return re.sub(
        r"(?<!/dev/)((?:&|[012])?>)\s*nul\b",
        r"\1 /dev/null",
        cmd,
        flags=re.IGNORECASE,
    )


def fix_msys2_drive_paths(cmd):
    """Fix G: /c/Work/... -> C:/Work/...

    MSYS2 drive mount paths (/c/, /d/, etc.) sometimes fail to convert
    when passed to Windows executables like python.exe. Using C:/ style
    is always safe for both MSYS2 tools and Windows executables.
    """
    return re.sub(
        r"(?:^|(?<=\s))/([a-zA-Z])/",
        lambda m: m.group(1).upper() + ":/",
        cmd,
    )


def fix_python3(cmd):
    """Fix B: python3 -> python (Windows Store alias, not real Python)."""
    return re.sub(r"\bpython3\b", "python", cmd)


# Mapping of Windows cmd.exe 'dir' flags to GNU 'ls' equivalents
_DIR_FLAG_MAP = {
    "b": "-1",   # bare names only, one per line
    "s": "-R",   # recursive (subdirectories)
    "a": "-la",  # all files including hidden (dotfiles)
    "w": "",     # wide format (ls default, no extra flag needed)
    "n": "-l",   # new long format
    "q": "-l",   # show owner (ls -l includes owner)
}


def fix_dir_windows_flags(cmd):
    """Fix I: 'dir /flags [path]' -> 'ls [flags] [path]'.

    In Git Bash, 'dir' is GNU coreutils, not cmd.exe. Windows-style
    /flags are treated as paths, not switches. Only rewrites when all
    flags are in the known mapping; unknown flags pass through unchanged.
    """
    if not re.match(r"^dir\b", cmd.strip(), re.IGNORECASE):
        return cmd

    rest = cmd.strip()[3:].strip()  # everything after 'dir'

    # Consume leading /flag tokens (e.g. /b, /s, /a:h)
    flags = []
    while True:
        fm = re.match(r"^(/[a-zA-Z])(?::[a-zA-Z]*)?\s*(.*)", rest, re.DOTALL | re.IGNORECASE)
        if not fm:
            break
        flags.append(fm.group(1).lower())
        rest = fm.group(2)

    if not flags:
        return cmd  # no Windows-style flags found

    # Only auto-fix when all flags are known
    if any(f[1] not in _DIR_FLAG_MAP for f in flags):
        return cmd

    ls_flags = []
    for f in flags:
        mapped = _DIR_FLAG_MAP[f[1]]
        if mapped and mapped not in ls_flags:
            ls_flags.append(mapped)

    path_part = rest.strip()
    ls_cmd = "ls"
    if ls_flags:
        ls_cmd += " " + " ".join(ls_flags)
    if path_part:
        ls_cmd += " " + path_part
    return ls_cmd.strip()


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
    if _is_enabled("git_commit_attribution", default=False):
        check_git_commit_attribution(command)
    if _is_enabled("git_commit_generated", default=False):
        check_git_commit_generated(command)
    if _is_enabled("git_commit_emoji", default=False):
        check_git_commit_emoji(command)
    if _is_enabled("cmd_workaround"):
        check_cmd_workaround(command)
    if _is_enabled("powershell_legacy"):
        check_powershell_file_for_oneliner(command)
    if _is_enabled("wsl_invocation"):
        check_wsl_invocation(command)
    if _is_enabled("wsl_paths"):
        check_wsl_paths(command)
    if _is_enabled("dir_in_pwsh"):
        check_dir_in_pwsh(command)
    if _is_enabled("reserved_names"):
        check_reserved_names(command)
    if _is_enabled("doubled_flags"):
        check_doubled_flags(command)
    if _is_enabled("backslash_paths"):
        check_backslash_paths(command)
    if _is_enabled("unc_paths"):
        check_unc_paths(command)

    # -- Tier 1 auto-fixes --------------------------------------------------

    if _is_enabled("nul_redirect"):
        command = fix_nul_redirect(command)
        if command != original:
            fixes.append("replaced > nul with > /dev/null")

    if _is_enabled("msys2_drive_paths"):
        prev = command
        command = fix_msys2_drive_paths(command)
        if command != prev:
            fixes.append("converted MSYS2 drive paths to Windows style")

    if _is_enabled("python3"):
        prev = command
        command = fix_python3(command)
        if command != prev:
            fixes.append("replaced python3 with python")

    if _is_enabled("dir_windows_flags"):
        prev = command
        command = fix_dir_windows_flags(command)
        if command != prev:
            fixes.append("converted Windows dir /flags to ls equivalent")

    if _is_enabled("pwsh_quoting"):
        prev = command
        command, pwsh_err = fix_pwsh_quoting(command)
        if pwsh_err:
            block(pwsh_err)
        if command != prev:
            fixes.append("swapped pwsh -Command quotes from double to single")

    # -- Emit result --------------------------------------------------------
    if fixes:
        log_autofix(original, command, fixes)
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

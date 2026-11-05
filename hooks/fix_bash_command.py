#!/usr/bin/env python
"""PreToolUse hook for Bash commands.

Intercepts commands before execution and applies fixes for common
Git Bash / MSYS2 mistakes on Windows.

Tier 1 (auto-fix): silently rewrites the command via updatedInput.
Tier 2 (suggest):  blocks with exit 2 and a message showing the proposed fix.
Both tiers log to fixups.log next to this script for review.
"""

import bisect
import difflib
import functools
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
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
    "powershell_legacy": True,
    "wsl_invocation": True,
    "bare_pwsh_cmdlet": True,
    "pwsh_noprofile": True,
    "windows_env_vars": True,
    "cmd_cd": True,
    "start_command": True,
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

# Size-based so the common path is a single stat(); the file is only read
# when it actually needs trimming. Entries average ~1 KB (full commands),
# so this keeps several weeks of history.
MAX_LOG_BYTES = 5_000_000
TRIM_TO_BYTES = 2_500_000


def _trim_log_if_needed():
    """Keep the log bounded: past MAX_LOG_BYTES, keep the newest ~TRIM_TO_BYTES."""
    if FIXUPS_LOG.stat().st_size <= MAX_LOG_BYTES:
        return
    lines = FIXUPS_LOG.read_text(encoding="utf-8").splitlines()
    kept, size = [], 0
    for line in reversed(lines):
        size += len(line.encode("utf-8")) + 1
        if size > TRIM_TO_BYTES:
            break
        kept.append(line)
    FIXUPS_LOG.write_text("\n".join(reversed(kept)) + "\n", encoding="utf-8", newline="\n")


def _log_entry(entry_type, fix_type, original, proposed=None, fixes=None, decision=None):
    """Append a structured log entry. decision is set for verbatim entries
    (noop or ask)."""
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
    if decision is not None:
        entry["decision"] = decision
    with open(FIXUPS_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry) + "\n")
    _trim_log_if_needed()


def log_autofix(original, fixed, fixes):
    """Log a tier-1 auto-fix (silently applied)."""
    _log_entry("autofix", ",".join(fixes), original, proposed=fixed, fixes=fixes)


def _write_stderr(message):
    """Write UTF-8 to stderr regardless of the console code page."""
    sys.stderr.buffer.write((message + "\n").encode("utf-8"))
    sys.stderr.buffer.flush()


def block(message, check_id, cmd, proposed=None):
    """Log the block, print the message to stderr and exit 2 (deny)."""
    _log_entry("suggest", check_id, cmd, proposed=proposed)
    _write_stderr(message)
    sys.exit(2)


def read_hook_input():
    """Parse the hook's JSON input. Claude Code sends UTF-8.

    sys.stdin would decode with the Windows code page (cp1252) and turn
    every non-ASCII character into mojibake, so decode the bytes directly.
    """
    raw = sys.stdin.buffer.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        _write_stderr(f"fix_bash_command: hook input is not valid UTF-8 JSON: {e}")
        sys.exit(1)


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

# Names and domains of AI coding assistants, as they appear in commit trailers
# and "Generated with" footers. Human co-authors never match.
_AI_TOOLS = (r"\b(?:claude|anthropic|copilot|chatgpt|openai|gemini|cursor|"
             r"codex|aider|devin)\b")
_AI_COAUTHOR_RE = re.compile(r"co-authored-by:[^\n]*?" + _AI_TOOLS, re.IGNORECASE)
_AI_GENERATED_RE = re.compile(r"\bgenerated\s+(?:with|by)\b[^\n]*?" + _AI_TOOLS,
                              re.IGNORECASE)


def _policy_message(what, check_id):
    return (f"Commit message contains {what}. This hook config disallows it "
            f"({check_id}). Remove it, or set \"{check_id}\": false in "
            f"config.json to allow it.")


def check_git_commit_attribution(cmd):
    """Block Co-Authored-By trailers that name an AI assistant (opt-in policy)."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return
    if _AI_COAUTHOR_RE.search(cmd):
        block(_policy_message("an AI Co-Authored-By trailer", "git_commit_attribution"),
              "git_commit_attribution", cmd)


def check_git_commit_generated(cmd):
    """Block 'Generated with <AI tool>' footers (opt-in policy)."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return
    if _AI_GENERATED_RE.search(cmd):
        block(_policy_message("a 'Generated with' AI footer", "git_commit_generated"),
              "git_commit_generated", cmd)


def check_git_commit_emoji(cmd):
    """Block git commit messages containing emoji."""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return
    if EMOJI_RE.search(cmd):
        block("Commit message contains emoji. "
              "Use plain text in commit messages.",
              "git_commit_emoji", cmd)


def _msys_arg_conversion_off():
    """True when MSYS2 argument conversion is off for every argument.

    With MSYS2_ARG_CONV_EXCL=* a Windows flag such as /fi reaches the
    program as is and //fi breaks it. With Git Bash's default conversion it
    is the exact opposite: /fi becomes C:/Program Files/Git/fi and //fi is
    the escape. Only "*" switches conversion off completely; a list of
    prefixes leaves other arguments converted.
    """
    return os.environ.get("MSYS2_ARG_CONV_EXCL") == "*"


def fix_doubled_flags(cmd):
    """Fix E: //flag -> /flag for Windows commands, when conversion is off.

    Measured with MSYS2_ARG_CONV_EXCL=*: tasklist /fi and ipconfig /all
    work, tasklist //fi and ipconfig //all fail. Under Git Bash's default
    conversion //flag is correct, so the command is left alone (#53).
    """
    if not _msys_arg_conversion_off():
        return cmd
    # Skip URLs
    if re.search(r"https?://", cmd):
        return cmd
    flag_re = re.compile(r"(?:^|\s)(//([a-zA-Z]{1,4}))(?=\s|$|\")")
    # Collect flags outside heredocs and UNC paths
    matches = []
    for m in flag_re.finditer(cmd):
        if _in_heredoc(cmd, m.start(1)):
            continue
        after = cmd[m.end(1):]
        if after.startswith("/"):
            continue
        matches.append((m.start(1), m.end(1), "/" + m.group(2)))
    if not matches:
        return cmd
    # Build fixed command by replacing all matches (reverse order to preserve offsets)
    fixed = cmd
    for start, end, replacement in reversed(matches):
        fixed = fixed[:start] + replacement + fixed[end:]
    return fixed


_HEREDOC_RE = re.compile(
    r"<<-?\s*['\"]?(\w+)['\"]?.*?\n(.*?\n)\1",
    re.DOTALL,
)


# A heredoc whose delimiter is quoted (<<'EOF', <<"EOF"): bash does not expand
# anything in its body, so the body is literal text.
_QUOTED_HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"])(\w+)\1.*?\n(.*?\n)\2",
    re.DOTALL,
)


def _quoted_heredoc_spans(cmd):
    """(start, end) of every quoted-delimiter heredoc body."""
    return [(m.start(3), m.end(3)) for m in _QUOTED_HEREDOC_RE.finditer(cmd)]


def _strip_heredocs(cmd):
    """Remove heredoc bodies from a command string.

    Heredoc content is text payload, not shell-interpreted paths.
    Stripping it prevents false positives in path checks.
    """
    return _HEREDOC_RE.sub("", cmd)


def _in_heredoc(cmd, pos):
    """Check if a position falls inside a heredoc body."""
    for m in _HEREDOC_RE.finditer(cmd):
        body_start = m.start(2)
        body_end = m.end(2)
        if body_start <= pos < body_end:
            return True
    return False


# A command whose arguments run on another system (Linux via WSL, a remote
# host via ssh, an Android device via adb shell). Matched at the start of a
# word, optionally with a path prefix (C:/Windows/System32/wsl.exe).
_FOREIGN_CMD_RE = re.compile(
    r"(?:[^\s'\";&|]*[/\\])?(?:wsl|ssh|plink)(?:\.exe)?\s"
    r"|adb(?:\.exe)?(?:\s+-[de]|\s+-s\s+\S+)*\s+shell\b",
    re.IGNORECASE,
)
# Same, but the program path is quoted: "C:\...\OpenSSH\ssh.exe" host ...
_QUOTED_FOREIGN_CMD_RE = re.compile(
    r"\"[^\"]*[/\\](?:wsl|ssh|plink)(?:\.exe)?\"\s",
    re.IGNORECASE,
)
_SEGMENT_SEPARATORS = ";&|\n"
_WSL_ANYWHERE_RE = re.compile(r"(?:^|[\s/\\\"'(;&|])wsl(?:\.exe)?\"?\s", re.IGNORECASE)


def _shell_spans(cmd):
    """Return (heredoc_spans, foreign_spans) as lists of (start, end).

    heredoc_spans are heredoc bodies. foreign_spans cover the arguments of
    a wsl/ssh/plink/adb-shell command up to the end of that simple command
    (the next unquoted ; & | or newline), plus any heredoc body fed to it.
    Quote state is tracked so separators inside quotes do not end a span,
    and heredoc bodies are skipped so their apostrophes do not confuse it.
    """
    heredocs = [(m.start(2), m.end(2)) for m in _HEREDOC_RE.finditer(cmd)]
    body_at = {start: end for start, end in heredocs}
    foreign = []
    fstart = None
    quote = None
    word_start = True
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\":
            i += 2
            word_start = False
            continue
        if ch in _SEGMENT_SEPARATORS:
            body_end = body_at.get(i + 1) if ch == "\n" else None
            if fstart is not None:
                foreign.append((fstart, body_end if body_end else i))
                fstart = None
            i = body_end if body_end else i + 1
            word_start = True
            continue
        if word_start and fstart is None:
            m = _FOREIGN_CMD_RE.match(cmd, i) or _QUOTED_FOREIGN_CMD_RE.match(cmd, i)
            if m:
                fstart = m.end()
                i = m.end()
                continue
        if ch in "'\"":
            quote = ch
        word_start = ch.isspace() or ch == "("
        i += 1
    if fstart is not None:
        foreign.append((fstart, n))
    return heredocs, foreign


# A trigger must be a whole word, not part of a longer file name, path, URL
# or identifier: `python3` in `C:/temp/python3-store.png` or `python3_x` is
# not a command, and `/mnt/c/` in `/srv/mnt/c/` is not a WSL mount path.
_NAME_OR_PATH_CHAR = r"[\w.~:/\\-]"
_WORD_BEFORE = r"(?<!" + _NAME_OR_PATH_CHAR + r")"
_WORD_AFTER = r"(?!" + _NAME_OR_PATH_CHAR + r")"


def _sub_outside(pattern, repl, cmd, spans, flags=0):
    """re.sub that leaves matches starting inside any of spans untouched."""
    def replace(m):
        if any(a <= m.start() < b for a, b in spans):
            return m.group(0)
        return repl(m) if callable(repl) else m.expand(repl)
    return re.sub(pattern, replace, cmd, flags=flags)


# ---------------------------------------------------------------------------
# Command parsing: split a command into typed spans (v2, #48 / #51)
#
# A fix should only touch text that is a command, not text about a command.
# parse_command() labels every character of the command with what it is:
#   cmd      a command word (after assignments, keywords, prefix commands)
#   arg      an ordinary argument or redirect target
#   code     text another interpreter runs (python -c, bash -c, pwsh -Command,
#            cmd /c, a heredoc fed to python - or bash)
#   foreign  text that runs on another system (wsl/ssh/plink/adb shell)
#   data     text no program runs (echo/printf arguments, git commit -m, gh
#            --body/--title, grep/sed/awk patterns, heredocs written to a file)
#   comment  a shell comment
#   op       whitespace, separators, redirect operators, heredoc delimiters
# This is a scanner for the bash that Claude writes, not a full bash grammar.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    lang: str = None     # for code: python | bash | pwsh | cmd
    quote: str = ""      # "" | "'" | '"' | "$'" | "<<'" (quoted heredoc) | "<<"
    heredoc: bool = False

    @property
    def expands(self):
        """True where bash performs $-expansion on this text."""
        return self.quote in ("", '"', "<<")


@dataclass(frozen=True)
class Parsed:
    text: str
    spans: tuple
    starts: tuple
    redirects: tuple     # (op_start, op_end, target_start, target_end)
    verbatim: bool       # a top-level "# hook:verbatim" comment is present

    def at(self, pos):
        """The span containing position pos, or None outside the text."""
        if pos < 0 or pos >= len(self.text):
            return None
        return self.spans[bisect.bisect_right(self.starts, pos) - 1]


_OP = ("op", None, "", False)
_COMMENT = ("comment", None, "", False)
_REDIRECT_RE = re.compile(r"\d*(?:<<<|<<-|<<|&>>|&>|>>|>&|<&|<>|>\||>|<)")
_ASSIGN_RE = re.compile(r"[A-Za-z_]\w*\+?=")
_VERBATIM_RE = re.compile(r"#\s*hook:verbatim\s*")
_KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "while",
                       "until", "!", "{", "}", "time", "coproc"})
_LIST_KEYWORDS = frozenset({"for", "select", "case", "function"})


class _Word:
    __slots__ = ("start", "end", "pieces", "value", "raw")

    def __init__(self, start, end, pieces, value, raw):
        self.start, self.end, self.pieces, self.value, self.raw = start, end, pieces, value, raw


class _SimpleCommand:
    __slots__ = ("words", "targets", "heredoc")

    def __init__(self):
        self.words = []
        self.targets = []
        self.heredoc = ("data", None)


def _cmd_name(value):
    name = value.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _label_rest(labels, start, label):
    for k in range(start, len(labels)):
        labels[k] = label


def _skip_prefix(values, i, name):
    """Index of the first word after a prefix command and its options."""
    n = len(values)
    i += 1
    if name == "env":
        while i < n and (values[i].startswith("-") or _ASSIGN_RE.match(values[i])):
            i += 2 if values[i] in ("-u", "-C", "-S") else 1
    elif name == "sudo":
        while i < n and values[i].startswith("-"):
            i += 2 if values[i] in ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T") else 1
    elif name == "nice":
        if i < n and values[i] == "-n":
            i += 2
        elif i < n and values[i].startswith("-"):
            i += 1
    elif name == "timeout":
        while i < n and values[i].startswith("-"):
            i += 2 if values[i] in ("-s", "-k", "--signal", "--kill-after") else 1
        i += 1
    elif name == "xargs":
        while i < n and values[i].startswith("-"):
            i += 2 if values[i] in ("-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a") else 1
    return min(i, n)


_PREFIX_COMMANDS = frozenset({"env", "sudo", "nice", "timeout", "xargs", "nohup",
                              "command", "exec", "builtin", "winpty"})
_PATTERN_VALUE_OPTS = frozenset({"-A", "-B", "-C", "-m", "-d", "-D", "-g", "-t", "-T", "-M",
                                 "-j", "--max-count", "--context", "--after-context",
                                 "--before-context", "--include", "--exclude",
                                 "--exclude-dir", "--glob", "--type", "--type-not"})


def _classify_words(values):
    """Kinds for the words of one simple command, plus the kind of a heredoc
    fed to it. Returns ([(kind, lang)] per word, (kind, lang) for heredocs).
    This is the one place that knows about specific programs."""
    n = len(values)
    labels = [("arg", None)] * n
    i = 0
    while i < n and (values[i] in _KEYWORDS or _ASSIGN_RE.match(values[i])):
        labels[i] = ("op", None) if values[i] in _KEYWORDS else ("arg", None)
        i += 1
    if i < n and values[i] in _LIST_KEYWORDS:
        labels[i] = ("op", None)
        return labels, ("data", None)
    while i < n and _cmd_name(values[i]) in _PREFIX_COMMANDS:
        labels[i] = ("cmd", None)
        if _cmd_name(values[i]) == "command" and i + 1 < n and values[i + 1] in ("-v", "-V"):
            return labels, ("data", None)    # a lookup like which: nothing runs
        i = _skip_prefix(values, i, _cmd_name(values[i]))
    if i >= n:
        return labels, ("data", None)

    name = _cmd_name(values[i])
    labels[i] = ("cmd", None)
    rest = range(i + 1, n)

    if name in ("wsl", "ssh", "plink"):
        _label_rest(labels, i + 1, ("foreign", None))
        return labels, ("foreign", None)

    if name == "adb":
        for k in rest:
            if values[k] == "shell":
                _label_rest(labels, k + 1, ("foreign", None))
                return labels, ("foreign", None)
        return labels, ("data", None)

    if re.fullmatch(r"python[\d.]*|py", name):
        k = i + 1
        while k < n:
            v = values[k]
            if v == "-c":
                if k + 1 < n:
                    labels[k + 1] = ("code", "python")
                return labels, ("data", None)
            if v == "-m":
                return labels, ("data", None)
            if v == "-":
                return labels, ("code", "python")
            if v.startswith("-"):
                k += 2 if v in ("-W", "-X", "-Q") else 1
                continue
            return labels, ("data", None)      # a script reads the heredoc
        return labels, ("code", "python")

    if name in ("bash", "sh", "zsh", "dash"):
        k = i + 1
        while k < n:
            v = values[k]
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", v):
                if k + 1 < n:
                    labels[k + 1] = ("code", "bash")
                return labels, ("data", None)
            if v == "-s":
                return labels, ("code", "bash")
            if v.startswith("-"):
                k += 2 if v in ("-o", "+o", "-O", "+O") else 1
                continue
            return labels, ("data", None)
        return labels, ("code", "bash")

    if name in ("pwsh", "powershell"):
        for k in rest:
            v = values[k].lower()
            if v == "-c" or (len(v) >= 4 and "-command".startswith(v)):
                _label_rest(labels, k + 1, ("code", "pwsh"))
                if k + 1 < n and values[k + 1] == "-":
                    return labels, ("code", "pwsh")
                return labels, ("data", None)
            if v in ("-f", "-file"):
                break
        return labels, ("data", None)

    if name == "cmd":
        for k in rest:
            if values[k].lower() in ("/c", "/k", "//c", "//k"):
                _label_rest(labels, k + 1, ("code", "cmd"))
                break
        return labels, ("data", None)

    if name in ("echo", "printf"):
        _label_rest(labels, i + 1, ("data", None))
        return labels, ("data", None)

    if name == "git":
        k = i + 1
        while k < n and values[k].startswith("-"):
            k += 2 if values[k] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
        if k < n and values[k] in ("commit", "tag", "notes", "merge"):
            k += 1
            while k < n:
                v = values[k]
                if v == "--":
                    break
                if v in ("-m", "--message") or re.fullmatch(r"-[a-zA-Z]*m", v):
                    if k + 1 < n:
                        labels[k + 1] = ("data", None)
                    k += 2
                    continue
                if v.startswith("--message=") or re.fullmatch(r"-m.+", v):
                    labels[k] = ("data", None)
                k += 1
        return labels, ("data", None)

    if name == "gh":
        for k in rest:
            v = values[k]
            if v in ("--body", "-b", "--title", "-t", "--notes", "-n") and k + 1 < n:
                labels[k + 1] = ("data", None)
            elif v.startswith(("--body=", "--title=", "--notes=")):
                labels[k] = ("data", None)
        return labels, ("data", None)

    if name in ("grep", "egrep", "fgrep", "rg", "sed", "awk", "gawk", "mawk"):
        have_pattern = False
        k = i + 1
        while k < n:
            v = values[k]
            if v == "--":
                if not have_pattern and k + 1 < n:
                    labels[k + 1] = ("data", None)
                break
            if name in ("sed",) and v in ("-e", "--expression") or \
               name not in ("sed", "awk", "gawk", "mawk") and \
               (v in ("-e", "--regexp") or re.fullmatch(r"-[a-zA-Z]*e", v)):
                if k + 1 < n:
                    labels[k + 1] = ("data", None)
                have_pattern = True
                k += 2
                continue
            if v.startswith(("--regexp=", "--expression=")):
                labels[k] = ("data", None)
                have_pattern = True
            elif v in ("-f", "--file"):
                have_pattern = True
                k += 2
                continue
            elif name in ("awk", "gawk", "mawk") and v in ("-F", "-v"):
                k += 2
                continue
            elif name not in ("sed", "awk", "gawk", "mawk") and v in _PATTERN_VALUE_OPTS:
                k += 2
                continue
            elif v.startswith("-") and v != "-":
                pass
            elif not have_pattern:
                labels[k] = ("data", None)
                have_pattern = True
            k += 1
        return labels, ("data", None)

    return labels, ("data", None)


class _Parser:
    def __init__(self, text):
        self.s = text
        self.n = len(text)
        self.labels = [None] * self.n
        self.redirects = []
        self.pending = []     # heredocs waiting for the end of their line
        self.verbatim = False

    def label(self, a, b, lab):
        """Label positions [a, b) that no inner construct labeled yet."""
        labels = self.labels
        for k in range(a, min(b, self.n)):
            if labels[k] is None:
                labels[k] = lab

    def label_word(self, word, kind, lang):
        pos = word.start
        for a, b, quote in word.pieces:
            self.label(pos, a, (kind, lang, "", False))
            self.label(a, b, (kind, lang, quote, False))
            pos = b
        self.label(pos, word.end, (kind, lang, "", False))

    def finish(self, sc):
        kinds, sc.heredoc = _classify_words([w.value for w in sc.words])
        for word, (kind, lang) in zip(sc.words, kinds):
            self.label_word(word, kind, lang)
        for word in sc.targets:
            self.label_word(word, "arg", None)

    def scan_list(self, i, term, depth):
        """Scan commands from i until term (")" or "`", None = end of text).
        Returns the index after the terminator."""
        s, n = self.s, self.n
        sc = _SimpleCommand()
        while i < n:
            c = s[i]
            if term is not None and c == term:
                self.finish(sc)
                self.label(i, i + 1, _OP)
                return i + 1
            if c in " \t\r":
                self.label(i, i + 1, _OP)
                i += 1
                continue
            if c == "\\" and s.startswith("\n", i + 1):
                self.label(i, i + 2, _OP)
                i += 2
                continue
            if c == "\n":
                self.finish(sc)
                sc = _SimpleCommand()
                self.label(i, i + 1, _OP)
                i = self.read_heredoc_bodies(i + 1)
                continue
            if c in ";|" or (c == "&" and not s.startswith(">", i + 1)):
                j = i + 2 if s[i:i + 2] in ("&&", "||", ";;", "|&", ";&") else i + 1
                self.finish(sc)
                sc = _SimpleCommand()
                self.label(i, j, _OP)
                i = j
                continue
            if c == "#":
                e = s.find("\n", i)
                e = n if e < 0 else e
                self.label(i, e, _COMMENT)
                if depth == 0 and _VERBATIM_RE.fullmatch(s, i, e):
                    self.verbatim = True
                i = e
                continue
            m = _REDIRECT_RE.match(s, i)
            if m and not (m.group() in ("<", ">") and s.startswith("(", m.end())):
                i = self.read_redirect(m, sc, term, depth)
                continue
            if c == "(":
                self.label(i, i + 1, _OP)
                i = self.scan_list(i + 1, ")", depth + 1)
                continue
            word, j = self.read_word(i, term, depth)
            if j == i:                      # nothing consumed: treat as an operator
                self.label(i, i + 1, _OP)
                i += 1
                continue
            sc.words.append(word)
            i = j
        self.finish(sc)
        return n

    def read_redirect(self, m, sc, term, depth):
        s, n = self.s, self.n
        op = m.group().lstrip("0123456789")
        self.label(m.start(), m.end(), _OP)
        i = m.end()
        while i < n and s[i] in " \t":
            self.label(i, i + 1, _OP)
            i += 1
        if i >= n or s[i] in "\n;&|" or s[i] == term:
            return i
        word, j = self.read_word(i, term, depth)
        if j == i:
            return i
        if op in ("<<", "<<-"):
            self.label(word.start, word.end, _OP)
            quoted = bool(word.pieces) or "\\" in word.raw
            self.pending.append((word.value, op == "<<-", quoted, sc))
        else:
            sc.targets.append(word)
            self.redirects.append((m.start(), m.end(), word.start, word.end))
        return j

    def read_heredoc_bodies(self, i):
        s, n = self.s, self.n
        pending, self.pending = self.pending, []
        for delim, strip_tabs, quoted, sc in pending:
            kind, lang = sc.heredoc
            lab = (kind, lang, "<<'" if quoted else "<<", True)
            body_start = j = i
            while True:
                if j >= n:
                    self.label(body_start, n, lab)
                    i = n
                    break
                e = s.find("\n", j)
                e = n if e < 0 else e
                line = s[j:e]
                if strip_tabs:
                    line = line.lstrip("\t")
                if line.rstrip("\r") == delim:
                    self.label(body_start, j, lab)
                    self.label(j, min(e + 1, n), _OP)
                    i = min(e + 1, n)
                    break
                j = e + 1
        return i

    def read_word(self, i, term, depth):
        s, n = self.s, self.n
        start = i
        pieces = []
        value = []
        while i < n:
            c = s[i]
            if c in " \t\r\n;&|<>":
                if c in "<>" and i == start and s.startswith("(", i + 1):
                    i = self.scan_list(i + 2, ")", depth + 1)     # process substitution
                    continue
                break
            if term is not None and c == term:
                break
            if c == "\\":
                value.append(s[i + 1:i + 2])
                i += 2
                continue
            if c == "'":
                e = s.find("'", i + 1)
                e = n if e < 0 else e + 1
                pieces.append((i, e, "'"))
                value.append(s[i + 1:e - 1])
                i = e
                continue
            if c == "$" and s.startswith("'", i + 1):
                j = i + 2
                while j < n and s[j] != "'":
                    j += 2 if s[j] == "\\" else 1
                e = min(j + 1, n)
                pieces.append((i, e, "$'"))
                value.append(s[i + 2:e - 1])
                i = e
                continue
            if c == '"':
                e = self.read_double_quoted(i + 1, depth)
                pieces.append((i, e, '"'))
                value.append(s[i + 1:e - 1])
                i = e
                continue
            if c in "$`":
                e = self.read_expansion(i, depth)
                if e is not None:
                    value.append(s[i:e])
                    i = e
                    continue
            value.append(c)
            i += 1
        return _Word(start, i, pieces, "".join(value), s[start:i]), i

    def read_double_quoted(self, i, depth):
        """Index after the closing double quote (or the end of the text)."""
        s, n = self.s, self.n
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                return i + 1
            if c in "$`":
                e = self.read_expansion(i, depth)
                if e is not None:
                    i = e
                    continue
            i += 1
        return n

    def read_expansion(self, i, depth):
        """End of a $(...), `...`, ${...} or $((...)) starting at i, else None."""
        s = self.s
        if s.startswith("$((", i):
            return self.skip_balanced(i + 3, "(", ")", 2)
        if s.startswith("$(", i):
            return self.scan_list(i + 2, ")", depth + 1)
        if s.startswith("${", i):
            return self.skip_balanced(i + 2, "{", "}", 1)
        if s[i] == "`":
            return self.scan_list(i + 1, "`", depth + 1)
        return None

    def skip_balanced(self, i, open_ch, close_ch, level):
        s, n = self.s, self.n
        while i < n:
            if s[i] == open_ch:
                level += 1
            elif s[i] == close_ch:
                level -= 1
                if level == 0:
                    return i + 1
            i += 1
        return n


@functools.lru_cache(maxsize=64)
def parse_command(cmd):
    """Split cmd into contiguous typed spans (see the section comment)."""
    p = _Parser(cmd)
    p.scan_list(0, None, 0)
    p.read_heredoc_bodies(p.n)
    labels = p.labels
    missing = [k for k, lab in enumerate(labels) if lab is None]
    if missing:
        raise RuntimeError(f"parse_command left positions {missing[:5]} unlabeled "
                           f"in {cmd[:80]!r}")
    spans, starts = [], []
    k = 0
    while k < p.n:
        lab = labels[k]
        e = k + 1
        while e < p.n and labels[e] == lab:
            e += 1
        spans.append(Span(k, e, *lab))
        starts.append(k)
        k = e
    return Parsed(cmd, tuple(spans), tuple(starts), tuple(p.redirects), p.verbatim)


def fix_backslash_paths(cmd):
    r"""Fix F: rewrite Windows backslash paths to forward slashes.

    Matches drive-letter patterns like C:\Users, C:\\Users, C:\\\\Users
    outside single-quoted strings and heredocs.  Any run of backslashes
    is collapsed to a single forward slash -- no matter how many times
    Claude doubled them.

    The drive letter must be at a word boundary (not mid-word like
    HKCU\Software) to avoid false positives on registry paths.

    Forward slashes work for virtually all Windows executables, so this is
    safe as a silent auto-fix.  If a tool truly requires backslashes, use
    single quotes (the check skips single-quoted content).
    """
    stripped = _strip_heredocs(cmd)
    needs_fix = False
    for m in re.finditer(r"(?<![A-Za-z])([A-Za-z]):\\+([A-Za-z])", stripped):
        # Skip if inside single quotes (count odd quotes before match)
        before = stripped[:m.start()]
        if before.count("'") % 2 == 1:
            continue
        needs_fix = True
        break
    if not needs_fix:
        return cmd
    return re.sub(
        r"(?<![A-Za-z])[A-Za-z]:\\+[^\s'\"]*",
        lambda m: re.sub(r"\\+", "/", m.group(0)),
        cmd,
    )


def check_unc_paths(cmd):
    r"""Fix H: detect UNC paths with backslashes (\\server\share).

    Backslash UNC paths don't work in Git Bash. Forward-slash UNC
    paths (//server/share) work in both Git Bash and Python.
    The \\\\ must be at a word boundary to avoid false positives on
    escaped backslashes mid-word (e.g. HKCU\\Software in registry paths).
    """
    stripped = _strip_heredocs(cmd)
    for m in re.finditer(r"(?:^|(?<=[\s\"'=]))\\\\([A-Za-z0-9._-]+)\\([^\s'\"]*)", stripped):
        # Skip Python/C string escape sequences (\\x02, \\n, \\u0041, etc.)
        server = m.group(1)
        if re.match(
            r"^(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[nrtabfv0])$",
            server,
        ):
            continue
        # Skip if inside single quotes
        before = stripped[:m.start()]
        if before.count("'") % 2 == 1:
            continue
        proposed = re.sub(
            r"(?:^|(?<=[\s\"'=]))\\\\([A-Za-z0-9._-]+)\\([^\s'\"]*)",
            lambda m: "//" + m.group(1) + "/" + m.group(2).replace("\\", "/"),
            cmd,
        )
        block(f"UNC paths with backslashes don't work in Git Bash.\n"
              f"Original:  {cmd}\n"
              f"Suggested: {proposed}",
              "unc_paths", cmd, proposed)


def check_wsl_invocation(cmd):
    """Block 'wsl' commands when WSL is not installed.

    When WSL is installed, a wsl invocation is deliberate: transcript
    review showed every blocked call was genuine WSL work (Linux builds,
    Linux-only tools), so it is allowed.
    """
    if not re.match(r"wsl(\.exe)?\s", cmd.lstrip(), re.IGNORECASE):
        return
    if _is_wsl_installed():
        return
    block(
        "WSL is not installed. You are running in Git Bash on native Windows.\n"
        "Run the command directly instead of prefixing it with wsl.",
        "wsl_invocation", cmd,
    )


def fix_wsl_paths(cmd):
    """Fix P: /mnt/c/... -> C:/... in commands run by Git Bash.

    Git Bash has no /mnt, so a WSL mount path used locally always fails.
    Paths inside the arguments of ssh/adb shell (which run on Linux) and
    heredoc bodies (file content) are left alone. If the command invokes
    wsl anywhere, nothing is rewritten: /mnt paths there are typically set
    in local variables (S='/mnt/c/...'; wsl -e bash -c "... $S") that
    are expanded into the WSL command.
    """
    if _in_wsl or _WSL_ANYWHERE_RE.search(cmd):
        return cmd
    heredocs, foreign = _shell_spans(cmd)
    return _sub_outside(
        _WORD_BEFORE + r"/mnt/([a-zA-Z])/",
        lambda m: m.group(1).upper() + ":/",
        cmd,
        heredocs + foreign,
    )


_RESERVED_WORD_RE = re.compile(
    r"\b(?:" + "|".join(sorted(WINDOWS_RESERVED_NAMES)) + r")\b", re.IGNORECASE)


def _reserved_stem(target):
    """The reserved device name a redirect target or file name uses, or None.
    con, con.txt and dir/con.log all use 'con'."""
    base = target.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.split(".", 1)[0].lower()
    return stem if stem in WINDOWS_RESERVED_NAMES else None


def _shell_scripts(cmd, depth=0):
    """Yield (offset, parsed) for cmd and for every script inside it that a
    POSIX shell runs: a bash -c argument, the quoted arguments of wsl, ssh,
    plink or adb shell, and a heredoc fed to bash or to such a command.
    Nested scripts (wsl -e bash -c "ssh pi '...'") are followed 3 levels deep.
    Unquoted foreign words need no descent: bash already parsed them."""
    parsed = parse_command(cmd)
    yield 0, parsed
    if depth >= 3:
        return
    for span in parsed.spans:
        if span.kind != "foreign" and not (span.kind == "code" and span.lang == "bash"):
            continue
        start, end = span.start, span.end
        if span.heredoc:
            pass
        elif span.quote in ("'", '"') and end - start >= 2 and cmd[start] == cmd[end - 1] == span.quote:
            start, end = start + 1, end - 1
        else:
            continue
        for offset, inner in _shell_scripts(cmd[start:end], depth + 1):
            yield start + offset, inner


def _nul_redirects(cmd):
    """(operator_end, target_end) of every output redirect to nul (any case,
    optionally quoted) that a shell performs, at any nesting level. In Git
    Bash and in WSL on a Windows disk this creates a file named nul that
    Windows tools cannot delete; a Linux host has /dev/null (#62)."""
    if "nul" not in cmd.lower():
        return []
    found = set()
    for offset, parsed in _shell_scripts(cmd):
        text = parsed.text
        for op0, op1, t0, t1 in parsed.redirects:
            if ">" in text[op0:op1] and text[t0:t1].strip("\"'").lower() == "nul":
                found.add((offset + op1, offset + t1))
    return sorted(found)


def _real_nul_redirect(cmd):
    """True if cmd redirects output to nul somewhere a shell runs it, as
    opposed to mentioning '> nul' inside text."""
    return bool(_nul_redirects(cmd))


def check_reserved_names(cmd):
    """Block redirects to Windows reserved device names (other than NUL).

    NUL is handled by the tier-1 auto-fix (rewritten to /dev/null).
    Other reserved names (CON, PRN, AUX, COM1-9, LPT1-9) would create
    undeletable files in Git Bash or redirect to hardware devices.
    Also catches reserved names used as file arguments (touch con, etc.).
    """
    # Check redirects: > con, 2> prn, &> aux, > lpt1.txt, etc.
    m = _RESERVED_RE.search(cmd)
    names = [m.group(2)] if m else []
    # The regex needs the name at the end of a line, so "> con  # note" got
    # past it. The parser's redirect list sees every real redirect (#57).
    if _RESERVED_WORD_RE.search(cmd):
        for op0, op1, t0, t1 in parse_command(cmd).redirects:
            if ">" in cmd[op0:op1] and _reserved_stem(cmd[t0:t1]):
                names.append(cmd[t0:t1].strip("\"'"))
    for name in names:
        if _reserved_stem(name) != "nul":  # nul is auto-fixed in tier 1
            block(f"'{name}' is a Windows reserved device name. "
                  f"Redirecting to it will either send output to a hardware "
                  f"device or create an undeletable file.\n"
                  f"Use > /dev/null to discard output.",
                  "reserved_names", cmd)

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
            block(f"'{basename}' uses Windows reserved name '{stem}'. "
                  f"This will create an undeletable file on Windows. "
                  f"Choose a different filename.",
                  "reserved_names", cmd)


def check_powershell_legacy(cmd):
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
            "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "powershell_legacy", cmd,
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
    block(
        f"'dir {flag}' is a cmd.exe flag; PowerShell's dir (Get-ChildItem) "
        f"does not accept it and will treat it as a path.\n"
        f"Use: {suggestion}",
        "dir_in_pwsh", cmd,
    )


# ---------------------------------------------------------------------------
# Tier 1 -- auto-fixes (silently rewrite the command)
# ---------------------------------------------------------------------------

def fix_nul_redirect(cmd):
    """Fix A: > nul -> > /dev/null (case-insensitive: NUL, Nul, nul)

    Only real redirects (#62): at the top level, inside $(...), and inside
    the scripts that bash -c, wsl and ssh run, including heredocs fed to
    such a shell. Left alone where nul is right or only text: cmd /c (cmd.exe
    needs nul), python code, PowerShell, echo/JSON payloads, commit
    messages, heredocs written to a file (a .bat file) and comments.
    """
    for op_end, target_end in reversed(_nul_redirects(cmd)):
        cmd = cmd[:op_end] + " /dev/null" + cmd[target_end:]
    return cmd


_WINDOWS_ENV_VARS = {
    "USERPROFILE": "$HOME",
    "APPDATA": "$HOME/AppData/Roaming",
    "LOCALAPPDATA": "$HOME/AppData/Local",
}

_WINDOWS_ENV_NAMES = "|".join(_WINDOWS_ENV_VARS)
# $NAME must not continue into a longer variable name ($APPDATA_DIR).
_WINDOWS_ENV_RE = re.compile(
    r"\$(?:\{(" + _WINDOWS_ENV_NAMES + r")\}|(" + _WINDOWS_ENV_NAMES + r")(?!\w))",
)


def fix_windows_env_vars(cmd):
    r"""Fix O: replace Windows env vars with bash-safe equivalents.

    $USERPROFILE, $APPDATA, $LOCALAPPDATA resolve to backslash paths in
    Git Bash (C:\Users\name). When expanded inside string literals (e.g.
    python -c), backslashes cause errors (\U = Python unicode escape).
    $HOME resolves to /c/Users/name (forward slashes, safe).

    Skips commands starting with pwsh or powershell, where the Windows
    env vars are native and correct, and bodies of quoted heredocs
    (<<'EOF'), which bash does not expand.

    Credit: @kmgallahan
    """
    stripped = cmd.lstrip()
    if re.match(r"(?:pwsh|powershell)(?:\.exe)?\s", stripped, re.IGNORECASE):
        return cmd

    return _sub_outside(
        _WINDOWS_ENV_RE,
        lambda m: _WINDOWS_ENV_VARS[m.group(1) or m.group(2)],
        cmd,
        _quoted_heredoc_spans(cmd),
    )


def fix_msys2_drive_paths(cmd):
    """Fix G: /c/Work/... -> C:/Work/...

    With MSYS2_ARG_CONV_EXCL=* (a user setting, see the README) Windows
    executables like python.exe get /c/... literally and read it as
    C:\\c\\...; under Git Bash's default conversion it is converted for
    them. C:/ style works for MSYS2 tools and Windows executables under
    either setting, so the rewrite is always safe.

    Paths in the arguments of wsl/ssh/adb shell belong to the other system
    and are left alone. Heredoc bodies are still rewritten: a Python
    heredoc needs C:/ paths just like the command line does.
    """
    _, foreign = _shell_spans(cmd)
    return _sub_outside(
        r"(?:^|(?<=\s))/([a-zA-Z])/",
        lambda m: m.group(1).upper() + ":/",
        cmd,
        foreign,
    )


def _python3_is_genuine():
    """True if python3 on PATH is a real interpreter, not the Store stub.

    Without a python.org install's python3, Windows provides an App
    Execution Alias in %LOCALAPPDATA%/Microsoft/WindowsApps that only
    opens the Microsoft Store. The same folder holds the alias of a real
    Store-installed Python, in which case a PythonSoftwareFoundation
    package folder exists next to it.
    """
    found = shutil.which("python3")
    if found is None:
        return False
    parent = Path(found).parent
    if parent.name.lower() == "windowsapps" and parent.parent.name.lower() == "microsoft":
        return any(parent.glob("PythonSoftwareFoundation.Python.3*"))
    return True


def _sub_where(pattern, repl, cmd, allow, flags=0):
    """re.sub that only replaces matches where allow(parsed, pos) is true,
    parsed being parse_command(cmd) and pos the start of the match."""
    parsed = parse_command(cmd)

    def replace(m):
        if not allow(parsed, m.start()):
            return m.group(0)
        return repl(m) if callable(repl) else m.expand(repl)
    return re.sub(pattern, replace, cmd, flags=flags)


def _inner_code_span(cmd, span, pos):
    """For a position inside a bash -c '...' code word, the span of the inner
    script at that position (the script parsed on its own), else None."""
    start, end = span.start, span.end
    if span.quote in ("'", '"') and end - start >= 2 and cmd[start] == cmd[end - 1] == span.quote:
        start, end = start + 1, end - 1
    if not start <= pos < end:
        return None
    return parse_command(cmd[start:end]).at(pos - start)


def _runs_as_command(parsed, pos):
    """True if the word at pos is a command word: at the top level, inside
    $(...) or backticks, or inside the script of a bash -c argument.
    Arguments, data, python code strings, heredoc bodies, comments and
    foreign text do not count."""
    span = parsed.at(pos)
    if span is None:
        return False
    if span.kind == "cmd":
        return True
    if span.kind == "code" and span.lang == "bash" and not span.heredoc:
        inner = _inner_code_span(parsed.text, span, pos)
        return inner is not None and inner.kind == "cmd"
    return False


_PYTHON3_RE = _WORD_BEFORE + r"python3(?=(?:\.exe)?" + _WORD_AFTER + r")"


def fix_python3(cmd):
    """Fix B: python3 -> python when python3 is not a real interpreter here.

    Only where python3 runs as a command (#60): a command word, also inside
    $(...), backticks and bash -c scripts. Lookups (which python3, command -v
    python3), text (echo, commit messages, grep patterns), python code,
    heredoc bodies such as a #!/usr/bin/env python3 shebang, comments and
    wsl/ssh/adb shell arguments (the other system may need python3) are left
    alone. The step C measurement on #48 found every rewrite outside a
    command word to be wrong.

    python3 must be a whole word (optionally python3.exe): it is left alone
    inside file names, paths and identifiers such as python3-store.png,
    /usr/bin/python3.9 or python3_helper.
    """
    if "python3" not in cmd or _python3_is_genuine():
        return cmd
    return _sub_where(_PYTHON3_RE, "python", cmd, _runs_as_command)


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


# PowerShell approved verbs -- used to detect bare cmdlets like Get-ChildItem
# https://learn.microsoft.com/en-us/powershell/scripting/developer/cmdlet/approved-verbs-for-windows-powershell-commands
_PS_APPROVED_VERBS = frozenset({
    # Common
    "Add", "Clear", "Close", "Copy", "Enter", "Exit", "Find", "Format",
    "Get", "Hide", "Join", "Lock", "Move", "New", "Open", "Optimize",
    "Pop", "Push", "Redo", "Remove", "Rename", "Reset", "Resize",
    "Search", "Select", "Set", "Show", "Skip", "Split", "Step",
    "Switch", "Undo", "Unlock", "Watch",
    # Communications
    "Connect", "Disconnect", "Read", "Receive", "Send", "Write",
    # Data
    "Backup", "Checkpoint", "Compare", "Compress", "Convert",
    "ConvertFrom", "ConvertTo", "Dismount", "Edit", "Expand", "Export",
    "Group", "Import", "Initialize", "Limit", "Merge", "Mount", "Out",
    "Publish", "Restore", "Save", "Sync", "Unpublish", "Update",
    # Diagnostic
    "Debug", "Measure", "Ping", "Repair", "Resolve", "Test", "Trace",
    # Lifecycle
    "Approve", "Assert", "Build", "Complete", "Confirm", "Deny",
    "Deploy", "Disable", "Enable", "Install", "Invoke", "Register",
    "Request", "Restart", "Resume", "Start", "Stop", "Submit",
    "Suspend", "Uninstall", "Unregister", "Wait",
    # Security
    "Block", "Grant", "Protect", "Revoke", "Unblock", "Unprotect",
})

_BARE_CMDLET_RE = re.compile(r'^([A-Z][a-zA-Z]*)-([A-Z][a-zA-Z]+)\b')


def fix_bare_pwsh_cmdlet(cmd):
    """Fix M: wrap bare PowerShell cmdlets with pwsh -NoProfile -Command.

    Claude sometimes emits bare PS cmdlets (Get-ChildItem, Test-Path) that
    fail in Git Bash. Wraps them with pwsh -NoProfile -Command '...'.
    Only triggers for approved PowerShell verbs to avoid false positives
    on things like VB-Script or OAuth-Token.

    Inspired by @kmgallahan's Bash Syntax Auto-Corrector Hook:
    https://github.com/anthropics/claude-code/issues/4928#issuecomment-3848600448
    """
    stripped = cmd.lstrip()
    if stripped.lower().startswith('pwsh'):
        return cmd

    m = _BARE_CMDLET_RE.match(stripped)
    if not m:
        return cmd

    verb = m.group(1)
    if verb not in _PS_APPROVED_VERBS:
        return cmd

    # Escape single quotes for PowerShell ('  -> '')
    escaped = stripped.replace("'", "''")
    return f"pwsh -NoProfile -Command '{escaped}'"


def fix_pwsh_noprofile(cmd):
    """Fix N: insert -NoProfile into pwsh -Command invocations.

    Profile loading adds latency and can cause side-effects. When Claude
    writes `pwsh -Command '...'` without -NoProfile, inject it.

    Inspired by @kmgallahan's Bash Syntax Auto-Corrector Hook:
    https://github.com/anthropics/claude-code/issues/4928#issuecomment-3848600448
    """
    stripped = cmd.lstrip()
    if not re.match(r'^pwsh(?:\.exe)?\s', stripped, re.IGNORECASE):
        return cmd

    # Must have -Command or -c
    if not re.search(r'\s-(?:Command|c)\b', stripped, re.IGNORECASE):
        return cmd

    # Already has -NoProfile or -nop
    if re.search(r'-(?:NoProfile|nop)\b', stripped, re.IGNORECASE):
        return cmd

    # Insert -NoProfile after pwsh
    return re.sub(
        r'^(pwsh(?:\.exe)?)\s',
        r'\1 -NoProfile ',
        stripped,
        flags=re.IGNORECASE,
    )


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


def fix_start_command(cmd):
    """Fix K: start "" "path" -> python -c "import os; os.startfile('path')".

    'start' is a cmd.exe built-in. Git Bash has to spawn cmd.exe to run it.
    os.startfile() calls ShellExecuteW directly -- no extra process.
    """
    m = re.match(
        r'^start\s+""\s+"([^"]+)"$',
        cmd.strip(),
    )
    if not m:
        # Also match without the empty title: start "path"
        m = re.match(
            r'^start\s+"([^"]+)"$',
            cmd.strip(),
        )
    if not m:
        # Unquoted: start path (no spaces in path)
        m = re.match(
            r'^start\s+(\S+)$',
            cmd.strip(),
        )
    if not m:
        return cmd
    path = m.group(1).replace("\\", "/")
    escaped = path.replace("'", "\\'")
    return f"python -c \"import os; os.startfile('{escaped}')\""


def fix_cmd_cd(cmd):
    """Fix L: inject cd /d into cmd /c so cmd.exe starts in the right directory.

    cmd.exe doesn't reliably inherit Git Bash's CWD, so `cmd /c build.bat`
    fails when run from a cd'd directory. Uses `cd /d` so cross-drive
    switches work (plain `cd` silently fails across drives). This fix:

    1. `cd DIR && cmd /c args [suffix]` -> `cmd /c "cd /d DIR && args" [suffix]`
    2. `cmd /c "cd DIR && args"`        -> `cmd /c "cd /d DIR && args"`
    3. `cmd /c args` (no preceding cd)  -> `cmd /c "cd /d <cwd> && args"`

    The suffix (2>&1, |, etc.) is preserved outside the quoted block.
    Outer quotes on args are unwrapped BEFORE suffix splitting to prevent
    the suffix regex from matching && inside quoted cmd.exe commands.
    """
    # Match optional `cd DIR &&` prefix, then `cmd /c`, then args
    # The /c flag may have been doubled (//c) and already fixed by fix_doubled_flags
    m = re.match(
        r'^(?:cd\s+("(?:[^"]+)"|[^\s&]+)\s*&&\s*)?'  # optional: cd DIR &&
        r'cmd(?:\.exe)?\s+/c\s+'                       # cmd /c
        r'(.*)',                                         # args (greedy)
        cmd,
        re.IGNORECASE,
    )
    if not m:
        return cmd

    cd_dir = m.group(1)  # None if no preceding cd
    args_and_suffix = m.group(2).strip()

    if not args_and_suffix:
        return cmd

    # Unwrap outer quotes BEFORE splitting suffix -- prevents the suffix
    # regex from matching && inside quoted args like "cd DIR && build.bat"
    if args_and_suffix.startswith('"'):
        close_idx = args_and_suffix.find('"', 1)
        if close_idx > 0:
            args = args_and_suffix[1:close_idx]
            suffix = args_and_suffix[close_idx + 1:]
        else:
            args = args_and_suffix[1:]
            suffix = ''
    else:
        # Split trailing redirections/pipes from the args
        # Match: 2>&1, >&2, |, || ..., && ... at the end
        suffix_m = re.search(r'\s+((?:[12]>&[12]|[|>]|&&\s).*)$', args_and_suffix)
        if suffix_m:
            args = args_and_suffix[:suffix_m.start()].strip()
            suffix = ' ' + suffix_m.group(1)
        else:
            args = args_and_suffix
            suffix = ''

    # Check if args themselves start with "cd [/d] DIR &&" (cd inside cmd /c quotes)
    inner_cd = re.match(
        r'^cd(?:\s+/d)?\s+("(?:[^"]+)"|[^\s&]+)\s*&&\s*(.*)',
        args,
        re.IGNORECASE,
    )
    if inner_cd and not cd_dir:
        inner_dir = inner_cd.group(1)
        if inner_dir.startswith('"') and inner_dir.endswith('"'):
            inner_dir = inner_dir[1:-1]
        directory = inner_dir
        args = inner_cd.group(2).strip()
    elif cd_dir:
        # Strip quotes from cd dir if present
        if cd_dir.startswith('"') and cd_dir.endswith('"'):
            cd_dir = cd_dir[1:-1]
        directory = cd_dir
    else:
        directory = os.getcwd().replace('\\', '/')

    # Prepend .\ to bare .bat/.cmd scripts so cmd.exe finds them in the
    # current directory even when NoDefaultCurrentDirectoryInExePath is set.
    first_token = args.split()[0] if args.split() else ''
    if re.match(r'^[^/\\]+\.(bat|cmd)$', first_token, re.IGNORECASE):
        args = r'.\{}'.format(args)

    return f'cmd /c "cd /d {directory} && {args}"{suffix}'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Blocking checks, in order: (check id, function, default when not configured)
_CHECKS = [
    ("git_commit_attribution", check_git_commit_attribution, False),
    ("git_commit_generated", check_git_commit_generated, False),
    ("git_commit_emoji", check_git_commit_emoji, False),
    ("powershell_legacy", check_powershell_legacy, None),
    ("wsl_invocation", check_wsl_invocation, None),
    ("dir_in_pwsh", check_dir_in_pwsh, None),
    ("reserved_names", check_reserved_names, None),
    ("unc_paths", check_unc_paths, None),
]

# Auto-fixes, in order: each feeds its output to the next (see CLAUDE.md for
# order dependencies, e.g. doubled_flags before cmd_cd).
_FIXES = [
    ("doubled_flags", fix_doubled_flags, "replaced doubled // flags with single /"),
    ("nul_redirect", fix_nul_redirect, "replaced > nul with > /dev/null"),
    ("windows_env_vars", fix_windows_env_vars, "replaced Windows env vars with bash-safe equivalents"),
    ("backslash_paths", fix_backslash_paths, "converted backslash paths to forward slashes"),
    ("msys2_drive_paths", fix_msys2_drive_paths, "converted MSYS2 drive paths to Windows style"),
    ("wsl_paths", fix_wsl_paths, "converted /mnt/ WSL paths to Windows style"),
    ("python3", fix_python3, "replaced python3 with python"),
    ("dir_windows_flags", fix_dir_windows_flags, "converted Windows dir /flags to ls equivalent"),
    ("bare_pwsh_cmdlet", fix_bare_pwsh_cmdlet, "wrapped bare PowerShell cmdlet with pwsh -NoProfile -Command"),
    ("pwsh_noprofile", fix_pwsh_noprofile, "inserted -NoProfile into pwsh invocation"),
    ("pwsh_quoting", fix_pwsh_quoting, "swapped pwsh -Command quotes from double to single"),
    ("start_command", fix_start_command, "replaced start with os.startfile()"),
    ("cmd_cd", fix_cmd_cd, "injected cd into cmd /c for correct working directory"),
]

VERBATIM_HINT = ("If a rewrite was wrong, run the command again with a trailing "
                 "`# hook:verbatim` comment; the user will be asked to confirm.")
MAX_VERBATIM_EXCERPTS = 5


def _run_checks(command):
    for check_id, fn, default in _CHECKS:
        if _is_enabled(check_id, default=default):
            fn(command)


def _run_fixes(command):
    """Apply the enabled fixes in order.

    Returns (command, steps, error): steps is a list of
    (check_id, description, before, after) for each fix that changed the
    command; error is (check_id, message) when a fix refuses to rewrite and
    the command must be blocked (fix_pwsh_quoting), else None.
    """
    steps = []
    for check_id, fn, description in _FIXES:
        if not _is_enabled(check_id):
            continue
        before = command
        if fn is fix_pwsh_quoting:
            command, err = fn(command)
            if err:
                return command, steps, (check_id, err)
        else:
            command = fn(command)
        if command != before:
            steps.append((check_id, description, before, command))
    return command, steps, None


def _excerpt(text, start, end, limit=60):
    """The words of text around [start, end), on one line, cut to limit."""
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    s = text[start:end].replace("\n", "\\n")
    return s if len(s) <= limit else s[:limit - 3] + "..."


def _rewrite_excerpts(before, after):
    """Distinct 'old -> new' excerpts for the changes between two commands."""
    seen = []
    sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        pair = f"{_excerpt(before, i1, i2)} -> {_excerpt(after, j1, j2)}"
        if pair not in seen:
            seen.append(pair)
    return seen


def _handle_verbatim(command):
    """The command carries a top-level # hook:verbatim comment: run it as
    written, but only with the user's approval when that skips a fix (#57).
    Blocking checks have already run. Always exits."""
    if _is_enabled("nul_redirect") and _real_nul_redirect(command):
        block("'> nul' creates a file named nul in Git Bash that Windows cannot "
              "delete; # hook:verbatim does not override this. "
              "Use > /dev/null to discard output.",
              "nul_redirect", command)

    fixed, steps, error = _run_fixes(command)
    items = []
    for _, description, before, after in steps:
        items += [f"{description}: {pair}" for pair in _rewrite_excerpts(before, after)]
    shown = items[:MAX_VERBATIM_EXCERPTS]
    if len(items) > len(shown):
        shown.append(f"and {len(items) - len(shown)} more")
    if error:
        shown.append(f"pwsh -Command quoting check: {error[1]}")

    fix_ids = [s[0] for s in steps] + ([error[0]] if error else [])
    if not fix_ids:
        _log_entry("verbatim", "none", command, decision="noop")
        sys.exit(0)

    _log_entry("verbatim", ",".join(fix_ids), command,
               proposed=fixed if steps else None, decision="ask")
    reason = ("# hook:verbatim: run this command exactly as written? Without the "
              "marker the hook would change it: " + "; ".join(shown) + ". "
              "If this is not confirmed, the command does not run; drop the "
              "marker to accept the rewrite instead.")
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    sys.exit(0)


def main():
    input_data = read_hook_input()

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    _run_checks(command)

    if "hook:verbatim" in command and parse_command(command).verbatim:
        _handle_verbatim(command)

    fixed, steps, error = _run_fixes(command)
    if error:
        block(error[1], error[0], fixed)

    if steps:
        fixes = [description for _, description, _, _ in steps]
        log_autofix(command, fixed, fixes)
        # updatedInput replaces the whole tool input: pass every field through
        # (timeout, run_in_background, description) and change only the command.
        updated = dict(tool_input)
        updated["command"] = fixed
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated,
                "additionalContext": "Hook auto-fixed: " + ", ".join(fixes)
                    + ". " + VERBATIM_HINT,
            }
        }
        json.dump(output, sys.stdout)

    sys.exit(0)

if __name__ == "__main__":
    main()

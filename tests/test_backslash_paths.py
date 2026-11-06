r"""Tests for fix_backslash_paths (#74).

C:\... becomes C:/... in command words and arguments whatever the quoting:
forward slashes work for Windows programs, and the step C measurement on #48
found 45 single-quoted rewrites (SP='C:\...'), all harmless. In code only
where bash processes backslashes (unquoted, double quotes, unquoted
heredocs); code bash passes on byte for byte is valid as it is. Text,
heredocs written to a file (a .bat needs backslashes), comments and wsl/ssh
arguments are left alone. # hook:verbatim is the escape hatch, not single
quotes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from fix_bash_command import fix_backslash_paths


class TestRewritten:
    @pytest.mark.parametrize("cmd, expected", [
        (r"ls C:\Users\me", "ls C:/Users/me"),
        (r'ls "C:\Users\me"', 'ls "C:/Users/me"'),
        (r"ls C:\\Users\\me", "ls C:/Users/me"),
        (r"ls C:\\\\Users\\\\me", "ls C:/Users/me"),
        # single quotes: rewritten too, the same in any context
        (r"ls 'C:\Users\me'", "ls 'C:/Users/me'"),
        (r"""SP='C:\Users\me\AppData\Local\Temp'; ls "$SP" """,
         """SP='C:/Users/me/AppData/Local/Temp'; ls "$SP" """),
        # code where bash processes the backslashes: double quotes, unquoted
        # heredocs
        (r"""python -c "open('C:\\Users\\x.txt')" """, """python -c "open('C:/Users/x.txt')" """),
        ("python - <<EOF\nopen(r'C:\\Users\\x.txt')\nEOF",
         "python - <<EOF\nopen(r'C:/Users/x.txt')\nEOF"),
        (r'cmd /c "type C:\Work\a.txt"', 'cmd /c "type C:/Work/a.txt"'),
        (r'pwsh -NoProfile -Command "Get-Item C:\Work"', 'pwsh -NoProfile -Command "Get-Item C:/Work"'),
        (r"cd D:\src && make", "cd D:/src && make"),
    ])
    def test_rewritten(self, cmd, expected):
        assert fix_backslash_paths(cmd) == expected


class TestLeftAlone:
    @pytest.mark.parametrize("cmd", [
        # text
        r'echo "C:\Users\me"',
        r'git commit -m "see C:\Users\me"',
        r"git commit -m 'see C:\Users\me'",
        r'grep -rn "C:\\Users" logs/',
        r"ls  # was C:\Users\me",
        # heredoc written to a file: a .bat needs its backslashes
        "cat > build.bat <<'EOF'\ncd C:\\Work\\src\nEOF",
        # code that reaches the interpreter byte for byte: a backslash path
        # is valid there and may be compared or replaced as a string
        "python - <<'EOF'\nopen(r'C:\\Users\\x.txt')\nEOF",
        "python - <<'EOF'\ns = s.replace(r'C:\\old', r'C:\\new')\nEOF",
        r"""python -c 'open(r"C:\Users\x.txt")'""",
        r"pwsh -NoProfile -Command 'Get-Item C:\Work'",
        # another system
        r"ssh host 'type C:\Work\a.txt'",
        # not a drive path
        r"reg query HKLM\Software\Microsoft",
        r"ls \\server\share",
    ])
    def test_unchanged(self, cmd):
        assert fix_backslash_paths(cmd) == cmd

    def test_heredoc_file_kept_when_command_line_is_rewritten(self):
        # v1: one path on the command line rewrote the .bat body too
        cmd = "cd C:\\Work && cat > build.bat <<'EOF'\ncd C:\\Work\\src\nEOF"
        assert fix_backslash_paths(cmd) == "cd C:/Work && cat > build.bat <<'EOF'\ncd C:\\Work\\src\nEOF"

    def test_text_kept_when_command_line_is_rewritten(self):
        cmd = r'ls C:\Work && echo "C:\Work done"'
        assert fix_backslash_paths(cmd) == 'ls C:/Work && echo "C:\\Work done"'

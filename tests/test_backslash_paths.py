r"""Tests for fix_backslash_paths (#74, #81).

C:\... becomes C:/... only where bash damages it: unquoted, in a command word,
an argument or code (`ls C:\Work\x` reaches ls as C:Workx). Inside quotes the
path reaches the program as Claude meant it (single quotes byte for byte;
double quotes and unquoted heredocs turn \\ into \, a single \W is kept), so
it is left alone: the hook cannot know whether the receiving program needs
backslashes (#81: a VMware shared folder, paths for a remote machine). Text,
heredocs written to a file, comments and wsl/ssh arguments are left alone too.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from fix_bash_command import fix_backslash_paths


class TestRewritten:
    @pytest.mark.parametrize("cmd, expected", [
        (r"ls C:\Users\me", "ls C:/Users/me"),
        (r"ls C:\\Users\\me", "ls C:/Users/me"),
        (r"ls C:\\\\Users\\\\me", "ls C:/Users/me"),
        (r"cd D:\src && make", "cd D:/src && make"),
        (r"python C:\Work\tools\ext4ls.py 2>&1 | head -40", "python C:/Work/tools/ext4ls.py 2>&1 | head -40"),
        # unquoted code: bash strips the backslashes before cmd.exe sees them
        (r"cmd /c type C:\Work\a.txt", "cmd /c type C:/Work/a.txt"),
        # only the unquoted part of a word
        (r"""ls C:\Work\x 'C:\Work\y'""", r"""ls C:/Work/x 'C:\Work\y'"""),
    ])
    def test_rewritten(self, cmd, expected):
        assert fix_backslash_paths(cmd) == expected


class TestQuotedLeftAlone:
    """#81: bash passes a quoted backslash path on intact."""

    @pytest.mark.parametrize("cmd", [
        r'ls "C:\Users\me"',
        r"ls 'C:\Users\me'",
        r"""SP='C:\Users\me\AppData\Local\Temp'; ls "$SP" """,
        # seen live: a VMware shared folder needs the Windows form
        r'"$VMRUN" -T ws addSharedFolder "$VMX" vmshare "C:\\Users\\me\\src\\vmshare"',
        # seen live: half rewritten at the space before
        r'VMX="C:\\Users\\me\\Documents\\Virtual Machines\\Windows Server 2019\\WS2019.vmx"',
        r"JAVA='C:\Program Files\Eclipse Adoptium\jdk-25\bin\java.exe'",
        # seen live: paths used on a remote machine
        r"./poc.exe 192.168.50.128 me 'C:\Users\me\vmshare' --shell 'C:\Marshal\helper.exe'",
        r"""'C:\Tools\jadx\bin\jadx.bat' -d out in.apk""",
        # code in double quotes or an unquoted heredoc: bash keeps the path
        r"""python -c "open(r'C:\Users\x.txt')" """,
        r"""python -c "open('C:\\Users\\x.txt')" """,
        "python - <<EOF\nopen(r'C:\\Users\\x.txt')\nEOF",
        r'cmd //c "C:\\Work\\app\\native\\build.bat"',
        r'cmd /c "type C:\Work\a.txt"',
        r'pwsh -NoProfile -Command "Get-Item C:\Work"',
        r"""powershell -NoProfile -Command "(Get-Item 'C:\Program Files\totalcmd\x.dll').VersionInfo" """,
    ])
    def test_unchanged(self, cmd):
        assert fix_backslash_paths(cmd) == cmd


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
        # code that reaches the interpreter byte for byte
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

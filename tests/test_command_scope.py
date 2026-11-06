"""Tests for WSL handling, foreign-span scoping and python3 detection.

Cases are taken from real commands seen in Claude Code transcripts.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import fix_bash_command as fbc

WSL = "C:/Windows/System32/wsl.exe"


@pytest.fixture
def python3_is_stub(monkeypatch):
    """Make python3 detection report 'not genuine' so the rewrite applies."""
    monkeypatch.setattr(fbc, "_python3_is_genuine", lambda: False, raising=False)


# ---------------------------------------------------------------------------
# #36 -- WSL invocation and /mnt paths
# ---------------------------------------------------------------------------

class TestWslInvocation:
    def test_allowed_when_wsl_installed(self, monkeypatch):
        monkeypatch.setattr(fbc, "_is_wsl_installed", lambda: True)
        fbc.check_wsl_invocation("wsl -e sh -c 'gcc --version'")
        fbc.check_wsl_invocation("wsl.exe -e bash -lc 'python3 x.py'")

    def test_blocked_when_wsl_missing(self, monkeypatch):
        monkeypatch.setattr(fbc, "_is_wsl_installed", lambda: False)
        with pytest.raises(SystemExit) as exc:
            fbc.check_wsl_invocation("wsl -e sh -c 'gcc --version'")
        assert exc.value.code == 2


class TestFixWslPaths:
    def test_local_mnt_path_autofixed(self):
        assert fbc.fix_wsl_paths("ls /mnt/c/Users/me") == "ls C:/Users/me"

    def test_inside_wsl_argument_untouched(self):
        cmd = f"cd /c/Work/BMW/nbt-evo-apps; {WSL} -e sh -c 'cd /mnt/c/Work/BMW/nbt-evo-apps && make'"
        assert fbc.fix_wsl_paths(cmd) == cmd

    def test_bare_wsl_argument_untouched(self):
        cmd = "wsl -e bash -c \"gcc -o /tmp/x /mnt/c/Work/hello.c\""
        assert fbc.fix_wsl_paths(cmd) == cmd

    def test_timeout_prefix_wsl_untouched(self):
        cmd = f'timeout 20 {WSL} -e sh -c "cd /mnt/c/Work/x && ./run"'
        assert fbc.fix_wsl_paths(cmd) == cmd

    def test_any_wsl_in_command_leaves_mnt_paths(self):
        cmd = f"{WSL} -e bash -lc 'ls /mnt/c/x'; ls /mnt/d/y"
        assert fbc.fix_wsl_paths(cmd) == cmd

    def test_local_variable_for_wsl_untouched(self):
        """Seen in a transcript: /mnt paths in variables expanded into wsl."""
        cmd = ("S='/mnt/c/Users/me/scratchpad'; R='/mnt/c/Work/hello_nbt'; "
               "wsl -e bash -c \"\nset -u\ngcc -o $S/hello $R/hello.c\n\"")
        assert fbc.fix_wsl_paths(cmd) == cmd

    def test_word_containing_wsl_does_not_disable_fix(self):
        assert fbc.fix_wsl_paths("ls /mnt/c/wsl_notes") == "ls C:/wsl_notes"

    def test_heredoc_body_untouched(self):
        cmd = "cat > run.sh <<'EOF'\ncd /mnt/c/Work\nEOF"
        assert fbc.fix_wsl_paths(cmd) == cmd

    def test_ssh_argument_untouched(self):
        cmd = "ssh pi 'ls /mnt/d/backup'"
        assert fbc.fix_wsl_paths(cmd) == cmd


# ---------------------------------------------------------------------------
# #37 -- fixes must not touch text that runs elsewhere
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("python3_is_stub")
class TestPython3Scope:
    def test_local_rewritten(self):
        assert fbc.fix_python3("python3 script.py") == "python script.py"

    def test_heredoc_command_line_rewritten(self):
        cmd = "python3 -I - <<'PY'\nprint(1)\nPY"
        assert fbc.fix_python3(cmd) == "python -I - <<'PY'\nprint(1)\nPY"

    def test_version_path_untouched(self):
        cmd = "ls /usr/bin/python3.9"
        assert fbc.fix_python3(cmd) == cmd

    def test_inside_wsl_untouched(self):
        cmd = f'{WSL} -e sh -c "command -v python3; python3 --version"'
        assert fbc.fix_python3(cmd) == cmd

    def test_wsl_after_pipe_untouched(self):
        cmd = f"cat x.py | {WSL} -e bash -lc 'python3 - /home/me/System.map'"
        assert fbc.fix_python3(cmd) == cmd

    def test_ssh_payload_untouched(self):
        cmd = "ssh pi 'sudo python3 -c \"import usb.core\"'"
        assert fbc.fix_python3(cmd) == cmd

    def test_local_part_fixed_next_to_ssh(self):
        cmd = 'ssh pi "cd /tmp; python3 x.py"; python3 y.py'
        assert fbc.fix_python3(cmd) == 'ssh pi "cd /tmp; python3 x.py"; python y.py'

    def test_shebang_in_heredoc_untouched(self):
        cmd = "cat > tool.py <<'EOF'\n#!/usr/bin/env python3\nprint(1)\nEOF\npython3 tool.py"
        expected = "cat > tool.py <<'EOF'\n#!/usr/bin/env python3\nprint(1)\nEOF\npython tool.py"
        assert fbc.fix_python3(cmd) == expected


class TestMsys2Scope:
    def test_local_rewritten(self):
        assert fbc.fix_msys2_drive_paths("cd /c/Work && ls") == "cd C:/Work && ls"

    def test_inside_wsl_untouched(self):
        cmd = f"{WSL} -e sh -c 'ls /c/foo'"
        assert fbc.fix_msys2_drive_paths(cmd) == cmd

    def test_ssh_argument_untouched(self):
        cmd = "cd /c/Work && ssh pi 'ls /d/data'"
        assert fbc.fix_msys2_drive_paths(cmd) == "cd C:/Work && ssh pi 'ls /d/data'"

    def test_adb_shell_untouched(self):
        cmd = "adb -s emulator-5580 shell ls /d/tracing"
        assert fbc.fix_msys2_drive_paths(cmd) == cmd

    def test_heredoc_body_still_rewritten(self):
        cmd = "cat > notes.txt <<'EOF'\nsee /c/Work\nEOF"
        assert fbc.fix_msys2_drive_paths(cmd) == "cat > notes.txt <<'EOF'\nsee C:/Work\nEOF"


class TestNulScope:
    def test_local_rewritten(self):
        assert fbc.fix_nul_redirect("ping x > nul 2>&1") == "ping x > /dev/null 2>&1"

    def test_bat_heredoc_untouched(self):
        cmd = "cat > b.bat <<'EOF'\n@echo off\ndir > nul\nEOF"
        assert fbc.fix_nul_redirect(cmd) == cmd

    def test_local_fixed_heredoc_kept(self):
        cmd = "ls > nul; cat > b.bat <<'EOF'\ndir > nul\nEOF"
        assert fbc.fix_nul_redirect(cmd) == "ls > /dev/null; cat > b.bat <<'EOF'\ndir > nul\nEOF"


class TestNulRealRedirectsOnly:
    """#62: rewrite > nul only where a shell redirects to it, also in the script
    that wsl, ssh or bash -c runs (in WSL on /mnt/c it creates a file cmd and
    PowerShell cannot delete). Leave it where nul is right or only text."""

    @pytest.mark.parametrize("cmd, expected", [
        ("ls > nul", "ls > /dev/null"),
        ("ls 2>nul", "ls 2> /dev/null"),
        ("ls >NUL 2>&1", "ls > /dev/null 2>&1"),
        ("ls &> nul", "ls &> /dev/null"),
        ("ls >> nul", "ls >> /dev/null"),
        ('ls > "nul"', "ls > /dev/null"),
        ("x=$(ls 2>nul)", "x=$(ls 2> /dev/null)"),
        ("ssh pi ls > nul", "ssh pi ls > /dev/null"),                  # redirected locally
        ("wsl -e sh -c 'make > nul'", "wsl -e sh -c 'make > /dev/null'"),
        ('ssh pi "ls > nul; echo ok"', 'ssh pi "ls > /dev/null; echo ok"'),
        ("bash -c 'make 2>nul'", "bash -c 'make 2> /dev/null'"),
        ("ssh pi bash <<'EOF'\nls > nul\nEOF", "ssh pi bash <<'EOF'\nls > /dev/null\nEOF"),
        ("bash <<'EOF'\nmake > nul\nEOF", "bash <<'EOF'\nmake > /dev/null\nEOF"),
        ("wsl -e bash -c \"ssh pi 'ls > nul'\"", "wsl -e bash -c \"ssh pi 'ls > /dev/null'\""),
    ])
    def test_real_redirect_rewritten(self, cmd, expected):
        assert fbc.fix_nul_redirect(cmd) == expected

    @pytest.mark.parametrize("cmd", [
        'cmd /c "build.bat > nul"',                                     # cmd.exe needs nul
        "python -c \"a=b'timeout /t 2 /nobreak >nul'; print(a)\"",      # seen in transcripts
        """echo '{"tool_name":"Bash","tool_input":{"command":"ls > nul"}}' | python hook.py""",  # seen
        'git commit -m "Rewrite > nul only as a redirect"',
        "cat > fix.bat <<'EOF'\n@echo off\ndel x > nul\nEOF",
        "pwsh -NoProfile -Command 'Get-Date > nul'",
        "ls > /dev/null",
        "cat nul",
        "ls  # > nul would be wrong here",
    ])
    def test_nul_that_is_right_or_text_untouched(self, cmd):
        assert fbc.fix_nul_redirect(cmd) == cmd


class TestForeignSpans:
    def test_quotes_with_apostrophe_in_heredoc(self):
        """An apostrophe in a heredoc body must not confuse quote tracking."""
        cmd = "cat > a.md <<'EOF'\nit's here\nEOF\nssh pi 'ls /d/x'; ls /d/y"
        assert fbc.fix_msys2_drive_paths(cmd) == (
            "cat > a.md <<'EOF'\nit's here\nEOF\nssh pi 'ls /d/x'; ls D:/y")

    def test_semicolon_inside_quotes_does_not_end_span(self):
        cmd = "ssh pi \"cd /tmp; ls /d/x\""
        assert fbc.fix_msys2_drive_paths(cmd) == cmd


# ---------------------------------------------------------------------------
# #38 -- python3 detection
# ---------------------------------------------------------------------------

class TestPython3Detection:
    @pytest.fixture
    def apps(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        d = tmp_path / "Microsoft" / "WindowsApps"
        d.mkdir(parents=True)
        return d

    def test_missing_python3_rewritten(self, apps, monkeypatch):
        monkeypatch.setattr(fbc.shutil, "which", lambda name: None)
        assert fbc.fix_python3("python3 x.py") == "python x.py"

    def test_real_python3_kept(self, apps, monkeypatch, tmp_path):
        exe = tmp_path / "Python312" / "python3.exe"
        monkeypatch.setattr(fbc.shutil, "which", lambda name: str(exe))
        assert fbc.fix_python3("python3 x.py") == "python3 x.py"

    def test_store_stub_rewritten(self, apps, monkeypatch):
        monkeypatch.setattr(fbc.shutil, "which", lambda name: str(apps / "python3.EXE"))
        assert fbc.fix_python3("python3 x.py") == "python x.py"

    def test_store_python_kept(self, apps, monkeypatch):
        (apps / "PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0").mkdir()
        monkeypatch.setattr(fbc.shutil, "which", lambda name: str(apps / "python3.exe"))
        assert fbc.fix_python3("python3 x.py") == "python3 x.py"


# ---------------------------------------------------------------------------
# #45 -- triggers inside file names, paths and variable names
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("python3_is_stub")
class TestPython3WholeWord:
    @pytest.mark.parametrize("cmd", [
        'cp "C:/temp/python3-store.png" "C:/x/"',      # seen in a transcript
        'grep -n "python3_is_stub" tests/x.py',        # seen in a transcript
        "ls docs/python3/notes.md",
        "cat python3.md",
        "pip install python3-dateutil",
        "cd my-python3",
    ])
    def test_inside_name_untouched(self, cmd):
        assert fbc.fix_python3(cmd) == cmd

    @pytest.mark.parametrize("cmd, expected", [
        ("python3 x.py", "python x.py"),
        ("cd /tmp && python3 x.py", "cd /tmp && python x.py"),
        ("env python3 x.py", "env python x.py"),
        ("ls *.py | xargs python3 -m py_compile", "ls *.py | xargs python -m py_compile"),
        ("(python3 x.py)", "(python x.py)"),
        ("python3.exe x.py", "python.exe x.py"),
    ])
    def test_command_word_rewritten(self, cmd, expected):
        assert fbc.fix_python3(cmd) == expected


# ---------------------------------------------------------------------------
# #60 -- python3 is rewritten only where it runs as a command
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("python3_is_stub")
class TestPython3CommandWordsOnly:
    @pytest.mark.parametrize("cmd", [
        "which -a python3 python 2>/dev/null",                   # seen in transcripts
        "which python python3 7z wsl 2>&1 | head",               # seen in transcripts
        "command -v python3",
        "type python3",
        'echo "python3 is the Store stub"',
        'git commit -m "Rewrite python3 only as a command"',
        'gh issue create --title "rewrite python3 only where it runs"',   # seen today
        'grep -n "python3" README.md',
        "awk '/python3 in/{p=1}' measure.txt",                    # seen today
        'python -c "for m in re.finditer(r\'python3\', s): print(m)"',   # seen in transcripts
        # Reverses a #45 test: never seen in transcripts, while the three
        # real hits inside python code strings were all wrong.
        'python -c "import subprocess; subprocess.run([\'python3\', \'-V\'])"',
        "python - <<'PY'\nimport shutil; print(shutil.which('python3'))\nPY",
        "ls  # python3 is not installed",
    ])
    def test_text_about_python3_untouched(self, cmd):
        assert fbc.fix_python3(cmd) == cmd

    @pytest.mark.parametrize("cmd, expected", [
        ("sudo python3 x.py", "sudo python x.py"),
        ("x=$(python3 -V)", "x=$(python -V)"),
        ('echo "version: $(python3 -V)"', 'echo "version: $(python -V)"'),
        ("echo `python3 -V`", "echo `python -V`"),
        ("bash -c 'python3 x.py'", "bash -c 'python x.py'"),
        ('bash -lc "cd /tmp && python3 x.py"', 'bash -lc "cd /tmp && python x.py"'),
        ("python3 -I - <<'PY'\nprint('python3')\nPY", "python -I - <<'PY'\nprint('python3')\nPY"),
    ])
    def test_command_word_rewritten(self, cmd, expected):
        assert fbc.fix_python3(cmd) == expected

    def test_bash_code_argument_mentioning_python3_untouched(self):
        cmd = "bash -c 'echo python3'"
        assert fbc.fix_python3(cmd) == cmd


# ---------------------------------------------------------------------------
# #66 -- /mnt/c/ inside text is left alone
# ---------------------------------------------------------------------------

class TestWslPathsSkipText:
    @pytest.mark.parametrize("cmd", [
        'grep -c "S=.*/mnt/c/Users" replay.txt',                 # seen in a transcript
        # (no "wsl" in these texts: any mention of wsl skips the fix entirely)
        'echo "Linux sees this disk as /mnt/c/Users"',
        'git commit -m "Leave /mnt/c/ paths in text alone"',
        "sed 's#/mnt/c/#C:/#' notes.txt",
        "ls  # /mnt/c/Users is the Linux view",
    ])
    def test_text_untouched(self, cmd):
        assert fbc.fix_wsl_paths(cmd) == cmd

    @pytest.mark.parametrize("cmd, expected", [
        ("ls /mnt/c/Users", "ls C:/Users"),
        ("cd /mnt/d/Work && make", "cd D:/Work && make"),
        ("python -c \"open('/mnt/c/x.txt').read()\"", "python -c \"open('C:/x.txt').read()\""),
        ("grep -n pattern /mnt/c/x.txt", "grep -n pattern C:/x.txt"),
    ])
    def test_paths_rewritten(self, cmd, expected):
        assert fbc.fix_wsl_paths(cmd) == expected


# ---------------------------------------------------------------------------
# #67 -- cmd_cd keeps every line after the first
# ---------------------------------------------------------------------------

class TestCmdCdKeepsLaterLines:
    def test_commit_message_body_kept(self):
        cmd = ('cd C:/Work/project && cmd /c "native\\\\build.bat" > build/log.txt 2>&1; '
               'git commit -q -m "Subject line\n\nBody line 1\nBody line 2"')
        out = fbc.fix_cmd_cd(cmd)
        assert out.startswith('cmd /c "cd /d C:/Work/project && native\\\\build.bat"')
        assert out.endswith('-m "Subject line\n\nBody line 1\nBody line 2"')

    def test_following_commands_kept(self):
        cmd = "cd C:/Work/x && cmd /c build.bat > log.txt 2>&1\necho done\ngit status"
        assert fbc.fix_cmd_cd(cmd) == ('cmd /c "cd /d C:/Work/x && build.bat" > log.txt 2>&1'
                                       "\necho done\ngit status")


class TestWslPathsWholeWord:
    def test_mnt_inside_longer_path_untouched(self):
        cmd = "ls /srv/mnt/d/backup"
        assert fbc.fix_wsl_paths(cmd) == cmd

    def test_mnt_in_url_untouched(self):
        cmd = "curl https://example.com/mnt/c/file"
        assert fbc.fix_wsl_paths(cmd) == cmd

    @pytest.mark.parametrize("cmd, expected", [
        ("ls /mnt/c/Users", "ls C:/Users"),
        ('cat "/mnt/c/x.txt"', 'cat "C:/x.txt"'),
        ("cp a --target=/mnt/d/out", "cp a --target=D:/out"),
    ])
    def test_standalone_mnt_rewritten(self, cmd, expected):
        assert fbc.fix_wsl_paths(cmd) == expected


@pytest.fixture
def windows_env(monkeypatch):
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\me")
    monkeypatch.setenv("APPDATA", "C:\\Users\\me\\AppData\\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\me\\AppData\\Local")


@pytest.mark.usefixtures("windows_env")
class TestWindowsEnvVarsWholeWord:
    def test_longer_variable_name_untouched(self):
        cmd = 'echo "$APPDATA_DIR/x"'
        assert fbc.fix_windows_env_vars(cmd) == cmd

    @pytest.mark.parametrize("cmd, expected", [
        ('cd "$APPDATA/Code"', 'cd "C:/Users/me/AppData/Roaming/Code"'),
        ("ls ${LOCALAPPDATA}/Temp", "ls C:/Users/me/AppData/Local/Temp"),
        ("echo $USERPROFILE", "echo C:/Users/me"),
    ])
    def test_variable_rewritten(self, cmd, expected):
        assert fbc.fix_windows_env_vars(cmd) == expected

    def test_quoted_heredoc_body_untouched(self):
        """Seen in practice: a PR description written via <<'EOF' got rewritten."""
        cmd = "cat > pr.md <<'EOF'\n`$APPDATA` and $USERPROFILE stay literal\nEOF\ngh pr edit 1 -F pr.md"
        assert fbc.fix_windows_env_vars(cmd) == cmd

    def test_unquoted_heredoc_body_rewritten(self):
        """bash expands $APPDATA in an unquoted heredoc, so the backslash problem applies."""
        cmd = 'python - <<EOF\nprint("$APPDATA/x")\nEOF'
        assert fbc.fix_windows_env_vars(cmd) == 'python - <<EOF\nprint("C:/Users/me/AppData/Roaming/x")\nEOF'

    def test_command_line_next_to_quoted_heredoc_rewritten(self):
        cmd = "cd $APPDATA && cat > x <<'EOF'\n$APPDATA\nEOF"
        assert fbc.fix_windows_env_vars(cmd) == "cd C:/Users/me/AppData/Roaming && cat > x <<'EOF'\n$APPDATA\nEOF"

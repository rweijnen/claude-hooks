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
        ('python -c "import subprocess; subprocess.run([\'python3\', \'-V\'])"',
         'python -c "import subprocess; subprocess.run([\'python\', \'-V\'])"'),
    ])
    def test_command_word_rewritten(self, cmd, expected):
        assert fbc.fix_python3(cmd) == expected


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


class TestWindowsEnvVarsWholeWord:
    def test_longer_variable_name_untouched(self):
        cmd = 'echo "$APPDATA_DIR/x"'
        assert fbc.fix_windows_env_vars(cmd) == cmd

    @pytest.mark.parametrize("cmd, expected", [
        ('cd "$APPDATA/Code"', 'cd "$HOME/AppData/Roaming/Code"'),
        ("ls ${LOCALAPPDATA}/Temp", "ls $HOME/AppData/Local/Temp"),
        ("echo $USERPROFILE", "echo $HOME"),
    ])
    def test_variable_rewritten(self, cmd, expected):
        assert fbc.fix_windows_env_vars(cmd) == expected

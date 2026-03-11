"""Tests for fix_bash_command.py hook."""

import os
import sys
from unittest.mock import patch

import pytest

# Add hooks/ to path so we can import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from fix_bash_command import fix_doubled_flags, fix_cmd_cd


# ---------------------------------------------------------------------------
# fix_doubled_flags (tier 1 auto-fix)
# ---------------------------------------------------------------------------

class TestFixDoubledFlags:
    def test_cmd_c_doubled(self):
        assert fix_doubled_flags("cmd //c build.bat") == "cmd /c build.bat"

    def test_tasklist_fi(self):
        assert fix_doubled_flags('tasklist //fi "STATUS eq RUNNING"') == 'tasklist /fi "STATUS eq RUNNING"'

    def test_ipconfig_all(self):
        assert fix_doubled_flags("ipconfig //all") == "ipconfig /all"

    def test_multiple_flags(self):
        assert fix_doubled_flags("cmd //c //v build.bat") == "cmd /c /v build.bat"

    def test_url_not_touched(self):
        cmd = "curl https://example.com"
        assert fix_doubled_flags(cmd) == cmd

    def test_unc_path_not_touched(self):
        """UNC paths start with // followed by server name (longer than 4 chars typically),
        but the regex only matches 1-4 char flags. //server would not match.
        //s or similar short ones are skipped because they're followed by /."""
        cmd = "ls //server/share"
        # //serv would be 4 chars and match, but followed by / -> skipped
        assert fix_doubled_flags(cmd) == cmd

    def test_no_flags_unchanged(self):
        cmd = "echo hello"
        assert fix_doubled_flags(cmd) == cmd

    def test_single_flags_unchanged(self):
        cmd = "tasklist /fi something"
        assert fix_doubled_flags(cmd) == cmd

    def test_heredoc_body_not_touched(self):
        cmd = 'cat <<EOF\ntasklist //fi "test"\nEOF'
        assert fix_doubled_flags(cmd) == cmd

    def test_returns_string_not_none(self):
        """The old check_ function returned None; fix_ must always return a string."""
        result = fix_doubled_flags("echo hello")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# fix_cmd_cd (tier 1 auto-fix)
# ---------------------------------------------------------------------------

class TestFixCmdCd:
    def test_cd_and_cmd_c(self):
        result = fix_cmd_cd("cd C:/Work/project && cmd /c build.bat 2>&1")
        assert result == 'cmd /c "cd C:/Work/project && build.bat" 2>&1'

    def test_bare_cmd_c_uses_cwd(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Users\me\project"):
            result = fix_cmd_cd("cmd /c build.bat")
        assert result == 'cmd /c "cd C:/Users/me/project && build.bat"'

    def test_already_quoted_args(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd('cmd /c "build.bat --verbose"')
        assert result == 'cmd /c "cd C:/Work && build.bat --verbose"'

    def test_cd_with_quoted_dir(self):
        result = fix_cmd_cd('cd "C:/My Projects" && cmd /c build.bat')
        assert result == 'cmd /c "cd C:/My Projects && build.bat"'

    def test_preserves_2_redirect(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd /c build.bat 2>&1")
        assert result == 'cmd /c "cd C:/Work && build.bat" 2>&1'

    def test_preserves_pipe(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd /c build.bat | grep error")
        assert result == 'cmd /c "cd C:/Work && build.bat" | grep error'

    def test_no_cmd_c_unchanged(self):
        cmd = "python build.py"
        assert fix_cmd_cd(cmd) == cmd

    def test_cmd_exe_variant(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd.exe /c build.bat")
        assert result == 'cmd /c "cd C:/Work && build.bat"'

    def test_empty_args_unchanged(self):
        # Edge case: cmd /c with nothing after it
        cmd = "cmd /c "
        assert fix_cmd_cd(cmd) == cmd

    def test_backslashes_in_cwd_converted(self):
        """os.getcwd() on Windows returns backslashes; they should be converted."""
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Users\me\src"):
            result = fix_cmd_cd("cmd /c test.bat")
        assert "\\" not in result
        assert "C:/Users/me/src" in result


# ---------------------------------------------------------------------------
# Integration: doubled_flags + cmd_cd together
# ---------------------------------------------------------------------------

class TestDoubledFlagsThenCmdCd:
    """Verify the pipeline: fix_doubled_flags runs first, then fix_cmd_cd."""

    def test_doubled_c_flag_then_cd_inject(self):
        cmd = "cmd //c build.bat"
        # Step 1: fix doubled flags
        cmd = fix_doubled_flags(cmd)
        assert cmd == "cmd /c build.bat"
        # Step 2: inject cd
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            cmd = fix_cmd_cd(cmd)
        assert cmd == 'cmd /c "cd C:/Work && build.bat"'

    def test_cd_with_doubled_flag(self):
        cmd = "cd C:/Work && cmd //c build.bat 2>&1"
        cmd = fix_doubled_flags(cmd)
        assert cmd == "cd C:/Work && cmd /c build.bat 2>&1"
        cmd = fix_cmd_cd(cmd)
        assert cmd == 'cmd /c "cd C:/Work && build.bat" 2>&1'

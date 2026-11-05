"""Tests for fix_bash_command.py hook."""

import os
import sys
from unittest.mock import patch

import pytest

# Add hooks/ to path so we can import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from fix_bash_command import (
    fix_doubled_flags,
    fix_cmd_cd,
    fix_bare_pwsh_cmdlet,
    fix_pwsh_noprofile,
    fix_windows_env_vars,
)


# ---------------------------------------------------------------------------
# fix_doubled_flags (tier 1 auto-fix)
# ---------------------------------------------------------------------------

@pytest.fixture
def conversion_off(monkeypatch):
    """MSYS2 argument conversion disabled: /flag reaches Windows programs as is."""
    monkeypatch.setenv("MSYS2_ARG_CONV_EXCL", "*")


@pytest.fixture
def conversion_default(monkeypatch):
    """Git Bash's default: /flag is converted to a path, //flag is the escape."""
    monkeypatch.delenv("MSYS2_ARG_CONV_EXCL", raising=False)


@pytest.mark.usefixtures("conversion_off")
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


@pytest.mark.usefixtures("conversion_default")
class TestDoubledFlagsDefaultConversion:
    """#53: with Git Bash's default conversion, //flag is correct and /flag breaks."""

    @pytest.mark.parametrize("cmd", [
        "cmd //c build.bat",
        'tasklist //fi "STATUS eq RUNNING"',
        "ipconfig //all",
        "cmd //c //v build.bat",
    ])
    def test_doubled_flags_left_alone(self, cmd):
        assert fix_doubled_flags(cmd) == cmd

    @pytest.mark.parametrize("value", ["", "/c;/fi"])
    def test_partial_exclusion_is_not_conversion_off(self, monkeypatch, value):
        monkeypatch.setenv("MSYS2_ARG_CONV_EXCL", value)
        assert fix_doubled_flags("cmd //c x.bat") == "cmd //c x.bat"


# ---------------------------------------------------------------------------
# fix_cmd_cd (tier 1 auto-fix)
# ---------------------------------------------------------------------------

class TestFixCmdCd:
    def test_cd_and_cmd_c(self):
        result = fix_cmd_cd("cd C:/Work/project && cmd /c build.bat 2>&1")
        assert result == r'cmd /c "cd /d C:/Work/project && .\build.bat" 2>&1'

    def test_bare_cmd_c_uses_cwd(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Users\me\project"):
            result = fix_cmd_cd("cmd /c build.bat")
        assert result == r'cmd /c "cd /d C:/Users/me/project && .\build.bat"'

    def test_already_quoted_args(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd('cmd /c "build.bat --verbose"')
        assert result == r'cmd /c "cd /d C:/Work && .\build.bat --verbose"'

    def test_cd_with_quoted_dir(self):
        result = fix_cmd_cd('cd "C:/My Projects" && cmd /c build.bat')
        assert result == r'cmd /c "cd /d C:/My Projects && .\build.bat"'

    def test_preserves_2_redirect(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd /c build.bat 2>&1")
        assert result == r'cmd /c "cd /d C:/Work && .\build.bat" 2>&1'

    def test_preserves_pipe(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd /c build.bat | grep error")
        assert result == r'cmd /c "cd /d C:/Work && .\build.bat" | grep error'

    def test_no_cmd_c_unchanged(self):
        cmd = "python build.py"
        assert fix_cmd_cd(cmd) == cmd

    def test_cmd_exe_variant(self):
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd.exe /c build.bat")
        assert result == r'cmd /c "cd /d C:/Work && .\build.bat"'

    def test_empty_args_unchanged(self):
        # Edge case: cmd /c with nothing after it
        cmd = "cmd /c "
        assert fix_cmd_cd(cmd) == cmd

    def test_backslashes_in_cwd_converted(self):
        """os.getcwd() on Windows returns backslashes; they should be converted."""
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Users\me\src"):
            result = fix_cmd_cd("cmd /c test.bat")
        assert "C:/Users/me/src" in result
        assert r".\test.bat" in result

    def test_exe_not_prefixed(self):
        r"""Non-.bat/.cmd args should not get .\ prefix."""
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd /c msbuild /p:Config=Release")
        assert result == 'cmd /c "cd /d C:/Work && msbuild /p:Config=Release"'

    def test_path_bat_not_prefixed(self):
        r"""A .bat with a path already should not get .\ prefix."""
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd("cmd /c C:/scripts/build.bat")
        assert result == r'cmd /c "cd /d C:/Work && C:/scripts/build.bat"'


# ---------------------------------------------------------------------------
# Integration: doubled_flags + cmd_cd together
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("conversion_off")
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
        assert cmd == r'cmd /c "cd /d C:/Work && .\build.bat"'

    def test_cd_with_doubled_flag(self):
        cmd = "cd C:/Work && cmd //c build.bat 2>&1"
        cmd = fix_doubled_flags(cmd)
        assert cmd == "cd C:/Work && cmd /c build.bat 2>&1"
        cmd = fix_cmd_cd(cmd)
        assert cmd == r'cmd /c "cd /d C:/Work && .\build.bat" 2>&1'


# ---------------------------------------------------------------------------
# fix_cmd_cd bugfixes (quoted args + cd /d)
# ---------------------------------------------------------------------------

class TestFixCmdCdBugfixes:
    def test_quoted_args_with_inner_cd_not_garbled(self):
        """Bug A: && inside quoted args must not be split by suffix regex."""
        result = fix_cmd_cd('cmd /c "cd C:/Work/project && build.bat"')
        assert result == r'cmd /c "cd /d C:/Work/project && .\build.bat"'

    def test_quoted_args_with_inner_cd_and_redirect(self):
        """Bug A variant: quoted args with inner cd AND trailing redirect."""
        result = fix_cmd_cd('cmd /c "cd C:/Work && build.bat" 2>&1')
        # The redirect is outside the quotes, so it stays as suffix
        # The inner cd gets /d added
        assert result == r'cmd /c "cd /d C:/Work && .\build.bat" 2>&1'

    def test_cd_d_used_for_cross_drive(self):
        """Bug B: cd /d is used so cross-drive switches work."""
        with patch("fix_bash_command.os.getcwd", return_value=r"D:\Build"):
            result = fix_cmd_cd("cmd /c build.bat")
        assert result == r'cmd /c "cd /d D:/Build && .\build.bat"'

    def test_inner_cd_already_has_d_flag(self):
        """Idempotent: inner cd /d is preserved, not doubled."""
        result = fix_cmd_cd('cmd /c "cd /d C:/Work && build.bat"')
        assert result == r'cmd /c "cd /d C:/Work && .\build.bat"'

    def test_quoted_args_without_inner_cd(self):
        """Quoted args without inner cd still get CWD injected."""
        with patch("fix_bash_command.os.getcwd", return_value=r"C:\Work"):
            result = fix_cmd_cd('cmd /c "build.bat --verbose"')
        assert result == r'cmd /c "cd /d C:/Work && .\build.bat --verbose"'


# ---------------------------------------------------------------------------
# fix_bare_pwsh_cmdlet (tier 1 auto-fix)
# ---------------------------------------------------------------------------

class TestFixBarePwshCmdlet:
    def test_get_childitem(self):
        result = fix_bare_pwsh_cmdlet("Get-ChildItem C:/Users")
        assert result == "pwsh -NoProfile -Command 'Get-ChildItem C:/Users'"

    def test_test_path(self):
        result = fix_bare_pwsh_cmdlet("Test-Path C:/file")
        assert result == "pwsh -NoProfile -Command 'Test-Path C:/file'"

    def test_write_host_with_single_quotes(self):
        """Single quotes in the command are escaped for PowerShell."""
        result = fix_bare_pwsh_cmdlet("Write-Host 'hello'")
        assert result == "pwsh -NoProfile -Command 'Write-Host ''hello'''"

    def test_already_wrapped_unchanged(self):
        cmd = "pwsh -Command 'Get-ChildItem'"
        assert fix_bare_pwsh_cmdlet(cmd) == cmd

    def test_not_cmdlet_unchanged(self):
        cmd = "ls -la"
        assert fix_bare_pwsh_cmdlet(cmd) == cmd

    def test_unapproved_verb_unchanged(self):
        """VB-Script matches Verb-Noun pattern but VB is not an approved verb."""
        cmd = "VB-Script foo"
        assert fix_bare_pwsh_cmdlet(cmd) == cmd

    def test_lowercase_verb_unchanged(self):
        """Bare cmdlets must start with uppercase verb."""
        cmd = "get-childitem C:/Users"
        assert fix_bare_pwsh_cmdlet(cmd) == cmd

    def test_invoke_expression(self):
        result = fix_bare_pwsh_cmdlet("Invoke-Expression 'dir'")
        assert result == "pwsh -NoProfile -Command 'Invoke-Expression ''dir'''"

    def test_select_string(self):
        result = fix_bare_pwsh_cmdlet("Select-String -Pattern test C:/file.txt")
        assert result == "pwsh -NoProfile -Command 'Select-String -Pattern test C:/file.txt'"


# ---------------------------------------------------------------------------
# fix_pwsh_noprofile (tier 1 auto-fix)
# ---------------------------------------------------------------------------

class TestFixPwshNoprofile:
    def test_adds_noprofile(self):
        result = fix_pwsh_noprofile("pwsh -Command 'foo'")
        assert result == "pwsh -NoProfile -Command 'foo'"

    def test_already_has_noprofile(self):
        cmd = "pwsh -NoProfile -Command 'foo'"
        assert fix_pwsh_noprofile(cmd) == cmd

    def test_already_has_nop_abbreviation(self):
        cmd = "pwsh -nop -Command 'foo'"
        assert fix_pwsh_noprofile(cmd) == cmd

    def test_file_flag_unchanged(self):
        """pwsh -File does not need -NoProfile injection."""
        cmd = "pwsh -File script.ps1"
        assert fix_pwsh_noprofile(cmd) == cmd

    def test_short_c_flag(self):
        result = fix_pwsh_noprofile("pwsh -c 'Get-Process'")
        assert result == "pwsh -NoProfile -c 'Get-Process'"

    def test_pwsh_exe_variant(self):
        result = fix_pwsh_noprofile("pwsh.exe -Command 'foo'")
        assert result == "pwsh.exe -NoProfile -Command 'foo'"

    def test_not_pwsh_unchanged(self):
        cmd = "python -c 'print(1)'"
        assert fix_pwsh_noprofile(cmd) == cmd


# ---------------------------------------------------------------------------
# fix_windows_env_vars (tier 1 auto-fix)
# ---------------------------------------------------------------------------

@pytest.fixture
def windows_env(monkeypatch):
    """The Windows variables as Git Bash passes them on: backslash paths."""
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\me")
    monkeypatch.setenv("APPDATA", "C:\\Users\\me\\AppData\\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\me\\AppData\\Local")


# #64: the rewrite target is the forward-slash Windows path. $HOME/... is
# /c/Users/me/..., which native programs cannot open with MSYS2_ARG_CONV_EXCL=*.
@pytest.mark.usefixtures("windows_env")
class TestFixWindowsEnvVars:
    def test_userprofile(self):
        assert fix_windows_env_vars("echo $USERPROFILE") == "echo C:/Users/me"

    def test_appdata(self):
        assert fix_windows_env_vars("ls $APPDATA") == "ls C:/Users/me/AppData/Roaming"

    def test_localappdata(self):
        assert fix_windows_env_vars("ls $LOCALAPPDATA") == "ls C:/Users/me/AppData/Local"

    def test_braced_userprofile(self):
        assert fix_windows_env_vars("echo ${USERPROFILE}") == "echo C:/Users/me"

    def test_braced_appdata(self):
        assert fix_windows_env_vars("echo ${APPDATA}") == "echo C:/Users/me/AppData/Roaming"

    def test_braced_localappdata(self):
        assert fix_windows_env_vars("echo ${LOCALAPPDATA}") == "echo C:/Users/me/AppData/Local"

    def test_escaped_dollar_untouched(self):
        """Seen in practice: \\$APPDATA in a commit message, which bash never expands."""
        cmd = 'git commit -m "bash never expands, so \\$APPDATA there"'
        assert fix_windows_env_vars(cmd) == cmd

    @pytest.mark.parametrize("cmd", [
        "echo '$APPDATA is literal here'",
        "ssh pi 'echo $USERPROFILE'",
        "ssh pi echo $USERPROFILE",
        'cmd /c "dir $USERPROFILE"',
        'pwsh -NoProfile -Command "Get-ChildItem $APPDATA"',
        "ls  # $APPDATA",
    ])
    def test_not_expanded_or_native_backslash_untouched(self, cmd):
        assert fix_windows_env_vars(cmd) == cmd

    def test_inside_bash_c_script_rewritten(self):
        assert fix_windows_env_vars("bash -c 'ls $APPDATA'") == "bash -c 'ls C:/Users/me/AppData/Roaming'"

    def test_variable_missing_from_environment_left_alone(self, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA")
        assert fix_windows_env_vars("ls $LOCALAPPDATA") == "ls $LOCALAPPDATA"

    def test_pwsh_skipped(self):
        cmd = "pwsh -Command 'echo $USERPROFILE'"
        assert fix_windows_env_vars(cmd) == cmd

    def test_powershell_exe_skipped(self):
        cmd = "powershell.exe -Command 'echo $APPDATA'"
        assert fix_windows_env_vars(cmd) == cmd

    def test_no_env_var_unchanged(self):
        cmd = "echo hello"
        assert fix_windows_env_vars(cmd) == cmd

    def test_multiple_vars(self):
        cmd = "cp $USERPROFILE/file $LOCALAPPDATA/dest"
        assert fix_windows_env_vars(cmd) == "cp C:/Users/me/file C:/Users/me/AppData/Local/dest"

    def test_in_python_command(self):
        cmd = 'python -c "import os; print(os.path.join(\'$USERPROFILE\', \'docs\'))"'
        expected = 'python -c "import os; print(os.path.join(\'C:/Users/me\', \'docs\'))"'
        assert fix_windows_env_vars(cmd) == expected

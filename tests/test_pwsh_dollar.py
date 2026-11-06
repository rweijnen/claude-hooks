"""Tests for the pwsh -Command $ check (#58).

Bash expands $ inside double quotes before pwsh runs, and only the caller
knows whether a $ is meant for bash or for pwsh. The check never rewrites:
it passes $ the caller has marked (\\$name for pwsh, a variable assigned in
the same command for bash) and blocks everything else with the reason.
The two commands from the transcript history are included.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import fix_bash_command as fbc


class Blocked(Exception):
    pass


@pytest.fixture
def check(monkeypatch):
    """check(cmd) -> None if allowed, else the block message."""
    def fake_block(message, check_id, cmd, proposed=None):
        assert check_id == "pwsh_quoting"
        raise Blocked(message)
    monkeypatch.setattr(fbc, "block", fake_block)

    def run(cmd):
        try:
            fbc.check_pwsh_quoting(cmd)
        except Blocked as e:
            return str(e)
        return None
    return run


class TestPwshDollarBlocked:
    @pytest.mark.parametrize("cmd, name", [
        ('pwsh -NoProfile -Command "Get-ChildItem | % { $_.Name }"', "$_"),
        ('pwsh -Command "Write-Host $env:TEMP"', "$env:TEMP"),
        ('pwsh.exe -nop -c "Write-Host $x"', "$x"),
        ('pwsh -NoProfile -Command "Remove-Item $TARGET"', "$TARGET"),
        ('pwsh -NoProfile -Command "Write-Host $(Get-Date)"', "$("),
        ('pwsh -NoProfile -Command "Write-Host $?"', "$?"),
        ('pwsh -NoProfile -Command "Write-Host ${x}"', "${x}"),
        # unquoted -Command words and unquoted heredocs are expanded too
        ('pwsh -NoProfile -Command Write-Host $x', "$x"),
        ('pwsh -NoProfile -Command - <<EOF\nWrite-Host $x\nEOF', "$x"),
        # an assignment after the pwsh call does not count
        ('pwsh -NoProfile -Command "Write-Host $W"; W=1', "$W"),
        # inside a bash command substitution
        ('echo "$(pwsh -NoProfile -Command "Write-Host $x")"', "$x"),
    ])
    def test_unmarked_dollar_blocked(self, check, cmd, name):
        msg = check(cmd)
        assert msg is not None
        assert name in msg

    def test_history_backtick_escape_blocked(self, check):
        # pwsh's backtick escape is command substitution in bash
        cmd = ("pwsh -NoProfile -Command \"try { 'ok ' + `$PSVersionTable.PSVersion } "
               "catch { 'failed: ' + `$_ }\"")
        msg = check(cmd)
        assert msg is not None
        assert "`" in msg

    def test_mixed_lists_only_the_unmarked_names(self, check):
        msg = check('W=1; pwsh -NoProfile -Command "Write-Host $W; ls | % { $_.Name }"')
        assert msg is not None
        assert "$_" in msg
        assert "$W" not in msg

    def test_message_explains_how_to_mark(self, check):
        msg = check('pwsh -NoProfile -Command "Write-Host $x"')
        assert "\\$x" in msg                 # pwsh variable
        assert "single-quote" in msg
        assert "\\$env:NAME" in msg          # environment value
        assert "assign" in msg               # bash variable / $(...) output


class TestPwshDollarAllowed:
    @pytest.mark.parametrize("cmd", [
        'pwsh -NoProfile -Command "Get-Date"',
        'pwsh -NoProfile -Command "Write-Host 5$"',
        # marked for pwsh
        'pwsh -NoProfile -Command "Write-Host \\$_ \\$env:TEMP \\$PSVersionTable"',
        "pwsh -NoProfile -Command 'Write-Host $x $_'",
        "pwsh -NoProfile -Command $'Write-Host $x'",
        "pwsh -NoProfile -Command - <<'EOF'\nWrite-Host $x\nEOF",
        # marked for bash: assigned in the same command
        'W=hi; pwsh -NoProfile -Command "Write-Host $W"',
        'W=hi; pwsh -NoProfile -Command "Write-Host ${W}"',
        'export W=hi && pwsh -c "$W"',
        'for f in a b; do pwsh -NoProfile -Command "Write-Host $f"; done',
        'P=$(cygpath -w x); pwsh -NoProfile -Command "Get-Item \'$P\'"',
        # mixed, both marked
        'W=1; pwsh -NoProfile -Command "Write-Host $W; ls | % { \\$_.Name }"',
        # not pwsh code
        'echo "pwsh -Command \\"Write-Host $x\\""',
        'git commit -m "check pwsh -Command \\"$x\\""',
        'pwsh -File script.ps1 "$x"',
    ])
    def test_allowed(self, check, cmd):
        assert check(cmd) is None

    def test_history_bash_variable_allowed(self, check):
        cmd = ('D="C:/tmp/wslnul"; W=$(cygpath -w "$D"); '
               'pwsh -NoProfile -Command "Remove-Item -LiteralPath \'$W\\\\nul\' '
               '-ErrorAction Stop; \'pwsh removed it\'" 2>&1 | head -3')
        assert check(cmd) is None


class TestPwshQuotingNotAFix:
    def test_no_longer_in_fix_chain(self):
        assert "pwsh_quoting" not in [fix_id for fix_id, _, _ in fbc._FIXES]
        assert "pwsh_quoting" in [check_id for check_id, _, _ in fbc._CHECKS]

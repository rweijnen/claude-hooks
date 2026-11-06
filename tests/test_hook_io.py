"""End-to-end tests: run the hook scripts as Claude Code does (JSON on stdin).

Each test runs a fresh copy of the hooks in a temp directory so config.json
and fixups.log are isolated from any real install. PYTHONUTF8 and
PYTHONIOENCODING are removed from the environment so stdin/stderr use the
platform default encoding, exactly as when Claude Code spawns the hook.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_SRC = Path(__file__).resolve().parent.parent / "hooks"


@pytest.fixture
def hooks_dir(tmp_path):
    for name in ("fix_bash_command.py", "check_file_content.py"):
        shutil.copy2(HOOKS_SRC / name, tmp_path / name)
    return tmp_path


def run_hook(hooks_dir, script, payload, config=None, conv_excl="*"):
    """Run a hook as Claude Code does. conv_excl is MSYS2_ARG_CONV_EXCL in the
    hook's environment (None = unset, Git Bash's default conversion)."""
    if config is not None:
        (hooks_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING", "MSYS2_ARG_CONV_EXCL")}
    if conv_excl is not None:
        env["MSYS2_ARG_CONV_EXCL"] = conv_excl
    return subprocess.run(
        [sys.executable, str(hooks_dir / script)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=hooks_dir,
    )


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command, "description": "d"}}


def rewritten(proc):
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["command"]


def log_entries(hooks_dir):
    log = hooks_dir / "fixups.log"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# #35 -- stdin/stderr encoding
# ---------------------------------------------------------------------------

class TestUtf8Input:
    def test_rewrite_preserves_non_ascii(self, hooks_dir):
        cmd = "cd /c/Work && cat > notes.md <<'EOF'\nStep 1 \u2014 check \u2192 done \u00d7 2\nEOF"
        out = rewritten(run_hook(hooks_dir, "fix_bash_command.py", bash(cmd)))
        assert out == cmd.replace("/c/Work", "C:/Work")

    def test_emoji_in_file_content_is_blocked(self, hooks_dir):
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": "x.md", "content": "launch \U0001F680 now"}}
        proc = run_hook(hooks_dir, "check_file_content.py", payload,
                        config={"file_content_unicode": True})
        assert proc.returncode == 2
        assert b"U+1F680" in proc.stderr

    def test_plain_unicode_letters_allowed(self, hooks_dir):
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": "x.md", "content": "caf\u00e9 na\u00efve"}}
        proc = run_hook(hooks_dir, "check_file_content.py", payload,
                        config={"file_content_unicode": True})
        assert proc.returncode == 0

    def test_block_message_with_non_cp1252_char(self, hooks_dir):
        cmd = 'echo "a \u2192 b"; ls \\\\server\\share\\dir'
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash(cmd))
        assert proc.returncode == 2
        assert "\u2192" in proc.stderr.decode("utf-8")

    def test_invalid_input_fails_loudly(self, hooks_dir):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        proc = subprocess.run(
            [sys.executable, str(hooks_dir / "fix_bash_command.py")],
            input=b"\xff\xfe not json", capture_output=True, env=env,
        )
        assert proc.returncode == 1
        assert b"fix_bash_command" in proc.stderr


# ---------------------------------------------------------------------------
# #49 -- updatedInput replaces the whole tool input
# ---------------------------------------------------------------------------

def updated_input(proc):
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]


class TestConversionSetting:
    """#53: the hook reads MSYS2_ARG_CONV_EXCL from the environment Claude Code gives it."""

    def test_doubled_flag_fixed_with_conversion_off(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash("ipconfig //all"))
        assert rewritten(proc) == "ipconfig /all"

    def test_doubled_flag_kept_with_default_conversion(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash("ipconfig //all"), conv_excl=None)
        assert proc.returncode == 0 and proc.stdout == b""


class TestUpdatedInputPassthrough:
    def test_timeout_and_background_kept(self, hooks_dir):
        payload = {"tool_name": "Bash", "tool_input": {
            "command": "ls > nul", "description": "d",
            "timeout": 600000, "run_in_background": True}}
        out = updated_input(run_hook(hooks_dir, "fix_bash_command.py", payload))
        assert out == {"command": "ls > /dev/null", "description": "d",
                       "timeout": 600000, "run_in_background": True}

    def test_no_description_invented(self, hooks_dir):
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls > nul"}}
        out = updated_input(run_hook(hooks_dir, "fix_bash_command.py", payload))
        assert out == {"command": "ls > /dev/null"}


# ---------------------------------------------------------------------------
# #39 -- logging
# ---------------------------------------------------------------------------

class TestLogging:
    def test_powershell_block_is_logged(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        bash("powershell.exe -Command Get-Date"))
        assert proc.returncode == 2
        entries = log_entries(hooks_dir)
        assert [e["fix"] for e in entries] == ["powershell_legacy"]
        assert entries[0]["type"] == "suggest"

    def test_git_commit_block_is_logged(self, hooks_dir):
        cmd = 'git commit -m "Fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash(cmd),
                        config={"git_commit_attribution": True})
        assert proc.returncode == 2
        assert [e["fix"] for e in log_entries(hooks_dir)] == ["git_commit_attribution"]

    def test_unc_block_logged_once(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash("ls \\\\server\\share\\dir"))
        assert proc.returncode == 2
        assert [e["fix"] for e in log_entries(hooks_dir)] == ["unc_paths"]

    def test_nul_fix_not_reported_when_absent(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash("tasklist //fi \"STATUS eq RUNNING\""))
        ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "doubled" in ctx
        assert "nul" not in ctx

    def test_log_trimmed_to_newest_entries(self, hooks_dir):
        """A log over the size cap is trimmed, keeping the newest entries."""
        sys.path.insert(0, str(HOOKS_SRC))
        import fix_bash_command as fbc
        assert fbc.MAX_LOG_BYTES >= 5_000_000
        old = json.dumps({"time": "t", "type": "autofix", "fix": "old", "original": "x" * 1000})
        lines = [old] * (fbc.MAX_LOG_BYTES // len(old) + 10)
        (hooks_dir / "fixups.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        run_hook(hooks_dir, "fix_bash_command.py", bash("ls > nul"))
        log = hooks_dir / "fixups.log"
        assert log.stat().st_size <= fbc.TRIM_TO_BYTES
        entries = log_entries(hooks_dir)
        assert entries[-1]["fix"] == "replaced > nul with > /dev/null"
        assert len(entries) > 1000


# ---------------------------------------------------------------------------
# #43 -- commit attribution checks: AI trailers only, neutral message
# ---------------------------------------------------------------------------

COMMIT_CHECKS = {"git_commit_attribution": True, "git_commit_generated": True}


def commit(message):
    return bash('git commit -m "' + message + '"')


class TestCommitAttribution:
    @pytest.mark.parametrize("trailer", [
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
        "Co-authored-by: Claude <noreply@anthropic.com>",
        "Co-Authored-By: Copilot <175728472+Copilot@users.noreply.github.com>",
        "Co-Authored-By: ChatGPT <noreply@openai.com>",
        "Co-Authored-By: Aider <aider@aider.chat>",
    ])
    def test_ai_trailer_blocked(self, hooks_dir, trailer):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        commit("Fix parser\n\n" + trailer), config=COMMIT_CHECKS)
        assert proc.returncode == 2
        assert [e["fix"] for e in log_entries(hooks_dir)] == ["git_commit_attribution"]

    def test_human_coauthor_allowed(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        commit("Fix parser\n\nCo-Authored-By: Jane Doe <jane@example.com>"),
                        config=COMMIT_CHECKS)
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

    def test_ai_generated_footer_blocked(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        commit("Fix parser\n\nGenerated with [Claude Code](https://claude.com/claude-code)"),
                        config=COMMIT_CHECKS)
        assert proc.returncode == 2
        assert [e["fix"] for e in log_entries(hooks_dir)] == ["git_commit_generated"]

    def test_unrelated_generated_with_allowed(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        commit("Regenerate fixtures generated with protoc"),
                        config=COMMIT_CHECKS)
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

    def test_message_is_neutral_and_names_check(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        commit("Fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>"),
                        config=COMMIT_CHECKS)
        msg = proc.stderr.decode("utf-8")
        assert "git_commit_attribution" in msg
        assert "config.json" in msg
        assert "Remove AI attribution" not in msg

    def test_checks_off_by_default(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        commit("Fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>"))
        assert proc.returncode == 0


# ---------------------------------------------------------------------------
# #58 -- pwsh -Command $ check in the full chain
# ---------------------------------------------------------------------------

class TestPwshDollarChain:
    def test_blocked_although_noprofile_is_inserted(self, hooks_dir):
        # v1: pwsh_noprofile ran first and hid the command from the quoting fix
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        bash('pwsh -Command "ls | % { $_.Name }"'))
        assert proc.returncode == 2
        assert b"$_" in proc.stderr

    def test_blocked_when_noprofile_already_present(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        bash('pwsh -NoProfile -Command "Write-Host $env:TEMP"'))
        assert proc.returncode == 2

    def test_marked_command_runs_with_quotes_unchanged(self, hooks_dir):
        cmd = 'W=hi; pwsh -NoProfile -Command "Write-Host $W \\$PID"'
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash(cmd))
        assert proc.returncode == 0
        assert proc.stdout == b""

    def test_disabled_by_config(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py",
                        bash('pwsh -NoProfile -Command "Write-Host $x"'),
                        config={"pwsh_quoting": False})
        assert proc.returncode == 0


# ---------------------------------------------------------------------------
# #57 -- # hook:verbatim escape hatch with permission prompt
# ---------------------------------------------------------------------------

MARK = "  # hook:" + "verbatim"


def hook_output(proc):
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout)["hookSpecificOutput"]


class TestVerbatim:
    def test_overridden_fix_asks_with_the_skipped_rewrite(self, hooks_dir):
        out = hook_output(run_hook(hooks_dir, "fix_bash_command.py",
                                   bash("ls /c/Users/me" + MARK)))
        assert out["permissionDecision"] == "ask"
        assert "updatedInput" not in out
        reason = out["permissionDecisionReason"]
        assert "/c/Users/me" in reason and "C:/Users/me" in reason
        assert "MSYS2 drive paths" in reason

    def test_every_skipped_fix_is_listed(self, hooks_dir):
        out = hook_output(run_hook(hooks_dir, "fix_bash_command.py",
                                   bash("ls /c/x $USERPROFILE" + MARK)))
        reason = out["permissionDecisionReason"]
        assert "MSYS2 drive paths" in reason and "Windows env vars" in reason

    def test_reason_caps_the_number_of_excerpts(self, hooks_dir):
        cmd = " ".join(f"/c/d{i}" for i in range(12))
        out = hook_output(run_hook(hooks_dir, "fix_bash_command.py", bash("ls " + cmd + MARK)))
        reason = out["permissionDecisionReason"]
        assert reason.count(" -> ") == 5
        assert "more" in reason

    def test_noop_when_nothing_would_change(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash("ls -la" + MARK))
        assert proc.returncode == 0 and proc.stdout == b""
        entries = log_entries(hooks_dir)
        assert [(e["type"], e["decision"]) for e in entries] == [("verbatim", "noop")]

    def test_ask_is_logged_with_the_fixes(self, hooks_dir):
        run_hook(hooks_dir, "fix_bash_command.py", bash("ls /c/x" + MARK))
        [entry] = log_entries(hooks_dir)
        assert entry["type"] == "verbatim" and entry["decision"] == "ask"
        assert entry["fix"] == "msys2_drive_paths"
        assert entry["proposed"] == "ls C:/x" + MARK

    @pytest.mark.parametrize("cmd", [
        # ls, not echo: since #69 echo text is not rewritten at all
        'ls "# hook:verbatim" /c/x',
        "ls '# hook:verbatim' /c/x",
        "cat > notes.md <<'EOF'\n# hook:verbatim\nEOF\nls /c/x",
    ])
    def test_marker_in_text_does_not_count(self, hooks_dir, cmd):
        out = hook_output(run_hook(hooks_dir, "fix_bash_command.py", bash(cmd)))
        assert "permissionDecision" not in out
        assert "C:/x" in out["updatedInput"]["command"]

    def test_blocking_checks_still_apply(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash("ls > con" + MARK))
        assert proc.returncode == 2

    def test_real_nul_redirect_still_blocked(self, hooks_dir):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash("ls > nul" + MARK))
        assert proc.returncode == 2
        assert "verbatim" in proc.stderr.decode("utf-8")
        assert [e["fix"] for e in log_entries(hooks_dir)] == ["nul_redirect"]

    def test_nul_in_text_is_not_blocked(self, hooks_dir):
        # Since #62 the nul fix leaves text alone, so the ask here comes from the
        # /c/ path; the point is that "> nul" in text does not trigger the block.
        cmd = """echo '{"command":"ls > nul"}' | python /c/x.py""" + MARK
        out = hook_output(run_hook(hooks_dir, "fix_bash_command.py", bash(cmd)))
        assert out["permissionDecision"] == "ask"
        assert "> nul" not in out["permissionDecisionReason"]

    @pytest.mark.parametrize("cmd", [
        "wsl -e sh -c 'make > nul'" + MARK,
        # on a heredoc command the marker goes on the first line: after EOF
        # it would make the terminator line "EOF  # ...", which ends nothing
        "ssh pi bash <<'EOF'" + MARK + "\nls > nul\nEOF",
    ])
    def test_nul_redirect_in_remote_script_still_blocked(self, hooks_dir, cmd):
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash(cmd))
        assert proc.returncode == 2

    def test_pwsh_dollar_check_not_overridden(self, hooks_dir):
        # #58: an unmarked $ in pwsh -Command "..." always has a plain way
        # through (\$name or an assignment), so verbatim does not ask
        cmd = """pwsh -Command "Write-Host $x 'y'\"""" + MARK
        proc = run_hook(hooks_dir, "fix_bash_command.py", bash(cmd))
        assert proc.returncode == 2
        assert b"$x" in proc.stderr

    def test_rewrite_hint_mentions_the_marker(self, hooks_dir):
        out = hook_output(run_hook(hooks_dir, "fix_bash_command.py", bash("ls /c/x")))
        assert "# hook:verbatim" in out["additionalContext"]
        assert "single-quote" not in out["additionalContext"]

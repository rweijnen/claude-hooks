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


def run_hook(hooks_dir, script, payload, config=None):
    if config is not None:
        (hooks_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
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
        cmd = 'git commit -m "Fix\n\nCo-Authored-By: X <x@example.com>"'
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

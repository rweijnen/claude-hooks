"""Tests for adding -X utf8 to python -I / -E (#56).

Isolated mode (-I) implies -E, which ignores every PYTHON* environment
variable, so the user's PYTHONUTF8=1 is lost and printing non-ASCII to a
pipe crashes with UnicodeEncodeError. -X options are not affected by -E.
The fix carries the user's setting through: only when PYTHONUTF8=1 is set,
only for a bare python command word (the interpreter this hook runs on),
and only before Python 3.15, where UTF-8 mode becomes the default.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import fix_bash_command as fbc


@pytest.fixture
def utf8_wanted(monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setattr(fbc, "_utf8_mode_is_default", lambda: False)


class TestAddsUtf8:
    @pytest.mark.parametrize("cmd, expected", [
        ("python -I script.py", "python -X utf8 -I script.py"),
        ('python -E -c "print(1)"', 'python -X utf8 -E -c "print(1)"'),
        ("python -IB x.py", "python -X utf8 -IB x.py"),
        ("python -B -I x.py", "python -X utf8 -B -I x.py"),
        ('python -Ic "import x"', 'python -X utf8 -Ic "import x"'),
        ("python -I -X dev x.py", "python -X utf8 -I -X dev x.py"),
        ("python -W ignore -I x.py", "python -X utf8 -W ignore -I x.py"),
        ("python.exe -I x.py", "python.exe -X utf8 -I x.py"),
        ("cd x && python -I measure.py a b", "cd x && python -X utf8 -I measure.py a b"),
        ("python -I - <<'EOF'\nprint(1)\nEOF", "python -X utf8 -I - <<'EOF'\nprint(1)\nEOF"),
        ("out=$(python -I x.py)", "out=$(python -X utf8 -I x.py)"),
        ("python -I a.py; python -I b.py", "python -X utf8 -I a.py; python -X utf8 -I b.py"),
        ("bash -c 'python -I x.py'", "bash -c 'python -X utf8 -I x.py'"),
    ])
    def test_added(self, utf8_wanted, cmd, expected):
        assert fbc.fix_python_isolated_utf8(cmd) == expected


class TestLeftAlone:
    @pytest.mark.parametrize("cmd", [
        "python x.py",
        "python -B x.py",
        'python -c "print(1)" -I',          # -I after -c is an argument of the code
        "python -m pytest -I",              # and after -m one of the module
        "python x.py -I",
        # already set, or explicitly turned off
        "python -I -X utf8 x.py",
        "python -X utf8 -I x.py",
        "python -Xutf8 -I x.py",
        "python -I -X utf8=0 x.py",
        # not a python command word
        'echo "python -I x.py"',
        'git commit -m "run python -I x.py"',
        "ls  # python -I x.py",
        "ssh pi python -I x.py",
        "which python",
        # another interpreter, whose version this hook does not know
        "C:/Python311/python.exe -I x.py",
        "py -I x.py",
    ])
    def test_unchanged(self, utf8_wanted, cmd):
        assert fbc.fix_python_isolated_utf8(cmd) == cmd

    def test_unchanged_without_pythonutf8(self, monkeypatch):
        monkeypatch.delenv("PYTHONUTF8", raising=False)
        monkeypatch.setattr(fbc, "_utf8_mode_is_default", lambda: False)
        assert fbc.fix_python_isolated_utf8("python -I x.py") == "python -I x.py"

    def test_unchanged_when_pythonutf8_is_0(self, monkeypatch):
        monkeypatch.setenv("PYTHONUTF8", "0")
        monkeypatch.setattr(fbc, "_utf8_mode_is_default", lambda: False)
        assert fbc.fix_python_isolated_utf8("python -I x.py") == "python -I x.py"

    def test_unchanged_from_python_315(self, monkeypatch):
        monkeypatch.setenv("PYTHONUTF8", "1")
        monkeypatch.setattr(fbc, "_utf8_mode_is_default", lambda: True)
        assert fbc.fix_python_isolated_utf8("python -I x.py") == "python -I x.py"


class TestUtf8ModeDefault:
    def test_matches_running_version(self):
        assert fbc._utf8_mode_is_default() == (sys.version_info >= (3, 15))

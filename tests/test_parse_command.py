"""Tests for parse_command(): typed spans for a Bash command (#51, v2 plan #48).

The parser must tell a command apart from text about a command: python3 as a
command word, inside a commit message, inside a grep pattern or inside a
heredoc written to a file are four different things.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import fix_bash_command as fbc


def span_at(cmd, needle, nth=0):
    """The span at the start of the nth occurrence of needle in cmd."""
    pos = -1
    for _ in range(nth + 1):
        pos = cmd.index(needle, pos + 1)
    return fbc.parse_command(cmd).at(pos)


def kind_at(cmd, needle, nth=0):
    return span_at(cmd, needle, nth).kind


# ---------------------------------------------------------------------------
# Command words
# ---------------------------------------------------------------------------

class TestCommandWords:
    @pytest.mark.parametrize("cmd", [
        "python3 x.py",
        "cd /tmp && python3 x.py",
        "ls; python3 x.py",
        "ls | python3 x.py",
        "FOO=1 python3 x.py",
        "env -i BAR=2 python3 x.py",
        "sudo -u root python3 x.py",
        "ls | xargs -n 1 python3 -V",
        "timeout 30 python3 x.py",
        "nohup python3 x.py &",
        "if true; then python3 x.py; fi",
        "while true; do python3 x.py; done",
        "(cd x && python3 y.py)",
        "echo $(python3 -V)",
        'echo "v=$(python3 -V)"',
        "echo `python3 -V`",
        "diff <(python3 a.py) b.txt",
    ])
    def test_python3_is_command(self, cmd):
        assert kind_at(cmd, "python3") == "cmd"

    def test_arguments_are_args(self):
        cmd = "cp a.txt C:/temp/python3-store.png"
        assert kind_at(cmd, "cp") == "cmd"
        assert kind_at(cmd, "a.txt") == "arg"
        assert kind_at(cmd, "python3") == "arg"

    def test_assignment_is_arg(self):
        assert kind_at('S="/c/x"; ls "$S"', "S=") == "arg"

    def test_program_path_basename(self):
        cmd = "C:/Windows/System32/wsl.exe -e sh -c 'ls'"
        assert kind_at(cmd, "C:/Windows") == "cmd"


# ---------------------------------------------------------------------------
# Data: text no program runs
# ---------------------------------------------------------------------------

class TestData:
    @pytest.mark.parametrize("cmd, needle", [
        ('echo "python3 is the stub"', "python3"),
        ("printf '%s\\n' python3", "python3"),
        ('git commit -m "use python3"', "python3"),
        ('git -C repo commit -am "use python3"', "python3"),
        ('git commit --message="use python3"', "python3"),
        ('git tag -a v1 -m "python3 era"', "python3"),
        ('gh pr create --title "python3 fix" --body "x"', "python3"),
        ('gh pr create --title "x" --body "uses $APPDATA_DIR"', "$APPDATA"),
        ('grep -n "usr/bin/python3" f.sh', "usr/bin"),
        ('grep -n "python3\\|wsl" README.md', "python3"),
        ("grep -rn -e python3 -e foo src/", "python3"),
        ("grep -rne python3 src/", "python3"),
        ("grep -A 3 python3 /c/x", "python3"),
        ("rg python3 src", "python3"),
        ("sed -e 's/python3/python/' f", "s/python3"),
        ("sed 's/a/b/' f", "s/a/b/"),
        ("awk -F, '{print $1}' f", "{print"),
    ])
    def test_is_data(self, cmd, needle):
        assert kind_at(cmd, needle) == "data"

    @pytest.mark.parametrize("cmd, needle", [
        ('grep -n "x" f.sh', "f.sh"),
        ("grep -A 3 python3 /c/x", "/c/x"),
        ("grep -A 3 python3 /c/x", "3"),
        ("sed 's/a/b/' file.txt", "file.txt"),
        ("awk -F, '{print $1}' data.csv", "data.csv"),
        ('git commit -m "x" -- src/a.py', "src/a.py"),
        ('git commit -m "x"', "commit"),
    ])
    def test_files_and_subcommands_stay_args(self, cmd, needle):
        assert kind_at(cmd, needle) == "arg"


# ---------------------------------------------------------------------------
# Code for another interpreter
# ---------------------------------------------------------------------------

class TestCode:
    @pytest.mark.parametrize("cmd, needle, lang", [
        ('python -c "import os; print(1)"', "import", "python"),
        ("python3 -I -c 'print(1)'", "print", "python"),
        ("bash -c 'python3 x.py'", "python3", "bash"),
        ("bash -lc 'python3 x.py'", "python3", "bash"),
        ("pwsh -NoProfile -Command 'Get-ChildItem C:/x'", "Get-ChildItem", "pwsh"),
        ("pwsh -c 'Get-Date'", "Get-Date", "pwsh"),
        ('cmd /c "build.bat > nul"', "build.bat", "cmd"),
        ("cmd //c dir", "dir", "cmd"),
    ])
    def test_is_code(self, cmd, needle, lang):
        span = span_at(cmd, needle)
        assert (span.kind, span.lang) == ("code", lang)

    def test_python_script_args_are_args(self):
        assert kind_at("python x.py -c foo", "foo") == "arg"


# ---------------------------------------------------------------------------
# Foreign: runs on another system
# ---------------------------------------------------------------------------

class TestForeign:
    @pytest.mark.parametrize("cmd, needle", [
        ("ssh pi 'python3 x.py'", "python3"),
        ("ssh pi python3 x.py", "python3"),
        ("wsl -e bash -c 'ls /mnt/c'", "/mnt/c"),
        ("C:/Windows/System32/wsl.exe -e sh -c 'command -v python3'", "python3"),
        ("adb -s ABC shell ls /sdcard", "/sdcard"),
        ("plink host python3 -V", "python3"),
        ('"C:/Program Files/OpenSSH/ssh.exe" host python3', "python3"),
    ])
    def test_is_foreign(self, cmd, needle):
        assert kind_at(cmd, needle) == "foreign"

    def test_adb_options_before_shell_are_args(self):
        assert kind_at("adb -s ABC shell ls", "ABC") == "arg"

    def test_command_after_foreign_segment_is_local(self):
        cmd = "ssh pi 'a; b'; python3 y.py"
        assert kind_at(cmd, "python3") == "cmd"

    def test_separator_inside_foreign_quotes_does_not_end_it(self):
        cmd = 'ssh pi "cd /tmp; python3 x.py"'
        assert kind_at(cmd, "python3") == "foreign"


# ---------------------------------------------------------------------------
# Heredocs
# ---------------------------------------------------------------------------

class TestHeredocs:
    def test_quoted_body_written_to_file_is_data(self):
        cmd = "cat > notes.md <<'EOF'\npython3 and $APPDATA\nEOF\n"
        span = span_at(cmd, "python3")
        assert (span.kind, span.quote, span.heredoc, span.expands) == ("data", "<<'", True, False)

    def test_unquoted_body_expands(self):
        cmd = "cat > x.sh <<EOF\necho $APPDATA\nEOF"
        span = span_at(cmd, "$APPDATA")
        assert (span.kind, span.quote, span.expands) == ("data", "<<", True)

    @pytest.mark.parametrize("cmd, lang", [
        ("python - <<'PY'\nprint(1)\nPY", "python"),
        ("python3 -I - <<'PY'\nprint(1)\nPY", "python"),
        ("python <<'PY'\nprint(1)\nPY", "python"),
        ("bash <<EOF\nprint(1)\nEOF", "bash"),
        ("bash -s <<'EOF'\nprint(1)\nEOF", "bash"),
        ("pwsh -NoProfile -Command - <<'EOF'\nprint(1)\nEOF", "pwsh"),
    ])
    def test_body_fed_to_interpreter_is_code(self, cmd, lang):
        span = span_at(cmd, "print(1)")
        assert (span.kind, span.lang, span.heredoc) == ("code", lang, True)

    def test_body_fed_to_script_is_data(self):
        cmd = "python tool.py <<'EOF'\npython3 x\nEOF"
        assert kind_at(cmd, "python3") == "data"

    def test_body_fed_to_foreign_is_foreign(self):
        cmd = "ssh pi bash <<'EOF'\npython3 x.py\nEOF"
        assert kind_at(cmd, "python3") == "foreign"

    def test_commit_message_via_stdin_is_data(self):
        cmd = "git commit -F - <<'EOF'\nuse python3\nEOF"
        assert kind_at(cmd, "python3") == "data"

    def test_heredoc_in_pipeline_belongs_to_its_command(self):
        cmd = "cat <<EOF | python -\nprint(1)\nEOF"
        assert kind_at(cmd, "print(1)") == "data"

    def test_command_after_heredoc_is_command(self):
        cmd = "cat > x <<'EOF'\nbody\nEOF\npython3 y.py"
        assert kind_at(cmd, "python3") == "cmd"

    def test_dash_heredoc_strips_tabs_from_terminator(self):
        cmd = "cat <<-EOF\n\tbody\n\tEOF\npython3 y.py"
        assert kind_at(cmd, "body") == "data"
        assert kind_at(cmd, "python3") == "cmd"

    def test_backslash_delimiter_is_quoted(self):
        cmd = "cat <<\\EOF\n$APPDATA\nEOF"
        assert span_at(cmd, "$APPDATA").quote == "<<'"

    def test_two_heredocs_on_one_line(self):
        cmd = "cat <<A <<'B'\none\nA\ntwo\nB\npython3 x"
        assert span_at(cmd, "one").quote == "<<"
        assert span_at(cmd, "two").quote == "<<'"
        assert kind_at(cmd, "python3") == "cmd"

    def test_unterminated_body_runs_to_end(self):
        cmd = "cat > x <<'EOF'\npython3 x\nmore"
        assert kind_at(cmd, "more") == "data"

    def test_heredoc_operator_inside_quotes_is_not_a_heredoc(self):
        cmd = 'echo "<<EOF"\npython3 x'
        assert kind_at(cmd, "python3") == "cmd"

    def test_here_string_is_arg(self):
        assert kind_at("cat <<< 'python3'", "python3") == "arg"

    def test_heredoc_inside_command_substitution(self):
        cmd = 'x=$(cat <<EOF\nbody\nEOF\n); python3 y'
        assert kind_at(cmd, "body") == "data"
        assert kind_at(cmd, "python3") == "cmd"


# ---------------------------------------------------------------------------
# Quoting
# ---------------------------------------------------------------------------

class TestQuoting:
    @pytest.mark.parametrize("cmd, needle, quote, expands", [
        ("ls plain", "plain", "", True),
        ("ls 'single $APPDATA'", "$APPDATA", "'", False),
        ('ls "double $APPDATA"', "$APPDATA", '"', True),
        ("ls $'ansi \\' x'", "x", "$'", False),
    ])
    def test_quote_recorded(self, cmd, needle, quote, expands):
        span = span_at(cmd, needle)
        assert (span.quote, span.expands) == (quote, expands)

    def test_command_substitution_inside_double_quotes(self):
        cmd = 'echo "a $(python3 -V) b"'
        assert kind_at(cmd, "a ") == "data"
        assert kind_at(cmd, "python3") == "cmd"
        assert kind_at(cmd, " b") == "data"


# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------

def redirect_targets(cmd):
    return [cmd[t0:t1] for _, _, t0, t1 in fbc.parse_command(cmd).redirects]


class TestRedirects:
    @pytest.mark.parametrize("cmd, targets", [
        ("ls > nul", ["nul"]),
        ("ls 2>nul", ["nul"]),
        ("ls>out.txt", ["out.txt"]),
        ("ls &> /dev/null", ["/dev/null"]),
        ("ls 2>&1 | tee out", ["1"]),
        ("ls >> log 2> err", ["log", "err"]),
        ('echo "> nul"', []),
        ("cat > x <<'EOF'\n> nul\nEOF", ["x"]),
        ("echo 2 > f", ["f"]),
    ])
    def test_targets(self, cmd, targets):
        assert redirect_targets(cmd) == targets

    def test_target_is_arg_and_operator_is_op(self):
        cmd = "ls > nul"
        assert kind_at(cmd, "nul") == "arg"
        assert kind_at(cmd, ">") == "op"

    def test_fd_digit_is_part_of_operator(self):
        assert kind_at("ls 2>nul", "2") == "op"

    def test_redirect_target_is_not_the_command_word(self):
        assert kind_at("> out python3 x.py", "python3") == "cmd"


# ---------------------------------------------------------------------------
# Comments and the verbatim marker
# ---------------------------------------------------------------------------

class TestComments:
    @pytest.mark.parametrize("cmd, verbatim", [
        ("ls  # hook:verbatim", True),
        ("ls #hook:verbatim", True),
        ("cat > x <<'EOF'  # hook:verbatim\nbody\nEOF", True),
        ("ls # note", False),
        ("ls # hook:verbatim please", False),
        ('echo "# hook:verbatim"', False),
        ("echo '# hook:verbatim'", False),
        ("cat > README.md <<'EOF'\n# hook:verbatim\nEOF", False),
        ("echo $(ls # hook:verbatim\n)", False),
        ('python -c "x  # hook:verbatim"', False),
        ("ls", False),
    ])
    def test_verbatim(self, cmd, verbatim):
        assert fbc.parse_command(cmd).verbatim is verbatim

    def test_comment_span(self):
        assert kind_at("ls # note", "# note") == "comment"

    def test_hash_inside_word_is_not_a_comment(self):
        assert kind_at("echo a#b", "#b") == "data"

    def test_heredoc_body_after_marker_line_still_data(self):
        cmd = "cat > x <<'EOF'  # hook:verbatim\npython3\nEOF"
        assert kind_at(cmd, "python3") == "data"


# ---------------------------------------------------------------------------
# Invariants: contiguous spans covering the whole string, never raises
# ---------------------------------------------------------------------------

INVARIANT_CASES = [
    "", " ", "ls", "python3 x.py", 'echo "unterminated', "echo 'x", "$(", "`",
    "a)", "<<", "cat <<", "cat <<'EOF'", "\\", "${", "$((1+", "echo ${x:-$(ls)}",
    "case $x in a) python3 x;; esac", "f() { python3 x; }", "a=(1 2 3); echo ${a[@]}",
    "for f in *.py; do python3 $f; done", "[[ -f x ]] && python3 x",
    "echo $((1 + $(echo 2)))", "ls \\\n  -la", "echo \"a\\\"b\" 'c'", "x=`echo \"$(ls)\"`",
    "cat <<EOF\n$(python3 -V)\nEOF", "ls &>> log", "ls >| f", "exec 3<> f", "ls ;; ls",
    "cmd /c \"a & b\" && echo $'x\\ty'",
]


@pytest.mark.parametrize("cmd", INVARIANT_CASES)
def test_spans_cover_the_command(cmd):
    p = fbc.parse_command(cmd)
    pos = 0
    for span in p.spans:
        assert span.start == pos and span.end > span.start
        pos = span.end
    assert pos == len(cmd)
    for i in range(len(cmd)):
        assert p.at(i) is not None
    assert p.at(len(cmd)) is None

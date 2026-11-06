# claude-hooks

PreToolUse hooks for Claude Code on Windows. These hooks intercept Bash commands and file writes before execution, automatically fixing common Git Bash/MSYS2 mistakes and enforcing code style preferences. No more `> nul` creating undeletable files, no more `python3` hitting the Windows Store alias, no more `dir /b` silently treating a flag as a path, no more emoji in code.

## Fixes

| Fix | Trigger | Tier | Action |
|-----|---------|------|--------|
| Null redirect | `> nul`, `2> nul` | Auto-fix | Rewrite to `> /dev/null` (not inside heredoc bodies, e.g. a `.bat` file being written) |
| Python3 alias | `python3 ...` | Auto-fix | Rewrite to `python` only when `python3` is not a real interpreter on this machine (missing, or the Microsoft Store stub); not inside wsl/ssh/adb shell arguments or heredoc bodies |
| Isolated Python | `python -I x.py` with `PYTHONUTF8=1` | Auto-fix | Add `-X utf8`, because `-I` ignores `PYTHONUTF8` (see [below](#recommended-setting-pythonutf8)); only a bare `python` command, before Python 3.15 |
| PowerShell `$` | `pwsh -Command "... $x ..."` | Block | Bash expands `$` before pwsh runs, and only the caller knows which shell each `$` is for. Passes when each one is marked: `\$x` for a pwsh variable (or single-quote the whole script), a variable assigned earlier in the same command for bash, `\$env:NAME` for an environment value. Never rewritten |
| MSYS2 drive paths | `/c/Work/...` | Auto-fix | Rewrite to `C:/Work/...`; not inside wsl/ssh/adb shell arguments |
| Reserved names | `> con`, `> prn`, `touch aux.txt` | Block | Reject -- undeletable files |
| Commit messages | AI Co-Authored-By trailers, "Generated with" AI footers, emoji | Block (opt-in) | Reject with a message naming the check |
| Doubled flags | `tasklist //fi` | Auto-fix | Rewrite to single `/`, only with `MSYS2_ARG_CONV_EXCL=*` (see [below](#recommended-setting-msys2_arg_conv_excl)) |
| Backslash paths | `C:\Users\...` | Auto-fix | Rewrite to `C:/Users/...` |
| UNC paths | `\\server\share\...` | Block | Suggest `//server/share/...` |
| WSL invocation | `wsl ls`, `wsl.exe cat` | Block | Only when WSL is not installed; allowed when it is |
| WSL mount paths | `/mnt/c/Users/...` | Auto-fix | Rewrite to `C:/Users/...` in Git Bash; left alone when the command invokes wsl, inside ssh/adb shell arguments and heredoc bodies |
| Legacy PowerShell | `powershell.exe ...` | Block | Suggest `pwsh`; use full path to opt in to PS 5.1 |
| `dir /b` in bash | `dir /b path` | Auto-fix | Rewrite to `ls -1 path` |
| Bare PS cmdlet | `Get-ChildItem C:/Users` | Auto-fix | Wrap with `pwsh -NoProfile -Command '...'` |
| pwsh -NoProfile | `pwsh -Command '...'` | Auto-fix | Insert `-NoProfile` flag |
| Windows env vars | `$USERPROFILE`, `$APPDATA`, `$LOCALAPPDATA` | Auto-fix | Rewrite to the forward-slash Windows path (`C:/Users/me/AppData/Roaming`) where bash expands them; not in single quotes, escaped, or in `cmd /c` / pwsh code |
| cmd /c CWD | `cmd /c build.bat` | Auto-fix | Inject `cd /d <cwd>` so cmd.exe finds the script |
| start command | `start "" "file.png"` | Auto-fix | Rewrite to `python os.startfile()` |
| `dir /flag` in pwsh | `pwsh -Command "dir /b ..."` | Block | Suggest `Get-ChildItem` equivalent |
| Emoji in files | Write/Edit with emoji, arrows, box drawing; also a Bash heredoc written to a file (`cat > notes.md <<'EOF'`, `tee`) | Block (opt-in) | Reject with message; both routes use the same character set and config key |

## Installation

### Global (all projects)

```
git clone https://github.com/rweijnen/claude-hooks.git
cd claude-hooks
python install.py
```

Copies hook scripts to `~/.claude/hooks/` and adds configuration to `~/.claude/settings.json`.

### Project-local (single project, good for testing)

```
python install.py --project /path/to/your/project
python install.py --project .
```

This installs hooks to `<project>/.claude/hooks/` and writes configuration to
`<project>/.claude/settings.local.json`. Only affects that one project.

### Manual installation

Copy `hooks/*.py` to `~/.claude/hooks/` and add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python \"$HOME/.claude/hooks/fix_bash_command.py\""}]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [{"type": "command", "command": "python \"$HOME/.claude/hooks/check_file_content.py\""}]
      }
    ]
  }
}
```

## Reviewing the fixups log

Every auto-fix and every block is logged. The log lives next to the hook script, so each install gets its own:

- **Global install**: `~/.claude/hooks/fixups.log`
- **Project install**: `<project>/.claude/hooks/fixups.log`

```
cat ~/.claude/hooks/fixups.log
```

Each line is a JSON object with:

| Field | Description |
|-------|-------------|
| `time` | Human-readable timestamp (`2026-02-18 13:15:51`) |
| `type` | `autofix` (tier 1, silently applied), `suggest` (tier 2, blocked) or `verbatim` (see [Escape hatch](#escape-hatch-hookverbatim)) |
| `fix` | What was fixed, or the check ID that blocked |
| `cwd` | Working directory (identifies the project) |
| `original` | The original command |
| `proposed` | The fixed/suggested command |

When the log exceeds 5 MB it is trimmed to the newest 2.5 MB (several weeks of typical use), so it won't grow unbounded.

## Recommended setting: PYTHONUTF8

The hooks handle UTF-8 themselves, but the Python scripts Claude writes and runs do not. Claude Code captures their output through a pipe, so on Windows Python encodes it with the ANSI code page (cp1252), and a script that prints an arrow or a Greek letter crashes:

```
UnicodeEncodeError: 'charmap' codec can't encode character 'λ' in position 0: character maps to <undefined>
```

Turning on Python's UTF-8 mode for everything Claude Code starts fixes this. Add it to the `env` block of `~/.claude/settings.json`:

```json
{
  "env": {
    "PYTHONUTF8": "1"
  }
}
```

It takes effect in running sessions without a restart. Side effect: `open()` without an `encoding` argument then reads and writes UTF-8 instead of cp1252, which is usually what you want, but matters if you have scripts that read legacy cp1252 files. `install.py` does not set this for you because it affects every Python process, not just the hooks. Python 3.15 makes UTF-8 mode the default ([PEP 686](https://peps.python.org/pep-0686/)), so this is only needed on 3.14 and older.

`python -I` (isolated mode, which Claude uses for scripts that read downloaded data) implies `-E` and ignores every `PYTHON*` variable, `PYTHONUTF8` included, so such a script crashes anyway. `-X` options are not affected: `python -I -X utf8` works. When `PYTHONUTF8=1` is set, the `python_isolated_utf8` fix adds `-X utf8` to `python -I` / `python -E` for you (on Python before 3.15).

## Recommended setting: MSYS2_ARG_CONV_EXCL

When Git Bash starts a native Windows program, MSYS2 converts arguments that look like POSIX paths into Windows paths. That helps with `/c/Users/...`, but it also hits anything else that starts with a slash: Windows flags and paths meant for another system. `MSYS2_ARG_CONV_EXCL=*` switches that conversion off. What a native program receives:

| Argument | `MSYS2_ARG_CONV_EXCL=*` | Git Bash default |
|---|---|---|
| `/fi` (Windows flag) | `/fi` | `C:/Program Files/Git/fi` |
| `//fi` | `//fi` | `/fi` |
| `/sdcard/DCIM` (adb device path) | `/sdcard/DCIM` | `C:/Program Files/Git/sdcard/DCIM` |
| `/c/Users/me` | `/c/Users/me` | `C:/Users/me` |
| `/tmp/x` | `/tmp/x` | `C:/Users/me/AppData/Local/Temp/x` |

So `cmd /c` works with the setting and breaks without it, and `cmd //c` is the other way round. With the setting, flags and device paths are no longer silently mangled. What remains is POSIX paths reaching native programs unconverted (Python reads `/c/Users` as `C:\c\Users` and fails loudly). The hooks cover that part: `msys2_drive_paths` rewrites `/c/...` to `C:/...` in commands. Paths a script builds at runtime are out of their reach, so scripts should build `C:/...` paths (`cygpath -m` prints that form) for native programs.

The hooks are written for this setting. `doubled_flags` only rewrites `//flag` to `/flag` when it is set, because under default conversion `//flag` is the correct form. To scope it to Claude Code instead of every Git Bash, add it to the `env` block of `~/.claude/settings.json`:

```json
{
  "env": {
    "MSYS2_ARG_CONV_EXCL": "*"
  }
}
```

Claude Code does not set this itself. Only `*` switches conversion off completely; a list of prefixes leaves the rest converted, and the hooks then treat conversion as on.

## Escape hatch: `# hook:verbatim`

When a rewrite is wrong, for example because the command asks about `python3` rather than running it, Claude can run the command again with a trailing comment:

```
which -a python3  # hook:verbatim
```

The hook then makes no change itself. If it would have changed something, it asks you first: the permission prompt lists each skipped rewrite (`python3 -> python`), and the command runs unmodified only if you approve. If nothing would have changed, the marker does nothing. Blocking checks still apply, and so does the block on a real `> nul` redirect, because that creates a file Windows cannot delete.

Only a real top-level comment counts; the marker inside quotes, a heredoc or a code string is ignored, so documentation that mentions it does not trigger it. Claude learns about the marker from the hook's own message after a rewrite. Every use is logged with `"type": "verbatim"` and `"decision": "noop"` or `"ask"`.

## Updating

Pull the latest changes and re-run the installer:

```
cd claude-hooks
git pull
python install.py
```

## Configuration

Individual checks can be enabled or disabled via `config.json` in the hooks directory (`~/.claude/hooks/config.json` for global installs, `<project>/.claude/hooks/config.json` for project installs).

To get started, copy the sample config:

```
cp ~/.claude/hooks/config.sample.json ~/.claude/hooks/config.json
```

Then edit `config.json` to toggle checks. Each key is a check ID mapped to `true` (enabled) or `false` (disabled). Missing keys use built-in defaults. If there is no `config.json`, all safety checks run and style checks are skipped (same as always).

### Check reference

| Check ID | Description | Default | Tier |
|----------|-------------|---------|------|
| `nul_redirect` | Rewrite `> nul` to `> /dev/null` | on | auto-fix |
| `msys2_drive_paths` | Rewrite `/c/...` to `C:/...` | on | auto-fix |
| `python3` | Rewrite `python3` to `python` when `python3` is not a real interpreter | on | auto-fix |
| `python_isolated_utf8` | Add `-X utf8` to `python -I` / `-E` when `PYTHONUTF8=1` is set (Python before 3.15) | on | auto-fix |
| `dir_windows_flags` | Rewrite `dir /b` to `ls -1` | on | auto-fix |
| `doubled_flags` | Rewrite `//flag` to `/flag` when `MSYS2_ARG_CONV_EXCL=*` (under default conversion `//flag` is correct) | on | auto-fix |
| `backslash_paths` | Rewrite `C:\` to `C:/` | on | auto-fix |
| `bare_pwsh_cmdlet` | Wrap bare PS cmdlets with `pwsh -NoProfile -Command` | on | auto-fix |
| `pwsh_noprofile` | Insert `-NoProfile` into `pwsh -Command` | on | auto-fix |
| `windows_env_vars` | Rewrite `$USERPROFILE` / `$APPDATA` / `$LOCALAPPDATA` to their forward-slash Windows paths | on | auto-fix |
| `cmd_cd` | Inject `cd /d` into `cmd /c` for correct CWD | on | auto-fix |
| `start_command` | Rewrite `start` to `python os.startfile()` | on | auto-fix |
| `wsl_paths` | Rewrite `/mnt/c/` WSL-style paths to `C:/` in Git Bash | on | auto-fix |
| `unc_paths` | Block `\\server` UNC paths | on | block |
| `reserved_names` | Block redirects to CON, PRN, etc. | on | block |
| `dir_in_pwsh` | Block `dir /flag` inside pwsh | on | block |
| `pwsh_quoting` | Block a `$` in `pwsh -Command` code that bash expands, unless marked as pwsh (`\$x`) or bash (assigned in the same command) | on | block |
| `powershell_legacy` | Block `powershell.exe`, suggest pwsh | on | block |
| `wsl_invocation` | Block `wsl` commands when WSL is not installed | on | block |
| `git_commit_attribution` | Block Co-Authored-By trailers that name an AI assistant (human co-authors pass) | off | block |
| `git_commit_generated` | Block "Generated with/by <AI tool>" footers in commits | off | block |
| `git_commit_emoji` | Block emoji in commit messages | off | block |
| `file_content_unicode` | Block emoji/unicode in file writes: Write/Edit content and Bash heredoc bodies written to a file (not `/dev/null`, commit messages or heredocs fed to an interpreter) | off | block |

Safety checks (default on) prevent real mistakes that break commands or create undeletable files. Style checks (default off) enforce preferences -- enable the ones you want. The commit attribution checks are for repos whose own policy disallows these lines; this project itself discloses AI use (see [How this project is built](#how-this-project-is-built)).

See `config.sample.json` for descriptions of each check.

## Customization

The hooks are plain Python scripts in `~/.claude/hooks/`. Edit them to:

- Add new auto-fix patterns (add a function, call it in `main()`)
- Promote a tier-2 suggestion to auto-fix (move the check from blocking to rewriting)
- Extend `_DIR_FLAG_MAP` in `fix_bash_command.py` to recognise additional `dir` flags
- Adjust unicode blocking ranges in `check_file_content.py`
- Add exceptions for specific commands or patterns

## How it works

Claude Code hooks receive a JSON object on stdin with `tool_name` and `tool_input`. The hook can:

- **Exit 0** with no output: allow the command unchanged
- **Exit 0** with JSON on stdout: rewrite the command via `updatedInput`
- **Exit 2** with a message on stderr: block the command and show the message to Claude

## How this project is built

The hooks are written with Claude Code, and we say so rather than hide it. Commits made with Claude carry a `Co-Authored-By: Claude` trailer, and each pull request says what was AI-written and what a person reviewed. We leave out the default "Generated with Claude Code" line: it says nothing the trailer does not, and the disclosure says more.

- Claude writes the code changes and the tests, including end-to-end tests built from real commands in Claude Code transcripts and a replay of past commands through the old and the new hook.
- A person reviews the test results and decides which changes to make.

## Credits

- Bare PowerShell cmdlet wrapping and `-NoProfile` enforcement inspired by [@kmgallahan](https://github.com/kmgallahan)'s [Bash Syntax Auto-Corrector Hook](https://github.com/anthropics/claude-code/issues/4928#issuecomment-3848600448)
- Windows env var replacement (`$USERPROFILE` / `$APPDATA` / `$LOCALAPPDATA`) suggested by [@kmgallahan](https://github.com/kmgallahan)

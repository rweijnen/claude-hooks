# claude-hooks

PreToolUse hooks for Claude Code on Windows. These hooks intercept Bash commands and file writes before execution, automatically fixing common Git Bash/MSYS2 mistakes and enforcing code style preferences. No more `> nul` creating undeletable files, no more `python3` hitting the Windows Store alias, no more emoji in code.

## Fixes

| Fix | Trigger | Tier | Action |
|-----|---------|------|--------|
| Null redirect | `> nul`, `2> nul` | Auto-fix | Rewrite to `> /dev/null` |
| Python3 alias | `python3 ...` | Auto-fix | Rewrite to `python` |
| PowerShell quoting | `pwsh -Command "$..."` | Auto-fix | Swap to single quotes |
| MSYS2 drive paths | `/c/Work/...` | Auto-fix | Rewrite to `C:/Work/...` |
| Reserved names | `> con`, `> prn`, `touch aux.txt` | Block | Reject -- undeletable files |
| Commit messages | Co-Authored-By, emoji, "Generated with" | Block | Reject with message |
| Doubled flags | `tasklist //fi` | Block | Suggest single `/` |
| Backslash paths | `C:\Users\...` | Block | Suggest `C:/Users/...` |
| UNC paths | `\\server\share\...` | Block | Suggest `//server/share/...` |
| cmd /c workaround | `cmd /c "..."` | Block | Reject with message |
| Legacy PowerShell | `powershell.exe ...` | Block | Suggest `pwsh`; full path allowed for PS 5.1 |
| `dir /b` in bash | `dir /b path` | Auto-fix | Rewrite to `ls -1 path` |
| `dir /flag` in pwsh | `pwsh -Command "dir /b ..."` | Block | Suggest `Get-ChildItem` equivalent |
| Emoji in files | Write/Edit with emoji | Block | Reject with message |

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

Both auto-fixes and tier-2 suggestions are logged. The log lives next to the hook script, so each install gets its own:

- **Global install**: `~/.claude/hooks/fixups.log`
- **Project install**: `<project>/.claude/hooks/fixups.log`

```
cat ~/.claude/hooks/fixups.log
```

Each line is a JSON object with:

| Field | Description |
|-------|-------------|
| `time` | Human-readable timestamp (`2026-02-18 13:15:51`) |
| `type` | `autofix` (tier 1, silently applied) or `suggest` (tier 2, blocked) |
| `fix` | What was fixed or suggested |
| `cwd` | Working directory (identifies the project) |
| `original` | The original command |
| `proposed` | The fixed/suggested command |

The log auto-trims to 250 lines when it exceeds 500, so it won't grow unbounded.

## Updating

Pull the latest changes and re-run the installer:

```
cd claude-hooks
git pull
python install.py
```

## Customization

The hooks are plain Python scripts in `~/.claude/hooks/`. Edit them to:

- Add new auto-fix patterns (add a function, call it in `main()`)
- Promote a tier-2 suggestion to auto-fix (move the check from blocking to rewriting)
- Adjust unicode blocking ranges in `check_file_content.py`
- Add exceptions for specific commands or patterns

## How it works

Claude Code hooks receive a JSON object on stdin with `tool_name` and `tool_input`. The hook can:

- **Exit 0** with no output: allow the command unchanged
- **Exit 0** with JSON on stdout: rewrite the command via `updatedInput`
- **Exit 2** with a message on stderr: block the command and show the message to Claude

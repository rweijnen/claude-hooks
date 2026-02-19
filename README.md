# claude-hooks

PreToolUse hooks for Claude Code on Windows. These hooks intercept Bash commands and file writes before execution, automatically fixing common Git Bash/MSYS2 mistakes and enforcing code style preferences. No more `> nul` creating undeletable files, no more `python3` hitting the Windows Store alias, no more emoji in code.

## Fixes

| Fix | Trigger | Tier | Action |
|-----|---------|------|--------|
| Null redirect | `> nul`, `2> nul` | Auto-fix | Rewrite to `> /dev/null` |
| Python3 alias | `python3 ...` | Auto-fix | Rewrite to `python` |
| PowerShell quoting | `pwsh -Command "$..."` | Auto-fix | Swap to single quotes |
| Reserved names | `> con`, `> prn`, `touch aux.txt` | Block | Reject -- undeletable files |
| Commit messages | Co-Authored-By, emoji, "Generated with" | Block | Reject with message |
| Doubled flags | `tasklist //fi` | Block | Suggest single `/` |
| Backslash paths | `C:\Users\...` | Block | Suggest `C:/Users/...` |
| cmd /c workaround | `cmd /c "..."` | Block | Reject with message |
| Legacy PowerShell | `powershell.exe ...` | Block | Suggest `pwsh` |
| Emoji in files | Write/Edit with emoji | Block | Reject with message |

## Installation

### Global (all projects, auto-updating)

```
git clone https://github.com/rweijnen/claude-hooks.git
cd claude-hooks
python install.py
```

This points `~/.claude/settings.json` directly at the repo's `hooks/` directory.
No files are copied -- the hooks run from the cloned repo. Updates are picked up
automatically: the hook checks once per day and runs a background `git pull` if
24+ hours have passed.

### Project-local (single project, good for testing)

```
python install.py --project /path/to/your/project
python install.py --project .
```

Copies hooks to `<project>/.claude/hooks/` and writes configuration to
`<project>/.claude/settings.local.json`. Only affects that one project.
These are snapshots -- re-run the installer to update.

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

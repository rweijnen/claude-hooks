# claude-hooks

PreToolUse hooks for Claude Code on Windows. These hooks intercept Bash commands and file writes before execution, automatically fixing common Git Bash/MSYS2 mistakes and enforcing code style preferences. No more `> nul` creating undeletable files, no more `python3` hitting the Windows Store alias, no more emoji in code.

## Fixes

| Fix | Trigger | Tier | Action |
|-----|---------|------|--------|
| Null redirect | `> nul`, `2> nul` | Auto-fix | Rewrite to `> /dev/null` |
| Python3 alias | `python3 ...` | Auto-fix | Rewrite to `python` |
| PowerShell quoting | `pwsh -Command "$..."` | Auto-fix | Swap to single quotes |
| Commit messages | Co-Authored-By, emoji, "Generated with" | Block | Reject with message |
| Doubled flags | `tasklist //fi` | Block | Suggest single `/` |
| Backslash paths | `C:\Users\...` | Block | Suggest `C:/Users/...` |
| cmd /c workaround | `cmd /c "..."` | Block | Reject with message |
| Legacy PowerShell | `powershell.exe ...` | Block | Suggest `pwsh` |
| Emoji in files | Write/Edit with emoji | Block | Reject with message |

## Installation

### Global (all projects)

```
python install.py
```

This will:
1. Copy hook scripts to `~/.claude/hooks/`
2. Add hook configuration to `~/.claude/settings.json`
3. Initialize a git repository (if needed)
4. Optionally create a GitHub repository via `gh`

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

Tier 2 suggestions (doubled flags, backslash paths) are logged to `~/.claude/hooks/fixups.log`:

```
cat ~/.claude/hooks/fixups.log
```

Each line is a JSON object with `timestamp`, `fix_type`, `original`, and `proposed`.

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

#!/usr/bin/env python
"""Install Claude Code hooks.

Copies hook scripts to ~/.claude/hooks/ and merges hook configuration
into ~/.claude/settings.json. Optionally initializes git and creates
a GitHub repository.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent
HOOKS_SRC = REPO_DIR / "hooks"
CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DST = CLAUDE_DIR / "hooks"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"

HOOKS_CONFIG = {
    "PreToolUse": [
        {
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": "python \"$HOME/.claude/hooks/fix_bash_command.py\"",
            }],
        },
        {
            "matcher": "Write|Edit",
            "hooks": [{
                "type": "command",
                "command": "python \"$HOME/.claude/hooks/check_file_content.py\"",
            }],
        },
    ],
}


def copy_hooks():
    """Copy all .py hook scripts to ~/.claude/hooks/."""
    HOOKS_DST.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sorted(HOOKS_SRC.glob("*.py")):
        dst = HOOKS_DST / src.name
        shutil.copy2(src, dst)
        print(f"  {src.name} -> {dst}")
        copied += 1
    if copied == 0:
        print("  No hook scripts found in hooks/ directory.", file=sys.stderr)
        sys.exit(1)
    return copied


def patch_settings():
    """Merge hook configuration into ~/.claude/settings.json."""
    settings = {}
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)

    settings.setdefault("hooks", {})
    settings["hooks"]["PreToolUse"] = HOOKS_CONFIG["PreToolUse"]

    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  Updated {SETTINGS_FILE}")


def git_init():
    """Initialize git repository if not already present."""
    if (REPO_DIR / ".git").exists():
        print("  Git repository already exists")
        return
    subprocess.run(["git", "init"], cwd=str(REPO_DIR), check=True)
    print("  Initialized git repository")


def gh_create():
    """Create GitHub repository using gh CLI."""
    answer = input("Create GitHub repo? (public/private/skip) [skip]: ").strip().lower()
    if answer in ("", "skip"):
        print("  Skipped GitHub repo creation")
        return
    if answer not in ("public", "private"):
        print(f"  Unknown option '{answer}', skipping", file=sys.stderr)
        return
    try:
        subprocess.run(
            ["gh", "repo", "create", "rweijnen/claude-hooks",
             f"--{answer}", "--source=.", "--push"],
            cwd=str(REPO_DIR),
            check=True,
        )
    except FileNotFoundError:
        print("  gh CLI not found. Install from https://cli.github.com/", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"  gh repo create failed (exit {e.returncode}). "
              "Create the repo manually.", file=sys.stderr)


def main():
    print("Installing Claude Code hooks...\n")

    print("1. Copying hook scripts:")
    count = copy_hooks()
    print(f"   {count} hook(s) installed\n")

    print("2. Patching settings.json:")
    patch_settings()
    print()

    print("3. Initializing git repository:")
    git_init()
    print()

    print("4. GitHub repository:")
    gh_create()
    print()

    print("Done. Restart Claude Code for hooks to take effect.")


if __name__ == "__main__":
    main()

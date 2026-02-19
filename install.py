#!/usr/bin/env python
"""Install Claude Code hooks.

Usage:
    python install.py                 # Global install (reference in place)
    python install.py --project DIR   # Project-local install (copies files)
    python install.py --project .     # Project-local install (current dir)

Global installs point settings.json directly at this repo's hooks/ directory.
Updates are picked up automatically via background git pull (once per day).

Project-local installs copy hooks to DIR/.claude/hooks/ and write config to
DIR/.claude/settings.local.json. These are self-contained snapshots -- re-run
the installer to update.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.resolve()
HOOKS_SRC = REPO_DIR / "hooks"


def hooks_config(hooks_dir):
    """Build the hooks config block pointing to the given hooks directory."""
    # Use forward slashes -- these run in Git Bash
    dst = str(hooks_dir).replace("\\", "/")
    return {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": f'python "{dst}/fix_bash_command.py"',
                }],
            },
            {
                "matcher": "Write|Edit",
                "hooks": [{
                    "type": "command",
                    "command": f'python "{dst}/check_file_content.py"',
                }],
            },
        ],
    }


def copy_hooks(hooks_dst):
    """Copy all .py hook scripts to the target directory."""
    hooks_dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sorted(HOOKS_SRC.glob("*.py")):
        dst = hooks_dst / src.name
        shutil.copy2(src, dst)
        print(f"  {src.name} -> {dst}")
        copied += 1
    if copied == 0:
        print("  No hook scripts found in hooks/ directory.", file=sys.stderr)
        sys.exit(1)
    return copied


def patch_settings(settings_file, hooks_dir):
    """Merge hook configuration into the settings file."""
    settings = {}
    if settings_file.exists():
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)

    settings.setdefault("hooks", {})
    settings["hooks"]["PreToolUse"] = hooks_config(hooks_dir)["PreToolUse"]

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  Updated {settings_file}")


def main():
    parser = argparse.ArgumentParser(description="Install Claude Code hooks")
    parser.add_argument(
        "--project", metavar="DIR",
        help="Install to a specific project directory (copies hooks, uses "
             "settings.local.json). Omit for global install.",
    )
    args = parser.parse_args()

    if args.project:
        # Project-local: copy hooks, write settings.local.json
        project = Path(args.project).resolve()
        hooks_dst = project / ".claude" / "hooks"
        settings_file = project / ".claude" / "settings.local.json"

        print(f"Installing Claude Code hooks (project: {project})...\n")

        print("1. Copying hook scripts:")
        count = copy_hooks(hooks_dst)
        print(f"   {count} hook(s) copied\n")

        print("2. Patching settings.local.json:")
        patch_settings(settings_file, hooks_dst)
    else:
        # Global: reference hooks in place, write settings.json
        settings_file = Path.home() / ".claude" / "settings.json"

        print(f"Installing Claude Code hooks (global, in-place)...\n")

        print(f"1. Using hooks from repo: {HOOKS_SRC}")
        print(f"   (no copy -- updates via git pull are picked up automatically)\n")

        print("2. Patching settings.json:")
        patch_settings(settings_file, HOOKS_SRC)

    print()
    print("Done. Hooks take effect on the next tool call.")


if __name__ == "__main__":
    main()

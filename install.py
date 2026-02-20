#!/usr/bin/env python
"""Install Claude Code hooks.

Usage:
    python install.py                 # Global install (~/.claude/)
    python install.py --project DIR   # Project-local install (DIR/.claude/)
    python install.py --project .     # Project-local install (current dir)

Global installs go to ~/.claude/settings.json and affect all projects.
Project-local installs go to DIR/.claude/settings.local.json and only
affect that project -- useful for testing hooks in a sandbox.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent
HOOKS_SRC = REPO_DIR / "hooks"


def get_paths(project_dir=None):
    """Return (hooks_dst, settings_file) for global or project-local install."""
    if project_dir:
        base = Path(project_dir).resolve() / ".claude"
        return base / "hooks", base / "settings.local.json"
    base = Path.home() / ".claude"
    return base / "hooks", base / "settings.json"


def hooks_config(hooks_dst):
    """Build the hooks config block with the correct path to hook scripts."""
    # Use forward slashes -- these run in Git Bash
    dst = str(hooks_dst).replace("\\", "/")
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
    """Copy all .py hook scripts and config.sample.json to the target directory."""
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

    # Copy config.sample.json (always overwrite -- it's the reference copy)
    sample_src = HOOKS_SRC / "config.sample.json"
    if sample_src.exists():
        sample_dst = hooks_dst / "config.sample.json"
        shutil.copy2(sample_src, sample_dst)
        print(f"  {sample_src.name} -> {sample_dst}")

    # Do NOT overwrite existing config.json (preserve user choices)
    config_dst = hooks_dst / "config.json"
    if not config_dst.exists():
        print(f"  No config.json found -- using built-in defaults")
        print(f"  Copy config.sample.json to config.json to customize checks")

    return copied


def patch_settings(settings_file, hooks_dst):
    """Merge hook configuration into the settings file."""
    settings = {}
    if settings_file.exists():
        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)

    settings.setdefault("hooks", {})
    settings["hooks"]["PreToolUse"] = hooks_config(hooks_dst)["PreToolUse"]

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  Updated {settings_file}")


def main():
    parser = argparse.ArgumentParser(description="Install Claude Code hooks")
    parser.add_argument(
        "--project", metavar="DIR",
        help="Install to a specific project directory (uses settings.local.json). "
             "Omit for global install to ~/.claude/",
    )
    args = parser.parse_args()

    hooks_dst, settings_file = get_paths(args.project)
    scope = f"project ({Path(args.project).resolve()})" if args.project else "global (~/.claude/)"

    print(f"Installing Claude Code hooks ({scope})...\n")

    print("1. Copying hook scripts:")
    count = copy_hooks(hooks_dst)
    print(f"   {count} hook(s) installed\n")

    print("2. Patching settings:")
    patch_settings(settings_file, hooks_dst)
    print()

    print("Done. Hooks will take effect on the next tool call.")


if __name__ == "__main__":
    main()

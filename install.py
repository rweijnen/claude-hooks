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
import subprocess
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

    if not args.project:
        print("3. Initializing git repository:")
        git_init()
        print()

        print("4. GitHub repository:")
        gh_create()
        print()

    print("Done. Restart Claude Code for hooks to take effect.")


if __name__ == "__main__":
    main()

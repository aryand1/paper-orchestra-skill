#!/usr/bin/env python3
"""Install the PaperOrchestra skills into one or more coding agents' skill folders.

Works on Windows, macOS and Linux with the Python standard library only.

    python install.py                      # Claude Code, user level (~/.claude/skills)
    python install.py --agent all          # Claude Code, Codex, generic agents, Gemini
    python install.py --project .          # Claude Code, this project only (./.claude/skills)
    python install.py --dest DIR           # any folder
    python install.py --mode copy          # copy instead of link
    python install.py --uninstall          # remove what this script installed
    python install.py --list               # show the skills and the target folders

Each skill folder is linked (a symlink, or a directory junction on Windows, which
needs no admin rights). If linking fails, it is copied. The skills run helper
scripts from this repository, so keep the cloned folder where it is; its path is
saved as PAPERORCHESTRA_REPO in ~/.paperorchestra/config.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SKILLS = REPO / "skills"
MARKER = ".paper-orchestra-skill"          # written into copied folders so uninstall can find them
CONFIG = Path.home() / ".paperorchestra" / "config"

AGENTS = {
    "claude": Path.home() / ".claude" / "skills",
    "codex": Path.home() / ".codex" / "skills",
    "agents": Path.home() / ".agents" / "skills",          # OpenClaw, OpenCode and other AGENTS.md agents
    "gemini": Path.home() / ".gemini" / "antigravity" / "skills",
}


def skill_dirs() -> list[Path]:
    """Every folder under skills/ that has a SKILL.md (skills/shared is reference material, not a skill)."""
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def is_link(p: Path) -> bool:
    if p.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)          # Python 3.12+
    if isjunction and isjunction(p):
        return True
    if os.name == "nt" and p.is_dir():
        try:
            os.readlink(p)                                      # junctions on Python 3.8-3.11
            return True
        except (OSError, ValueError):
            return False
    return False


def link_target(p: Path) -> Path | None:
    try:
        t = Path(os.readlink(p))
    except (OSError, ValueError):
        return None
    s = str(t)
    if s.startswith("\\\\?\\"):
        t = Path(s[4:])
    return t if t.is_absolute() else (p.parent / t)


def ours(p: Path) -> bool:
    """True if p was installed by this script (a link into this repo, or a copy with the marker)."""
    if is_link(p):
        t = link_target(p)
        try:
            return t is not None and t.resolve().parent == SKILLS
        except OSError:
            return False
    return p.is_dir() and (p / MARKER).is_file()


def remove(p: Path) -> None:
    if is_link(p):
        try:
            p.unlink()
        except (IsADirectoryError, PermissionError, OSError):
            os.rmdir(p)                                         # directory symlinks and junctions on Windows
    else:
        shutil.rmtree(p)


def make_link(src: Path, dst: Path) -> str:
    try:
        os.symlink(src, dst, target_is_directory=True)
        return "symlink"
    except OSError:
        if os.name != "nt":
            raise
    import _winapi                                              # Windows: junctions need no admin rights
    _winapi.CreateJunction(str(src), str(dst))
    return "junction"


def install_one(src: Path, dest_dir: Path, mode: str) -> str:
    dst = dest_dir / src.name
    if dst.exists() or is_link(dst):
        if not ours(dst):
            return f"skipped {dst} (exists and was not installed by this script)"
        remove(dst)
    if mode in ("auto", "link"):
        try:
            return f"{make_link(src, dst)}  {dst}"
        except OSError as e:
            if mode == "link":
                return f"FAILED  {dst}: {e}"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dst / MARKER).write_text(f"copied from {REPO}\n", encoding="utf-8")
    return f"copy     {dst}"


def write_config() -> None:
    """Record the repo path, keeping any other KEY=value lines already in the file."""
    lines = []
    if CONFIG.is_file():
        lines = [l for l in CONFIG.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("PAPERORCHESTRA_REPO=")]
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text("\n".join([f"PAPERORCHESTRA_REPO={REPO}"] + lines) + "\n", encoding="utf-8")


def targets(args) -> list[Path]:
    out = []
    if args.project:
        out.append(Path(args.project).resolve() / ".claude" / "skills")
    for d in args.dest or []:
        out.append(Path(d).expanduser().resolve())
    agents = args.agent or ([] if out else ["claude"])
    for a in agents:
        out += list(AGENTS.values()) if a == "all" else [AGENTS[a]]
    return list(dict.fromkeys(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--agent", action="append", choices=[*AGENTS, "all"],
                    help="agent skill folder(s) to install into (default: claude)")
    ap.add_argument("--project", metavar="DIR", help="install into DIR/.claude/skills instead of the user folder")
    ap.add_argument("--dest", action="append", metavar="DIR", help="install into this folder (repeatable)")
    ap.add_argument("--mode", choices=["auto", "link", "copy"], default="auto",
                    help="auto: link, or copy if linking fails (default)")
    ap.add_argument("--uninstall", action="store_true", help="remove the skills this script installed")
    ap.add_argument("--list", action="store_true", help="list the skills and targets, change nothing")
    args = ap.parse_args()

    skills, dests = skill_dirs(), targets(args)
    if args.list:
        print("skills:", ", ".join(s.name for s in skills))
        print("targets:", *dests, sep="\n  ")
        return 0

    failed = False
    for dest in dests:
        print(f"\n{dest}")
        if args.uninstall:
            for s in skills:
                p = dest / s.name
                if (p.exists() or is_link(p)) and ours(p):
                    remove(p)
                    print(f"  removed  {p}")
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for s in skills:
            msg = install_one(s, dest, args.mode)
            failed |= msg.startswith("FAILED")
            print("  " + msg)

    if not args.uninstall:
        write_config()
        print(f"\nRepo path saved to {CONFIG}")
        print("Next: pip install -r requirements.txt, install a LaTeX distribution (pdflatex + bibtex),")
        print('then ask your agent: "Run the paper-orchestra pipeline on ./workspace".')
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

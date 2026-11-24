#!/usr/bin/env python3
"""
install-shims: make every script in this folder runnable by name from anywhere.

WRITES FILES (outside this folder): one tiny launcher per *.py found here, into
~/.local/bin (Linux/macOS) or %USERPROFILE%\\bin (Windows). Nothing inside this
folder is changed. It only overwrites or deletes launchers it created itself
(they carry a marker line). Standard library only.

  python install-shims.py             create/refresh the launchers
  python install-shims.py --remove    delete the launchers it created
  python install-shims.py --dir DIR   use another launcher folder

The launchers hard-code this folder's absolute path. If you move the folder,
run this again. On a machine where you haven't run it, the plain
`python <script>.py` from the README still works.
"""

import argparse
import os
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELF = Path(__file__).name
WIN = os.name == "nt"
MARK = "breakglass-launcher"


def scripts():
    return sorted(p for p in HERE.glob("*.py") if p.name != SELF)


def shim_path(bindir, script):
    return bindir / (script.stem + (".cmd" if WIN else ""))


def shim_text(script):
    if WIN:
        return f'@rem {MARK}\r\n@python "{script}" %*\r\n'
    return f'#!/bin/sh\n# {MARK}\nexec python3 {shlex.quote(str(script))} "$@"\n'


def is_ours(path):
    try:
        return MARK in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def on_path(bindir):
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.expandvars(p)))
    return norm(str(bindir)) in {norm(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p}


def path_hint(bindir):
    print(f"\n{bindir} is not on your PATH yet.", file=sys.stderr)
    if WIN:
        print("Add it: Win+R > sysdm.cpl > Advanced > Environment Variables > "
              "User variables > Path > New. Then fully close and reopen the terminal.",
              file=sys.stderr)
    else:
        print(f'Add it: echo \'export PATH="{bindir}:$PATH"\' >> ~/.profile, '
              "then log out and in (many distros add ~/.local/bin on login by themselves).",
              file=sys.stderr)


def main():
    default = Path.home() / ("bin" if WIN else ".local/bin")
    p = argparse.ArgumentParser(description="Create launchers so the scripts here run by name.")
    p.add_argument("--remove", action="store_true", help="delete the launchers this tool created")
    p.add_argument("--dir", type=Path, default=default, help=f"launcher folder (default: {default})")
    args = p.parse_args()
    bindir = args.dir.expanduser().resolve()

    if args.remove:
        for s in scripts():
            target = shim_path(bindir, s)
            if target.exists() and is_ours(target):
                target.unlink()
                print(f"removed {target}")
        return

    bindir.mkdir(parents=True, exist_ok=True)
    for s in scripts():
        target = shim_path(bindir, s)
        if target.exists() and not is_ours(target):
            print(f"skipped {target.name}: exists and was not created by this tool", file=sys.stderr)
            continue
        target.write_bytes(shim_text(s).encode("utf-8"))
        if not WIN:
            target.chmod(0o755)
        print(f"{s.stem}  ->  {target}")
    if not on_path(bindir):
        path_hint(bindir)


if __name__ == "__main__":
    main()

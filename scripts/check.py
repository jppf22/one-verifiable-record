#!/usr/bin/env python3
"""Dependency-free sanity checks: compile every Python module and verify that
all cross-module imports (``from x import y`` between local modules) resolve.

Run with ``make check`` or ``python3 scripts/check.py``. Catches the class of
regression where a merge replaces one module with an older version whose
neighbours still import the old names.
"""
import ast
import os
import py_compile
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = ("server", "jobs", "db/load", "scripts")


def module_names(path: str) -> set:
    tree = ast.parse(open(path).read())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, ast.Import):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                names.add(a.asname or a.name)
    return names


def main() -> int:
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        for pkg in PACKAGES:
            pkg_dir = os.path.join(ROOT, pkg)
            files = [f for f in os.listdir(pkg_dir) if f.endswith(".py")]
            for f in files:
                path = os.path.join(pkg_dir, f)
                try:
                    py_compile.compile(path, cfile=os.path.join(tmp, f + ".pyc"), doraise=True)
                except py_compile.PyCompileError as exc:
                    print(f"COMPILE FAIL {pkg}/{f}: {exc}")
                    failed = True
            mods = {f[:-3]: module_names(os.path.join(pkg_dir, f)) for f in files}
            for f in files:
                tree = ast.parse(open(os.path.join(pkg_dir, f)).read())
                for n in ast.walk(tree):
                    if (isinstance(n, ast.ImportFrom) and n.module and n.level == 0
                            and n.module.split(".")[0] in mods):
                        for a in n.names:
                            if a.name != "*" and a.name not in mods[n.module.split(".")[0]]:
                                print(f"IMPORT FAIL {pkg}/{f}: from {n.module} import {a.name}")
                                failed = True
    if failed:
        print("CHECK FAILED")
        return 1
    print("CHECK OK: all modules compile, all local imports resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Report names a router module uses but never defines or imports.

Closure parameters of ``register`` count as local bindings, so the report only
lists genuinely unresolved identifiers — the ones that would raise NameError
at request time.
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path


def _bind_target(target: ast.AST, names: set[str]) -> None:
    """Collect names bound by an assignment/loop target, including tuples."""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            _bind_target(element, names)
    elif isinstance(target, ast.Starred):
        _bind_target(target.value, names)


def defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                for group in (
                    args.posonlyargs,
                    args.args,
                    args.kwonlyargs,
                ):
                    names.update(a.arg for a in group)
                if args.vararg:
                    names.add(args.vararg.arg)
                if args.kwarg:
                    names.add(args.kwarg.arg)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _bind_target(target, names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _bind_target(node.target, names)
        elif isinstance(node, ast.NamedExpr):
            _bind_target(node.target, names)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _bind_target(node.target, names)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _bind_target(node.optional_vars, names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.comprehension):
            _bind_target(node.target, names)
    return names


def main(paths: list[str]) -> int:
    failures = 0
    builtin_names = set(dir(builtins))
    for path in paths:
        source = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = defined_names(tree)
        used = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        unknown = sorted(used - defined - builtin_names)
        status = "OK" if not unknown else "MISSING"
        print(f"{status} {path}: {', '.join(unknown) if unknown else '—'}")
        if unknown:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

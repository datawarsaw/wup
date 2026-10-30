#!/usr/bin/env python3
"""Guard the repository against dead imports left by infrastructure removal.

The notification, scheduler, and remote-sentinel modules were deleted. Any
module that still imports them fails at import time, and any import that is
never referenced is dead weight. These checks keep both classes of defect out
of the retained product surface and out of the test suite.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"

REMOVED_MODULES = (
    "run_notifier",
    "telegram_notify",
    "telegram_secrets",
    "remote_version_sentinel",
    "workstation_snapshot",
)

# Product modules reworked by the notification-infrastructure removal.
TOUCHED_SCRIPT_MODULES = (
    "change_history.py",
    "check_toolchain.py",
    "doctor.py",
    "preflight.py",
    "wup_config.py",
)


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imported_symbols(path: Path) -> list:
    """Return one record per imported name, including its import line span."""
    found = []
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(
                    {
                        "module": alias.name,
                        "bound": alias.asname or alias.name.split(".")[0],
                        "statement": "import " + alias.name,
                        "span": (node.lineno, node.end_lineno or node.lineno),
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                found.append(
                    {
                        "module": node.module or "",
                        "bound": alias.asname or alias.name,
                        "statement": "from " + (node.module or ".") + " import " + alias.name,
                        "span": (node.lineno, node.end_lineno or node.lineno),
                    }
                )
    return found


def unused_imports(path: Path) -> list:
    """Return (bound_name, statement) for imports never referenced outside imports."""
    records = imported_symbols(path)
    source_lines = path.read_text(encoding="utf-8").splitlines()
    import_lines = set()
    for record in records:
        start, end = record["span"]
        import_lines.update(range(start, end + 1))
    separator = chr(10)
    body = separator.join(
        line for number, line in enumerate(source_lines, start=1) if number not in import_lines
    )

    dead = []
    for record in records:
        if re.search(r"\b" + re.escape(record["bound"]) + r"\b", body):
            continue
        dead.append((record["bound"], record["statement"]))
    return dead


class RemovedInfrastructureImportTests(unittest.TestCase):
    def test_no_module_imports_removed_notification_infrastructure(self) -> None:
        for path in sorted(list(SCRIPTS.glob("*.py")) + list(TESTS.glob("*.py"))):
            for record in imported_symbols(path):
                if record["module"].split(".")[0] in REMOVED_MODULES:
                    self.fail(
                        "{} imports removed module via: {}".format(
                            path.relative_to(ROOT), record["statement"]
                        )
                    )

    def test_first_party_imports_resolve_to_existing_files(self) -> None:
        local_names = {path.stem for path in SCRIPTS.glob("*.py")} | {path.stem for path in TESTS.glob("*.py")}
        for path in sorted(list(SCRIPTS.glob("*.py")) + list(TESTS.glob("*.py"))):
            for record in imported_symbols(path):
                module = record["module"]
                if not module or "." in module:
                    continue
                if module in sys.stdlib_module_names or module in local_names:
                    continue
                self.fail(
                    "{} imports a module that does not exist: {}".format(
                        path.relative_to(ROOT), record["statement"]
                    )
                )


class DeadImportTests(unittest.TestCase):
    def test_touched_product_modules_have_no_unused_imports(self) -> None:
        for name in TOUCHED_SCRIPT_MODULES:
            with self.subTest(module=name):
                path = SCRIPTS / name
                self.assertTrue(path.is_file(), "expected product module: " + name)
                self.assertEqual([], unused_imports(path), "dead imports in " + name)

    def test_test_modules_have_no_unused_imports(self) -> None:
        for path in sorted(TESTS.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertEqual([], unused_imports(path), "dead imports in " + path.name)


if __name__ == "__main__":
    unittest.main()

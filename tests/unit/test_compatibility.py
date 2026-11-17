"""Compatibility guarantees for historical imports and operational entry points."""
import ast
import builtins
import importlib
from pathlib import Path
import subprocess
import sys
import symtable
import unittest
from unittest.mock import patch

from courswatch.compat import ALIASES

ROOT = Path(__file__).resolve().parents[2]


class CompatibilityTests(unittest.TestCase):
    def test_legacy_and_canonical_imports_share_the_same_module(self):
        for legacy, target in ALIASES.items():
            with self.subTest(module=legacy):
                self.assertIs(importlib.import_module(legacy), importlib.import_module(target))

    def test_monkeypatches_reach_the_canonical_implementation(self):
        legacy = importlib.import_module('course_bot')
        canonical = importlib.import_module(ALIASES['course_bot'])
        with patch('course_bot.configuration', return_value={'synthetic': True}):
            self.assertEqual(canonical.configuration(), {'synthetic': True})
            self.assertIs(legacy.configuration, canonical.configuration)

    def test_legacy_help_commands_remain_available_without_state_access(self):
        for script in ('cours_watch.py', 'course_pdf_publish.py', 'course_obsidian.py', 'publish_course_vault.py'):
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, '-B', str(ROOT / script), '--help'],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)

    def test_application_imports_do_not_depend_on_legacy_entry_points(self):
        for path in (ROOT / 'courswatch').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                self.assertFalse(set(names) & ALIASES.keys(), str(path))

    def test_package_global_references_have_imported_or_builtin_definitions(self):
        def scopes(table):
            yield table
            for child in table.get_children():
                yield from scopes(child)

        for path in (ROOT / 'courswatch').rglob('*.py'):
            relative = path.relative_to(ROOT).with_suffix('').parts
            name = '.'.join(relative[:-1] if relative[-1] == '__init__' else relative)
            module = importlib.import_module(name)
            symbols = symtable.symtable(path.read_text(encoding='utf-8'), str(path), 'exec')
            known = set(vars(module)) | set(vars(builtins))
            missing = {symbol.get_name() for scope in scopes(symbols) for symbol in scope.get_symbols()
                       if symbol.is_global() and symbol.is_referenced() and symbol.get_name() not in known
                       # Python 3.12 inlines comprehensions and records their
                       # temporary targets in the module symbol table.
                       and not (scope.get_type() == 'module' and symbol.is_local() and symbol.is_assigned())}
            with self.subTest(module=name):
                self.assertEqual(missing, set())


if __name__ == '__main__':
    unittest.main()

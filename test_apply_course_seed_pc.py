"""Compatibility adapter for tests.migration.test_apply_course_seed_pc."""
import importlib
import sys

implementation = importlib.import_module('tests.migration.test_apply_course_seed_pc')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

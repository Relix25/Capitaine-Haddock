"""Compatibility adapter for tests.migration.test_configure_course_sync."""
import importlib
import sys

implementation = importlib.import_module('tests.migration.test_configure_course_sync')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

"""Compatibility adapter for tests.unit.test_course_health."""
import importlib
import sys

implementation = importlib.import_module('tests.unit.test_course_health')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

"""Compatibility adapter for tests.unit.test_course_translation."""
import importlib
import sys

implementation = importlib.import_module('tests.unit.test_course_translation')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

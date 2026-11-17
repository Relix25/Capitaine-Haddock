"""Compatibility adapter for tests.migration.test_prepare_course_seed."""
import importlib
import sys

implementation = importlib.import_module('tests.migration.test_prepare_course_seed')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

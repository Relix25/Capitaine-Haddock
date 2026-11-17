"""Compatibility adapter for tests.operations.test_operations."""
import importlib
import sys

implementation = importlib.import_module('tests.operations.test_operations')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

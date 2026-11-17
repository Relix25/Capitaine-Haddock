"""Compatibility adapter for tests.unit.test_luna_brain."""
import importlib
import sys

implementation = importlib.import_module('tests.unit.test_luna_brain')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

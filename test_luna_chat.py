"""Compatibility adapter for tests.unit.test_luna_chat."""
import importlib
import sys

implementation = importlib.import_module('tests.unit.test_luna_chat')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

"""Compatibility adapter for tests.integration.test_moodle_pdf_collection."""
import importlib
import sys

implementation = importlib.import_module('tests.integration.test_moodle_pdf_collection')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

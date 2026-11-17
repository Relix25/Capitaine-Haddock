"""Compatibility adapter for tests.integration.test_course_pdf_publish."""
import importlib
import sys

implementation = importlib.import_module('tests.integration.test_course_pdf_publish')
if __name__ == '__main__':
    import unittest
    unittest.main(module=implementation)
else:
    sys.modules[__name__] = implementation

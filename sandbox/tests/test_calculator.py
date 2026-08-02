"""The oracle. Each failing test is an open task; a passing suite is a met milestone.
Agents may NOT edit this file — the workspace refuses patches under tests/."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calculator as c


class TestCalculator(unittest.TestCase):
    def test_add(self):
        self.assertEqual(c.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(c.subtract(7, 3), 4)

    def test_multiply(self):
        self.assertEqual(c.multiply(3, 4), 12)
        self.assertEqual(c.multiply(-2, 5), -10)

    def test_divide(self):
        self.assertEqual(c.divide(10, 4), 2.5)

    def test_divide_by_zero(self):
        with self.assertRaises(ValueError):
            c.divide(1, 0)

    def test_power(self):
        self.assertEqual(c.power(2, 10), 1024)
        self.assertEqual(c.power(5, 0), 1)


if __name__ == "__main__":
    unittest.main()

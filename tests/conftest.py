"""Shared test setup: make the flat src/ modules importable."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

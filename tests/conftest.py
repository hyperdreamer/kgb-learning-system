"""Shared pytest fixtures for the KGB SRS test suite."""

import os
import sys

# Ensure the project root is on sys.path for imports.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

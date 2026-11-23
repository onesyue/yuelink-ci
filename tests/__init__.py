"""Repository tests discoverable from the project root."""

from pathlib import Path


def load_tests(loader, _tests, pattern):
    # Use the same module names as policy CI so sibling test imports resolve.
    directory = str(Path(__file__).resolve().parent)
    return loader.discover(directory, pattern=pattern, top_level_dir=directory)

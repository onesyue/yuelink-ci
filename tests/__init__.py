"""Use the policy suite's flat module layout for repository-root discovery."""

from pathlib import Path


def load_tests(loader, standard_tests, pattern):
    directory = str(Path(__file__).resolve().parent)
    return loader.discover(directory, pattern=pattern, top_level_dir=directory)

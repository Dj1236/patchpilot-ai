"""Trusted pytest launcher, baked into the sandbox image.

Started as ``python -P -s -B /opt/patchpilot/run_tests.py``. Because of
``-P`` neither this script's directory nor the working directory is on the
import path, so pytest and everything it needs are imported from the image
before any workspace directory becomes importable. A file such as
``pytest.py`` in the workspace therefore cannot replace the test runner.

Usage: run_tests.py [--source-root DIR]... -- PYTEST_ARGS...
"""

import os
import sys
import xml.etree.ElementTree  # noqa: F401

# Imported now so that workspace modules of the same name cannot be picked up
# when pytest loads these lazily.
import _pytest.junitxml  # noqa: F401
import pytest


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("run_tests.py: missing '--' before the pytest arguments", file=sys.stderr)
        return 4
    split = argv.index("--")
    options, pytest_args = argv[:split], argv[split + 1 :]
    roots: list[str] = []
    while options:
        if options[0] != "--source-root" or len(options) < 2:
            print(f"run_tests.py: unexpected argument {options[0]!r}", file=sys.stderr)
            return 4
        roots.append(options[1])
        options = options[2:]
    # Appended, not prepended: workspace code can never shadow the standard
    # library or the packages installed in the image.
    sys.path.extend(roots)
    return int(pytest.main(pytest_args))


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # Leave without running exit handlers registered by the code under test,
    # so they cannot rewrite the report or change the exit status afterwards.
    os._exit(code)

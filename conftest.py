"""Refuse to run the suite on versions the image does not install.

`ops/scripts/validate_repository.py --check-pins` covers the verify script, but
a guard that only runs there is one `python -m pytest` away from being
bypassed — including inside the container, where `pip install -r
requirements.txt` goes into the base interpreter and there is no venv to
recognise. Checking here ties the guard to the thing that actually matters:
whichever interpreter is collecting these tests.
"""

import pytest

from ops.scripts.validate_repository import dependency_pin_report


def pytest_configure(config):
    _, failure = dependency_pin_report()
    if failure:
        raise pytest.UsageError(failure)

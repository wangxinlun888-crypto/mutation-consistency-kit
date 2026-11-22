# Install and verify locally

Use Python 3.10+ with the standard sqlite3 module and a local filesystem. Commands below use POSIX paths inside a virtual environment; on Windows replace .venv/bin/python with .venv/Scripts/python.exe. No service is required.

```sh
python -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps /path/to/mutation_consistency_kit-0.1.1-py3-none-any.whl
.venv/bin/python -I -c "import sqlite3; from importlib.metadata import version; print(version('mutation-consistency-kit'), sqlite3.sqlite_version)"
.venv/bin/python -I examples/recovery_journey.py
.venv/bin/python -m unittest discover -v -s tests
```

Obtain the example and tests from the matching source archive. The example makes a new temporary database per invocation and prints step evidence as JSON. To retain the synthetic evidence, pass `--evidence-dir <new-directory>`; existing directories are refused. The writer's internal exit 74 is intentional. The parent exits zero only after recovery and stale revision rejection.

To build locally from source with a preinstalled compatible setuptools backend, use `python -m pip wheel --no-index --no-deps --no-build-isolation .` in a scratch copy. Installing a wheel does not need setuptools. A read failure, missing receipt or UNKNOWN is not a confirmed rejection or permission to resubmit.

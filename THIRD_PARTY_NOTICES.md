# Third-party notices

This distribution contains project Python code and documentation, not a Python interpreter, SQLite binary, vendored library or build tool. No third-party Python packages are required at runtime. These references identify external components; they do not grant a license to this project.

| Component | Use and upstream terms |
| --- | --- |
| CPython and its standard library | Supplied by the user. [PSF License Version 2 and component notices](https://docs.python.org/3.14/license.html). The [sqlite3 binding](https://github.com/python/cpython/blob/v3.14.0/Modules/_sqlite/connection.c) has its own pysqlite-origin notice and zlib-style terms. |
| SQLite | Engine supplied by the interpreter or system. Upstream [public-domain dedication](https://sqlite.org/copyright.html) covers deliverable library code, not every build script. |
| setuptools | Source build backend only. [MIT license](https://github.com/pypa/setuptools/blob/v81.0.0/LICENSE); vendored components retain their terms. |
| pip | Optional local installer. [MIT license](https://github.com/pypa/pip/blob/25.2/LICENSE.txt); vendored components retain their terms. |

Changing the distribution to bundle any of these components requires a new inventory and retention of the applicable notices.

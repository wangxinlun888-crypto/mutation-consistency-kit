# Mutation Consistency Kit

A local SQLite library for replacing a JSON document without losing track of which request committed. Bring an operation key and the revision you read. An identical duplicate returns the original receipt; it does not apply the change again.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from mutation_consistency_kit import Store

with TemporaryDirectory() as folder:
    store = Store(Path(folder) / "stock.sqlite")
    first = store.create("drawer", {"count": 4})
    result = store.replace("count-01", first.target, first.revision, {"count": 6})
    print(result.status.value, result.result_revision)  # SUCCEEDED 2
    print(store.recover("count-01") == result)          # True
```

If a response is lost, keep the key and use `recover`. An absent or UNKNOWN receipt is not a reason to retry a write. Receipts describe local document commits, not delivery to a remote party. History contains full document bodies: use a local filesystem with appropriate permissions. This library does not implement authentication, encryption, external side effects or power-loss certification.

- [Install and run the examples](docs/INSTALL.md)
- [Python API and failure states](docs/API.md)
- [Transactions and evidence](docs/ARCHITECTURE.md)
- [Historical 0.1.1rc1 acceptance results](docs/TEST_RESULTS.md)
- [0.1.1 release notes](RELEASE_NOTES.md)
- [Complete two-process example](examples/recovery_journey.py)
- [Dependencies and notices](THIRD_PARTY_NOTICES.md)
- [When to choose this library over an ORM or workflow engine](COMPARISON.md)

Project license text: [Apache License, Version 2.0](LICENSE).
Third-party components retain their original terms.

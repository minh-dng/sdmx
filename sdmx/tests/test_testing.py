import json
from contextlib import nullcontext

from sdmx.testing import installed_schemas
from sdmx.testing.report import main


def test_report_main(tmp_path):
    # Example input data
    with open(tmp_path.joinpath("TEST.json"), "w") as f:
        json.dump({"TEST": {"foo": "pass"}}, f)

    # Function runs
    main(tmp_path)

    # Output files are generated
    assert tmp_path.joinpath("all-data.json").exists()
    assert tmp_path.joinpath("index.html").exists()


def test_installed_schemas_worker_isolation(monkeypatch, tmp_path):
    import sdmx.testing as testing

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr("platformdirs.user_cache_path", lambda app, **kwargs: cache)
    locks = []
    monkeypatch.setattr(
        testing, "FileLock", lambda path: (locks.append(path), nullcontext())[1]
    )
    monkeypatch.setattr(
        "sdmx.format.xml.common.install_schemas",
        lambda path, version: path.mkdir(parents=True, exist_ok=True),
    )

    class WorkerPaths:
        def __init__(self, worker):
            self.base = tmp_path / worker
            self.base.mkdir()

        def mktemp(self, name):
            path = self.base / name
            path.mkdir()
            return path

        def getbasetemp(self):
            return self.base

    paths = []
    for worker in ("gw0", "gw1"):
        fixture = installed_schemas.__wrapped__(
            worker, nullcontext(), WorkerPaths(worker)
        )
        path = next(fixture)
        paths.append(path)
        fixture.close()

    assert paths[0] != paths[1]
    assert all(
        (path / version).is_dir() for path in paths for version in ("2.1", "3.0.0")
    )
    assert locks == [cache / "schemas.lock"] * 2

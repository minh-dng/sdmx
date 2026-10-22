"""Code for working with :file:`sdmx-test-data`."""

import logging
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import platformdirs
import pytest

if TYPE_CHECKING:
    import git
    import pandas
    from _pytest.mark.structures import ParameterSet

    from sdmx.session import Session
    from sdmx.source import Source

log = logging.getLogger(__name__)

#: Default directory for local copy of sdmx-test-data.
DEFAULT_DIR = platformdirs.user_cache_path("sdmx").joinpath("test-data")

#: Expected to_pandas() results for data files; see :meth:`SpecimenCollection.expected_data`
#:
#: - Keys are the file name (see :func:`.add_specimens`) with '.' -> '-': 'foo.xml' ->
#:   'foo-xml'.
#: - Data is stored in :file:`sdmx-test-data/expected/{KEY}.txt`.
#: - Values are either argument to :func:`pandas.read_csv`; or a dict(use='other-key'),
#:   in which case the info for other-key is used instead.
EXPECTED: dict[str, dict[str, Any]] = {
    "ng-flat-xml": dict(index_col=[0, 1, 2, 3, 4, 5]),
    "ng-ts-gf-xml": dict(use="ng-flat-xml"),
    "ng-ts-xml": dict(use="ng-flat-xml"),
    "ng-xs-xml": dict(index_col=[0, 1, 2, 3, 4, 5]),
    # Excluded: this file contains two DataSets, and
    # :meth:`SpecimenCollection.expected_data` currently only supports specimens with
    # one DataSet.
    # 'action-delete-json': dict(header=[0, 1, 2, 3, 4]),
    "xs-json": dict(index_col=[0, 1, 2, 3, 4, 5]),
    "flat-json": dict(index_col=[0, 1, 2, 3, 4, 5]),
    "ts-json": dict(use="flat-json"),
}

ERROR = (
    "Unable to locate test specimens. Give --sdmx-fetch-data, or use --sdmx-test-data=…"
    "or the SDMX_TEST_DATA environment variable to indicate an existing directory"
)

#: Git remote URL for cloning test data.
REMOTE_URL = "https://github.com/khaeru/sdmx-test-data.git"
# REMOTE_URL = "git@github.com:khaeru/sdmx-test-data.git"


def _rmtree(path: Path) -> None:
    """Remove `path`, including the read-only object files that Git creates."""
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.chmod(os.path.join(root, name), stat.S_IRWXU)
    shutil.rmtree(path)


class SpecimenCollection:
    """Collection of test specimens.

    Parameters
    ----------
    path :
       Path containing :file:`sdmx-test-data`, or to which to clone the repository.
    fetch :
       If :any:`True`, call :meth:`fetch`.
    refresh :
       Passed to :meth:`fetch`.
    """

    #: Base path containing the specimen collection.
    base_path: Path

    #: Each tuple contains:
    #:
    #: 1. Path to specimen file.
    #: 2. Format: one of "csv", "json", "xml".
    #: 3. Message type: either "data", "structure", or None.
    specimens: list[tuple[Path, str, str | None]]

    def __init__(self, path: Path, fetch: bool, refresh: bool = False):
        self.base_path = path

        if fetch or refresh:
            self.fetch(refresh)

        # Check the base directory exists
        if not self.base_path.exists():  # pragma: no cover
            # Cannot proceed further; this exception kills the test session
            raise FileNotFoundError(f"SDMX test data in {path}\n{ERROR}")

        self.specimens = []
        add_specimens(self.specimens, self.base_path)

    @contextmanager
    def __call__(self, pattern="", opened: bool = True):
        """Open the test specimen file with `pattern` in the name.

        With :py:`opened=True` (default), return a file-like object. With
        :py:`opened=False`, return only the full path to the specimen file.
        """
        for path, f, k in self.specimens:
            if path.match("*" + pattern + "*"):
                yield open(path, "br") if opened else path
                return
        raise ValueError(pattern)  # pragma: no cover

    def as_params(
        self,
        format: str | None = None,
        kind: str | None = None,
        marks: dict | None = None,
    ) -> list["ParameterSet"]:
        """Generate :func:`pytest.param` from specimens.

        One :func:`~.pytest.param` is generated for each specimen that matches the
        `format` and `kind` arguments (if any). Marks are attached to each param from
        `marks`, wherein the keys are partial paths.
        """
        # Transform `marks` into a platform-independent mapping from path parts
        _marks = {PurePosixPath(k).parts: v for k, v in (marks or {}).items()}

        result = []
        for path, f, k in self.specimens:
            if (format and format != f) or (kind and kind != k):
                continue
            p_rel = path.relative_to(self.base_path)

            # - Use a string ID for this specimen.
            # - Look up marks via path parts.
            result.append(
                pytest.param(path, id=str(p_rel), marks=_marks.get(p_rel.parts, ()))
            )

        return result

    def expected_data(self, path) -> "pandas.DataFrame | None":
        """Return the expected :func:`.to_pandas` result for the specimen `path`.

        Data is retrieved from :data:`.EXPECTED`.
        """
        import pandas as pd

        try:
            key = path.name.replace(".", "-")
            info = EXPECTED[key]
            if "use" in info:
                # Use the same expected data as another file
                key = info["use"]
                info = EXPECTED[key]
        except KeyError:
            return None

        args: dict[str, Any] = dict(sep=r"\s+", index_col=[0], header=[0])
        args.update(info)

        result = pd.read_csv(
            self.base_path.joinpath("expected", key).with_suffix(".txt"), **args
        )

        # A series; unwrap
        if set(result.columns) == {"value"}:
            result = result["value"]

        return result

    def fetch(self, refresh: bool = False) -> None:
        """Make :attr:`base_path` a usable checkout of sdmx-test-data.

        sdmx manages only the default location, :data:`DEFAULT_DIR`. There, the checkout
        is in one of four states, each handled without help from the caller:

        - Missing, or a Git repository too damaged to use: clone it. The old directory
          is removed only after the new clone is complete.
        - Clean: leave it alone. No network access occurs.
        - Files deleted or modified: restore them from the local repository. No network
          access occurs.
        - With `refresh`, whatever the state: fetch the latest ``main`` from
          :data:`REMOTE_URL` and check it out.

        Any other path is the user's. So is :data:`DEFAULT_DIR` if a symbolic link
        leads to it or to its checkout: a link in its path, a ``.git`` that is a link or
        file, or a ``core.worktree`` setting. Such a path is cloned into only if missing
        or empty. If it is not a clean, usable checkout, or if `refresh` is given, sdmx
        changes nothing and raises :class:`FileExistsError` (not a usable checkout) or
        :class:`RuntimeError` (local changes, or `refresh`).
        """
        import git

        # Symbolic links lead to files that may belong to the user
        git_dir = self.base_path.joinpath(".git")
        managed = (
            self.base_path == DEFAULT_DIR
            and DEFAULT_DIR.resolve() == DEFAULT_DIR.absolute()
            and not (git_dir.is_symlink() or git_dir.is_file())
        )
        self.base_path.parent.mkdir(parents=True, exist_ok=True)

        # Lock to avoid concurrency issues when running with pytest-xdist
        lock = git.BlockingLockFile(self.base_path, check_interval_s=0.1)
        lock._obtain_lock()
        try:
            if managed:
                with git.GitConfigParser(
                    [git_dir / "config", git_dir / "config.worktree"], read_only=True
                ) as config:
                    managed = not config.has_option("core", "worktree")
            if not (inspected := self._inspect()):
                self._clone(managed)
                return

            repo, dirty = inspected
            with repo:
                if not managed and (dirty or refresh):
                    raise RuntimeError(
                        f"{self.base_path} is not managed by sdmx and "
                        + ("has local changes" if dirty else "cannot be refreshed")
                        + ". Restore it yourself (for instance 'git reset --hard'), or "
                        f"use the default location {DEFAULT_DIR}"
                    )
                if refresh:
                    log.info(f"Fetch test data from {REMOTE_URL}")
                    repo.remotes["origin"].fetch("refs/heads/main", depth=1)
                    repo.git.reset("--hard", "FETCH_HEAD")
                elif dirty:
                    log.info(f"Restore files in {self.base_path}")
                    repo.git.reset("--hard", "HEAD")
        finally:
            lock._release_lock()

    def _inspect(self) -> "tuple[git.Repo, bool] | None":
        """Return the repository at :attr:`base_path` and whether it has changes.

        Return :any:`None` if the repository is not usable: any failure to read it,
        such as a missing commit or a truncated index, counts.
        """
        import git

        try:
            repo = git.Repo(self.base_path)
            repo.head.commit  # Raises ValueError if there are no commits
            # Without an index, Git cannot tell which files are changed
            dirty = not Path(repo.git_dir, "index").exists() or repo.is_dirty()
            return repo, dirty
        except Exception:
            return None

    def _clone(self, managed: bool) -> None:
        """Replace :attr:`base_path` with a new shallow clone of :data:`REMOTE_URL`.

        The clone is made next to :attr:`base_path` and moved into place only when
        complete, so a failure leaves :attr:`base_path` as it was.
        """
        import git

        path = self.base_path
        in_use = FileExistsError(
            f"{path} exists but is not a usable sdmx-test-data checkout. sdmx does "
            "not change directories it does not manage. Repair the repository, "
            "empty or remove the directory, or point --sdmx-test-data / "
            "SDMX_TEST_DATA elsewhere"
        )
        if not managed and path.exists() and any(path.iterdir()):
            raise in_use

        log.info(f"Clone test data from {REMOTE_URL} → {path}")
        staging = Path(tempfile.mkdtemp(prefix=f"{path.name}.", dir=path.parent))
        new, old = staging / "checkout", staging / "previous"
        try:
            git.Repo.clone_from(REMOTE_URL, new, depth=1, branch="main").close()
            if managed:
                if path.exists():
                    path.rename(old)
            else:
                try:
                    path.rmdir()  # Fails if something appeared during the clone
                except FileNotFoundError:
                    pass
                except OSError:
                    raise in_use from None
            new.rename(path)
        except BaseException:
            if old.exists():
                old.rename(path)
            _rmtree(staging)
            raise
        else:
            _rmtree(staging)

    def parametrize(self, metafunc) -> None:
        """Handle the ``parametrize_specimens`` mark for a specific test."""
        try:
            mark = next(metafunc.definition.iter_markers("parametrize_specimens"))
        except StopIteration:
            return

        metafunc.parametrize(mark.args[0], self.as_params(**mark.kwargs))


def add_responses(session: "Session", file_cache_path: Path, source: "Source") -> None:
    """Populate cached responses for `session`.

    Two sources are used:

    1. Responses stored in :file:`sdmx-test-data/responses/`, as indicated by
       `file_cache_path`.
    2. For the ``TEST`` source as indicated by `source`, responses generated by this
       function. These are not stored in sdmx-test-data.
    """

    # NB actually requests_cache.backend.filesystem.FileCache
    # TODO Use the requests_cache API to access/construct this class
    from requests_cache import FileCache  # type: ignore [attr-defined]

    import sdmx
    from sdmx.format import MediaType
    from sdmx.message import StructureMessage
    from sdmx.rest import Resource
    from sdmx.util.requests import save_response

    # Access the file cache in the given directory
    fc = FileCache(file_cache_path)

    # Add to `session` cache
    session.cache.update(fc)
    session.cache.recreate_keys()  # type: ignore [no-untyped-call]

    content: bytes = sdmx.to_xml(StructureMessage())
    headers = {"Content-Type": repr(MediaType("generic", "xml", "2.1"))}

    for resource in Resource:
        if resource in (Resource.data, Resource.metadata, Resource.registration):
            continue  # Not structure queries with this form of URL

        endpoint = (
            "schema/datastructure" if resource is Resource.schema else resource.value
        )
        for url in (
            f"{source.url}/{endpoint}/{source.id}/all/latest",
            f"{source.url}/{endpoint}/{source.id}/all/latest?references=children",
        ):
            save_response(
                session,
                method="GET",
                url=url,
                content=content,
                headers=headers,
            )

    for url in (
        f"{source.url}/availableconstraint",
        f"{source.url}/categoryscheme/{source.id}/all/latest?references=parentsandsiblings",
    ):
        save_response(session, method="GET", url=url, content=content, headers=headers)


def add_specimens(target: list[tuple[Path, str, str | None]], base: Path) -> None:
    """Populate the `target` collection with specimens from :file:`sdmx-test-data`."""
    # XML data files for the ECB exchange rate data flow
    for source_id in ("ECB_EXR",):
        for path in base.joinpath(source_id).rglob("*.xml"):
            kind = "data"
            if "structure" in path.name or "common" in path.name:
                kind = "structure"
            target.append((path, "xml", kind))

    # JSON data files for ECB and OECD data flows
    for source_id in ("ECB_EXR", "OECD"):
        target.extend(
            (fp, "json", "data") for fp in base.joinpath(source_id).rglob("*.json")
        )

    # Miscellaneous XML data files
    target.extend(
        (base.joinpath(*parts), "xml", "data")
        for parts in [
            ("constructed", "gh-218.xml"),
            ("INSEE", "CNA-2010-CONSO-SI-A17.xml"),
            ("INSEE", "IPI-2010-A21.xml"),
            ("IMF", "PCPS.xml"),
            ("IMF_RES", "WEO-data.xml"),  # khaeru/sdmx#266
            ("ESTAT", "demography-xs.xml"),
            ("ESTAT", "esms.xml"),
            ("ESTAT", "footer.xml"),
            ("ESTAT", "NAMA_10_GDP-ss.xml"),
        ]
    )

    # Miscellaneous XML structure files
    target.extend(
        (base.joinpath(*parts), "xml", "structure")
        for parts in [
            ("BIS", "actualconstraint-0.xml"),
            ("BIS", "hierarchicalcodelist-0.xml"),
            ("BIS", "gh-180.xml"),
            ("ECB", "orgscheme.xml"),
            ("ECB", "structureset-0.xml"),
            ("ESTAT", "apro_mk_cola-structure.xml"),
            ("ESTAT", "demography-structure.xml"),
            ("ESTAT", "esms-structure.xml"),
            ("ESTAT", "CL_FREQ.xml"),
            ("ESTAT", "GOV_10Q_GGNFA.xml"),
            ("ESTAT", "HCL_WSTATUS_SCL_BNSPART.xml"),
            ("ESTAT", "HCL_WSTATUS_SCL_WSTATUSPR.xml"),
            ("IAEG-SDGs", "metadatastructure-0.xml"),
            ("IMF", "01R.xml"),
            ("IMF", "1PI-structure.xml"),
            ("IMF", "CL_AREA-structure.xml"),
            ("IMF", "CL_FREQ-3.0-structure.xml"),
            ("IMF", "datastructure-0.xml"),
            # Manually reduced subset of the response for this DSD. Test for
            # <str:CubeRegion> containing both <com:KeyValue> and <com:Attribute>
            ("IMF", "ECOFIN_DSD-structure.xml"),
            ("IMF", "hierarchicalcodelist-0.xml"),
            ("IMF", "hierarchicalcodelist-1.xml"),
            ("IMF", "structureset-0.xml"),
            ("IMF_RES", "WEO-structure.xml"),  # khaeru/sdmx#266
            ("IMF_STA", "availableconstraint_CPI.xml"),  # khaeru/sdmx#161
            ("IMF_STA", "DSD_BOP.xml"),  # khaeru/sdmx#271
            ("IMF_STA", "DSD_GFS.xml"),  # khaeru/sdmx#164
            ("INSEE", "CNA-2010-CONSO-SI-A17-structure.xml"),
            ("INSEE", "dataflow.xml"),
            ("INSEE", "gh-205.xml"),
            ("INSEE", "IPI-2010-A21-structure.xml"),
            ("ISTAT", "22_289-structure.xml"),
            ("ISTAT", "47_850-structure.xml"),
            ("ISTAT", "actualconstraint-0.xml"),
            ("ISTAT", "metadataflow-0.xml"),
            ("ISTAT", "metadatastructure-0.xml"),
            ("OECD", "actualconstraint-0.xml"),
            ("OECD", "metadatastructure-0.xml"),
            ("UNICEF", "GLOBAL_DATAFLOW-structure.xml"),
            ("UNSD", "codelist_partial.xml"),
            ("SDMX", "HCL_TEST_AREA.xml"),
            ("SGR", "common-structure.xml"),
            ("SGR", "hierarchicalcodelist-0.xml"),
            ("SGR", "metadatastructure-0.xml"),
            ("SPC", "actualconstraint-0.xml"),
            ("SPC", "metadatastructure-0.xml"),
            ("TEST", "gh-142.xml"),
            ("TEST", "gh-149.xml"),
            ("WB", "gh-78.xml"),
        ]
    )

    # Files from the SDMX 2.1 specification
    v21 = base.joinpath("v21", "xml")
    target.extend((p, "xml", None) for p in v21.glob("**/*.xml"))

    # Files from the SDMX 3.0 specification
    v3 = base.joinpath("v3")

    # Files from the SDMX-CSV 2.0.0 specification
    target.extend((p, "csv", "data") for p in v3.joinpath("csv").glob("*.csv"))

    # commented: SDMX-JSON 2.0 is not yet implemented
    # # SDMX-JSON
    # self.specimens.extend(
    #     (p, "json", "data") for p in v3.joinpath("json", "data").glob("*.json")
    # )
    # for dir in ("metadata", "structure"):
    #     self.specimens.extend(
    #         (p, "json", "structure")
    #         for p in v3.joinpath("json", dir).glob("*.json")
    #     )

    # SDMX-ML
    target.extend((p, "xml", None) for p in v3.glob("xml/*.xml"))

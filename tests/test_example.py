"""example(): download a ready-to-run example project from useful-views-data.

Tests read tests/data instead of GitHub (the offline_examples fixture), which
holds each example exactly as the data repository publishes it.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys

import pytest
from pathlib import Path

import useful_views as uvs
from useful_views.examples import DATA_URL_ENV, EXAMPLES

from conftest import DATA, REFERENCE, generated

PUBLISHED = sorted(EXAMPLES["pwt"]["files"])


def test_manifest_matches_the_published_files():
    """The checksums in the package are those of the files in tests/data, and
    every file of the example (apart from the outputs it produces) is listed."""
    on_disk = sorted(
        p.relative_to(REFERENCE).as_posix()
        for p in REFERENCE.rglob("*")
        if p.is_file() and p.relative_to(REFERENCE).parts[0] not in ("useful-views",) and p.name not in ("datapackage.json", "cookbook.json")
    )
    assert on_disk == PUBLISHED
    for rel, digest in EXAMPLES["pwt"]["files"].items():
        assert hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest() == digest, rel


def test_example_creates_a_ready_project(tmp_path):
    root = uvs.example("pwt", tmp_path / "reference")
    assert root == (tmp_path / "reference").resolve()
    for rel in PUBLISHED:
        assert (root / rel).read_bytes() == (REFERENCE / rel).read_bytes()
    for rel in (".gitignore", "datapackage.json", "useful-views/README.md", "raw/.gitkeep"):
        assert (root / rel).is_file()
    assert [f.kind for f in uvs.check(root=root)] == ["missing-input", "never-run", "never-run"]
    result = uvs.sync(root=root)
    assert result.ran == ("pwt_panel", "gdp_per_capita")
    # Named "reference", the project reproduces the golden outputs exactly.
    assert generated(root) == (
        (REFERENCE / "datapackage.json").read_bytes(),
        (REFERENCE / "useful-views" / "README.md").read_bytes(),
    )


def test_default_folder_is_name_example(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert uvs.example("pwt") == (tmp_path / "pwt-example").resolve()


def test_unknown_example_lists_the_available_ones(tmp_path):
    with pytest.raises(uvs.ExampleError, match="pwt"):
        uvs.example("nope", tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_existing_non_empty_folder_is_refused(tmp_path):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "notes.txt").write_text("mine")
    with pytest.raises(uvs.ExampleError, match="not an empty folder"):
        uvs.example("pwt", tmp_path / "taken")
    assert [p.name for p in (tmp_path / "taken").iterdir()] == ["notes.txt"]


def test_empty_existing_folder_is_used(tmp_path):
    (tmp_path / "empty").mkdir()
    assert (uvs.example("pwt", tmp_path / "empty") / "README.md").is_file()


def test_example_inside_a_project_is_refused(project):
    with pytest.raises(uvs.ProjectError, match="cannot be nested"):
        uvs.example("pwt", project / "notebooks" / "pwt")
    assert not (project / "notebooks").exists()


def test_a_changed_file_is_rejected_and_nothing_is_written(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    shutil.copytree(DATA, mirror)
    with open(mirror / "examples" / "pwt" / "recipes" / "pwt_panel" / "recipe.toml", "a") as f:
        f.write("# tampered\n")
    monkeypatch.setenv(DATA_URL_ENV, mirror.as_uri())
    with pytest.raises(uvs.ExampleError, match="checksum"):
        uvs.example("pwt", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_failed_download_says_so_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_URL_ENV, (tmp_path / "nowhere").as_uri())
    with pytest.raises(uvs.ExampleError, match="Could not read"):
        uvs.example("pwt", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_example_on_the_command_line(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "useful_views", "example", "pwt", "demo"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert 'useful-views -C "' in proc.stdout and proc.stdout.rstrip().endswith('" sync')
    assert (tmp_path / "demo" / "raw" / "pwt110_bra_deu_esp_usa.csv").is_file()
    proc = subprocess.run([sys.executable, "-m", "useful_views", "example", "nope"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 3 and "pwt" in proc.stderr


# --- what a failed download says ----------------------------------------------------

from useful_views import examples as examples_module  # noqa: E402


def test_a_mirror_can_be_a_plain_folder_path(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_URL_ENV, str(DATA))
    assert (uvs.example("pwt", tmp_path / "out") / "README.md").is_file()


def test_a_missing_mirror_names_the_variable(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_URL_ENV, str(tmp_path / "nowhere"))
    with pytest.raises(uvs.ExampleError) as err:
        uvs.example("pwt", tmp_path / "out")
    assert err.value.kind == "not-found" and err.value.to_dict()["kind"] == "not-found"
    assert f"{DATA_URL_ENV} is set to" in str(err.value) and "internet" not in str(err.value)


def test_a_tampered_mirror_is_explained(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    shutil.copytree(DATA, mirror)
    (mirror / "examples" / "pwt" / "README.md").write_bytes(b"changed\r\n")
    monkeypatch.setenv(DATA_URL_ENV, mirror.as_uri())
    with pytest.raises(uvs.ExampleError) as err:
        uvs.example("pwt", tmp_path / "out")
    assert err.value.kind == "checksum" and "line endings" in str(err.value)


@pytest.mark.parametrize("exc, kind", [
    (__import__("http.client").client.IncompleteRead(b"partial"), "network"),
    (__import__("urllib.error").error.URLError(__import__("ssl").SSLCertVerificationError(
        "certificate verify failed: CERTIFICATE_VERIFY_FAILED")), "tls"),
    (__import__("urllib.error").error.URLError("Name or service not known"), "network"),
    (TimeoutError("timed out"), "network"),
])
def test_download_failures_are_example_errors(tmp_path, monkeypatch, exc, kind):
    monkeypatch.delenv(DATA_URL_ENV)

    def fail(url, timeout):
        raise exc

    monkeypatch.setattr(examples_module.urllib.request, "urlopen", fail)
    with pytest.raises(uvs.ExampleError) as err:
        uvs.example("pwt", tmp_path / "out")
    assert err.value.kind == kind
    if kind == "tls":
        assert "SSL_CERT_FILE" in str(err.value)
    else:
        assert "HTTPS_PROXY" in str(err.value)
    assert not (tmp_path / "out").exists()


def test_credentials_in_a_mirror_url_are_refused_and_never_shown(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_URL_ENV, "https://user:s3cret@mirror.example.invalid/data")
    with pytest.raises(uvs.ExampleError) as err:  # the real urlopen: it never gets that far
        uvs.example("pwt", tmp_path / "out")
    assert "s3cret" not in str(err.value) and "user name or password" in str(err.value)
    assert not (tmp_path / "out").exists()


def test_a_password_in_an_error_from_urllib_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_URL_ENV)

    def fail(url, timeout):
        raise examples_module.http.client.InvalidURL("nonnumeric port: 's3cret@host'")

    monkeypatch.setattr(examples_module.urllib.request, "urlopen", fail)
    with pytest.raises(uvs.ExampleError) as err:
        uvs.example("pwt", tmp_path / "out")
    assert "s3cret" not in str(err.value)


@pytest.mark.parametrize("form", ["file", "file1", "path"])
def test_every_way_of_naming_a_local_mirror_works(tmp_path, monkeypatch, form):
    value = {"file": DATA.as_uri(), "file1": "file:" + DATA.as_posix(), "path": str(DATA)}[form]
    monkeypatch.setenv(DATA_URL_ENV, value)
    assert (uvs.example("pwt", tmp_path / "out") / "README.md").is_file()


def test_example_refuses_the_home_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("x")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with pytest.raises(uvs.ExampleError, match="is your home folder"):
        uvs.example("pwt", home)
    assert sorted(p.name for p in home.iterdir()) == [".zshrc"]


def test_a_folder_holding_only_hidden_files_counts_as_empty(tmp_path):
    folder = tmp_path / "looks-empty"
    folder.mkdir()
    (folder / ".DS_Store").write_bytes(b"\0")
    (folder / "Thumbs.db").write_bytes(b"\0")
    assert (uvs.example("pwt", folder) / "README.md").is_file()

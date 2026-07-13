from pathlib import Path

from llm_bench import __version__


def test_package_version_is_stable_release():
    assert __version__ == "1.0.3"


def test_release_targets_only_current_version_artifacts():
    makefile = Path("Makefile").read_text()

    assert (
        'VERSION := $(shell python3 -c "from llm_bench import __version__; print(__version__)")'
        in makefile
    )
    assert "DIST_FILES := dist/llm_speed_bench-$(VERSION)*" in makefile
    assert "python3 -m twine check $(DIST_FILES)" in makefile
    assert "python3 -m twine upload --repository testpypi $(DIST_FILES)" in makefile


def test_source_distribution_manifest_excludes_internal_repository_material():
    manifest = Path("MANIFEST.in").read_text()

    for exclusion in (
        "exclude AGENTS.md",
        "exclude CONTRIBUTING.md",
        "exclude LAUNCH.md",
        "exclude Makefile",
        "exclude RELEASING.md",
        "exclude SECURITY.md",
        "prune docs",
        "prune .github",
        "prune tests",
    ):
        assert exclusion in manifest

    for internal_path in (
        "AGENTS.md",
        "CONTRIBUTING.md",
        "LAUNCH.md",
        "Makefile",
        "RELEASING.md",
        "SECURITY.md",
        "docs",
    ):
        assert f"include {internal_path}" not in manifest


def test_pypi_trusted_publisher_isolated_to_release_workflow():
    workflow = Path(".github/workflows/release.yml").read_text()

    assert "types: [published]" in workflow
    assert "id-token: write" in workflow
    assert "actions/upload-artifact" in workflow
    assert "actions/download-artifact" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "PYPI_API_TOKEN" not in workflow


def test_testpypi_workflow_uses_oidc_and_verifies_the_published_package():
    workflow = Path(".github/workflows/testpypi.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "environment:" in workflow
    assert "name: testpypi" in workflow
    assert "id-token: write" in workflow
    assert "repository-url: https://test.pypi.org/legacy/" in workflow
    assert "llm-speed-bench==${VERSION}" in workflow
    assert (
        'llm-bench --quick "Reply with ok." --models mock:local --no-save' in workflow
    )
    assert "TWINE_PASSWORD" not in workflow
    assert "PYPI_API_TOKEN" not in workflow

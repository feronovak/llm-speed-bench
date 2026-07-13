from pathlib import Path

from llm_bench import __version__


def test_package_version_is_stable_release():
    assert __version__ == "1.0.2"


def test_release_targets_only_current_version_artifacts():
    makefile = Path("Makefile").read_text()

    assert (
        'VERSION := $(shell python3 -c "from llm_bench import __version__; print(__version__)")'
        in makefile
    )
    assert "DIST_FILES := dist/llm_speed_bench-$(VERSION)*" in makefile
    assert "python3 -m twine check $(DIST_FILES)" in makefile
    assert "python3 -m twine upload --repository testpypi $(DIST_FILES)" in makefile


def test_pypi_trusted_publisher_isolated_to_release_workflow():
    workflow = Path(".github/workflows/release.yml").read_text()

    assert "types: [published]" in workflow
    assert "id-token: write" in workflow
    assert "actions/upload-artifact" in workflow
    assert "actions/download-artifact" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "PYPI_API_TOKEN" not in workflow

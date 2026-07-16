import json
import re
from pathlib import Path

from llm_bench import __version__
from llm_bench import cli
from llm_bench.runner import run_benchmark


def test_package_version_is_stable_release():
    assert __version__ == "2.0.0"


def test_preflight_is_the_primary_command_and_bench_remains_compatible(monkeypatch):
    monkeypatch.setattr("sys.argv", ["llm-preflight"])
    assert cli._display_command() == "llm-preflight"

    pyproject = Path("pyproject.toml").read_text()
    assert 'name = "llm-preflight"' in pyproject
    assert 'llm-preflight = "llm_bench.cli:main"' in pyproject
    assert 'llm-bench = "llm_bench.cli:main"' in pyproject


def test_legacy_pypi_distribution_is_a_dependency_only_compatibility_shim():
    shim = Path("legacy-pypi-shim/pyproject.toml").read_text()

    assert 'name = "llm-speed-bench"' in shim
    assert "llm-preflight>=2.0.0,<3.0.0" in shim
    assert 'llm-bench = "llm_bench.cli:main"' in shim
    assert not (Path("legacy-pypi-shim") / "llm_bench").exists()


def test_legacy_shim_can_only_be_published_by_explicit_manual_confirmation():
    workflow = Path(".github/workflows/publish-legacy-shim.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert 'default: ""' in workflow
    assert "inputs.confirm == 'PUBLISH-LEGACY-SHIM'" in workflow
    assert "legacy-pypi-shim" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow


def test_build_backend_supports_editable_installs_without_an_unnecessary_floor():
    pyproject = Path("pyproject.toml").read_text()

    assert 'requires = ["setuptools>=64"]' in pyproject
    assert 'build-backend = "setuptools.build_meta"' in pyproject


def test_legacy_installer_has_explicit_package_metadata_fallback():
    setup = Path("setup.py").read_text()

    assert 'name="llm-preflight"' in setup
    assert 'version="2.0.0"' in setup
    assert '"llm-preflight=llm_bench.cli:main"' in setup
    assert '"llm-bench=llm_bench.cli:main"' in setup


def test_example_does_not_present_unknown_model_pricing_as_free():
    example = Path("benchmark.example.json").read_text()

    assert '"input_cost_per_million": 0' not in example
    assert '"output_cost_per_million": 0' not in example


def test_custom_contract_examples_are_parseable_and_documented():
    examples = (
        "examples/custom-contracts/ticket-extraction.json",
        "examples/custom-contracts/intent-routing.json",
        "examples/custom-contracts/content-rule.json",
    )

    for example in examples:
        config = json.loads(Path(example).read_text())
        assert config["prompts"]
        assert config["models"][0]["provider"] == "mock"
        assert (
            run_benchmark(config)["models"][0]["profiles"][0]["summary"]["failed"] == 0
        )

    tutorial = Path("docs/custom-tests.md").read_text()
    for example in examples:
        assert example in tutorial


def test_public_docs_make_the_catalog_lifecycle_primary():
    cli_reference = Path("docs/cli-reference.md").read_text()
    tutorial = Path("docs/model-watch.md").read_text()
    changelog = Path("CHANGELOG.md").read_text()

    for command in (
        "catalog init",
        "catalog refresh",
        "catalog prepare",
        "catalog test",
        "models approve",
        "models remove",
        "--approve-to",
    ):
        assert command in cli_reference
        assert command in tutorial
    assert "compatibility aliases" in cli_reference
    assert "compatibility aliases" in tutorial
    assert "catalog init" in changelog
    assert "catalog prepare" in Path("README.md").read_text()
    assert "catalog prepare" in Path("docs/workflows.md").read_text()
    assert "--approve-to" in Path("docs/interactive.md").read_text()
    assert "--migration-check" in Path("README.md").read_text()
    assert "--migration-check" in Path("docs/getting-started.md").read_text()
    assert "--migration-check" in Path("docs/cli-reference.md").read_text()
    assert Path("docs/troubleshooting.md").exists()


def test_public_docs_give_beginner_and_scripted_users_clear_starting_paths():
    readme = Path("README.md").read_text()
    getting_started = Path("docs/getting-started.md").read_text()

    assert "## Choose your path" in readme
    for destination in (
        "docs/getting-started.md",
        "docs/custom-tests.md",
        "docs/model-watch.md",
        "docs/ci.md",
    ):
        assert destination in readme
    assert "## Choose your next path" in getting_started
    assert "custom contract test" in getting_started
    assert "CI and JSON output" in getting_started


def test_public_markdown_links_resolve_locally():
    markdown_files = (Path("README.md"), *Path("docs").glob("*.md"))
    link_pattern = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")

    for document in markdown_files:
        for target in link_pattern.findall(document.read_text()):
            path = target.split("#", 1)[0]
            if not path or "://" in path or path.startswith("mailto:"):
                continue
            assert (document.parent / path).resolve().is_file(), (
                f"{document}: missing local documentation target {target}"
            )


def test_release_targets_only_current_version_artifacts():
    makefile = Path("Makefile").read_text()

    assert (
        'VERSION := $(shell python3 -c "from llm_bench import __version__; print(__version__)")'
        in makefile
    )
    assert "DIST_FILES := dist/llm_preflight-$(VERSION)*" in makefile
    assert "python3 -m twine check $(DIST_FILES)" in makefile
    assert "python3 -m twine upload --repository testpypi $(DIST_FILES)" in makefile


def test_source_distribution_manifest_keeps_only_public_release_material():
    manifest = Path("MANIFEST.in").read_text()

    for included in (
        "include CHANGELOG.md",
        "include LICENSE",
        "include README.md",
        "include SECURITY.md",
        "recursive-include examples *.json",
    ):
        assert included in manifest

    for internal_path in (
        "AGENTS.md",
        "CONTRIBUTING.md",
        "LAUNCH.md",
        "Makefile",
        "RELEASING.md",
        "docs",
    ):
        assert f"include {internal_path}" not in manifest

    for excluded in ("exclude AGENTS.md", "exclude CONTRIBUTING.md", "prune docs"):
        assert excluded in manifest
    assert "prune legacy-pypi-shim" in manifest


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
    assert "llm-preflight==${VERSION}" in workflow
    assert (
        'llm-preflight --quick "Reply with ok." --models mock:local --no-save'
        in workflow
    )
    assert "TWINE_PASSWORD" not in workflow
    assert "PYPI_API_TOKEN" not in workflow

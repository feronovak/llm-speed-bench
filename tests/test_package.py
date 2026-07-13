from llm_bench import __version__


def test_package_version_is_stable_release():
    assert __version__ == "1.0.1"

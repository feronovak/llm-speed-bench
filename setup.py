"""Compatibility metadata for installers with an older setuptools backend."""

from setuptools import find_packages, setup


setup(
    name="llm-preflight",
    version="2.0.0",
    description="Local, cross-provider preflight checks for an LLM model switch",
    packages=find_packages(include=["llm_bench", "llm_bench.*"]),
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "llm-preflight=llm_bench.cli:main",
            "llm-bench=llm_bench.cli:main",
        ]
    },
)

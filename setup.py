"""Compatibility metadata for installers with an older setuptools backend."""

from setuptools import find_packages, setup


setup(
    name="llm-speed-bench",
    version="1.2.2",
    description="Local smoke tests for live LLM models, latency, and cost",
    packages=find_packages(include=["llm_bench", "llm_bench.*"]),
    python_requires=">=3.10",
    entry_points={"console_scripts": ["llm-bench=llm_bench.cli:main"]},
)

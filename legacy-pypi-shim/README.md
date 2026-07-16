# llm-speed-bench compatibility shim

LLM Speed Bench was renamed to [LLM Preflight](https://pypi.org/project/llm-preflight/).

Install `llm-preflight` directly for new projects. This package exists only so
existing installations can transition without a broken dependency. It contains
no benchmark code; it installs `llm-preflight` and preserves the `llm-bench`
command.

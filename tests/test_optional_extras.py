"""Optional-extra modules must import cleanly when the extra is NOT installed.

Regression: ``kalyx_sdk.langchain`` referenced ``Document`` /
``CallbackManagerForRetrieverRun`` in runtime-evaluated annotations, but those
names are only bound inside the ``try:`` import block — so with
``langchain-core`` absent, ``import kalyx_sdk.langchain`` died with
``NameError: name 'Document' is not defined`` instead of the documented
helpful ``ImportError`` on use.

Each test poisons ``sys.modules`` in a subprocess so the real (installed)
extra cannot leak in, then imports the module and exercises the fallback.
"""

from __future__ import annotations

import subprocess
import sys

_LANGCHAIN_NAMES = (
    "langchain_core",
    "langchain_core.callbacks",
    "langchain_core.documents",
    "langchain_core.retrievers",
    "langchain_core.tools",
)

_LLAMAINDEX_NAMES = (
    "llama_index",
    "llama_index.core",
    "llama_index.core.base",
    "llama_index.core.base.base_retriever",
    "llama_index.core.schema",
    "llama_index.core.tools",
    "llama_index.core.tools.tool_spec",
    "llama_index.core.tools.tool_spec.base",
)


def _run_without_extra(names: tuple[str, ...], body: str) -> subprocess.CompletedProcess[str]:
    poison = "".join(f"sys.modules[{name!r}] = None\n" for name in names)
    # S603: the command is a fixed in-repo string (no untrusted input).
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import sys\n{poison}{body}"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_langchain_module_imports_without_extra() -> None:
    proc = _run_without_extra(
        _LANGCHAIN_NAMES,
        "import kalyx_sdk.langchain as m\n"
        "assert m._HAVE_LANGCHAIN is False\n"
        "assert 'KalyxRetriever' in m.__all__\n",
    )
    assert proc.returncode == 0, f"import failed without langchain-core:\n{proc.stderr}"


def test_langchain_retriever_raises_helpful_importerror_without_extra() -> None:
    proc = _run_without_extra(
        _LANGCHAIN_NAMES,
        "import kalyx_sdk.langchain as m\n"
        "try:\n"
        "    m.KalyxRetriever()\n"
        "except ImportError as exc:\n"
        "    assert 'langchain' in str(exc).lower(), str(exc)\n"
        "else:\n"
        "    raise SystemExit('expected ImportError, got a constructed retriever')\n",
    )
    assert proc.returncode == 0, proc.stderr


def test_llamaindex_module_imports_without_extra() -> None:
    proc = _run_without_extra(
        _LLAMAINDEX_NAMES,
        "import kalyx_sdk.llamaindex as m\nassert m._HAVE_LLAMAINDEX is False\n",
    )
    assert proc.returncode == 0, f"import failed without llama-index-core:\n{proc.stderr}"


def test_llamaindex_retriever_raises_helpful_importerror_without_extra() -> None:
    proc = _run_without_extra(
        _LLAMAINDEX_NAMES,
        "import kalyx_sdk.llamaindex as m\n"
        "try:\n"
        "    m.KalyxLlamaIndexRetriever()\n"
        "except ImportError as exc:\n"
        "    assert 'llamaindex' in str(exc).lower(), str(exc)\n"
        "else:\n"
        "    raise SystemExit('expected ImportError, got a constructed retriever')\n",
    )
    assert proc.returncode == 0, proc.stderr

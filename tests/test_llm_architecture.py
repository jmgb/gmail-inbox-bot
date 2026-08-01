"""Architecture gate for provider-neutral chat callers."""

import ast
from pathlib import Path

PROVIDER_SDKS = {"anthropic", "google.generativeai", "google.genai", "groq", "openai"}


def _is_provider_sdk(module: str) -> bool:
    return any(module == sdk or module.startswith(f"{sdk}.") for sdk in PROVIDER_SDKS)


def _provider_imports(path: Path) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if _is_provider_sdk(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _is_provider_sdk(node.module):
                imports.append(node.module)
                continue
            imports.extend(
                qualified
                for alias in node.names
                if _is_provider_sdk(qualified := f"{node.module}.{alias.name}")
            )
    return imports


def test_provider_import_detector_rejects_sdk_submodules(tmp_path):
    caller = tmp_path / "caller.py"
    caller.write_text(
        "from openai.resources import Responses\n"
        "from google.genai.errors import APIError\n"
        "from google import genai\n"
        "import groq.resources\n",
        encoding="utf-8",
    )

    assert _provider_imports(caller) == [
        "openai.resources",
        "google.genai.errors",
        "google.genai",
        "groq.resources",
    ]


def test_application_code_does_not_import_provider_sdks_directly():
    violations = {
        str(path): imports
        for path in Path("gmail_inbox_bot").rglob("*.py")
        if (imports := _provider_imports(path))
    }

    assert violations == {}, (
        "Chat/JSON callers must depend on neutral-llm-gateway; "
        f"direct provider SDK imports found: {violations}"
    )

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app  # noqa: E402


def main():
    original_load_env_file = app._load_env_file
    try:
        # Keep the test isolated from the real .env so we can prove
        # whether runtime reload updates the in-memory LLM settings.
        app._load_env_file = lambda overwrite=False: None

        os.environ["DEEPSEEK_API_KEY"] = "probe-key"
        os.environ["DEEPSEEK_BASE_URL"] = "https://probe.example"
        os.environ["DEEPSEEK_MODEL"] = "probe-model"

        app._reload_runtime_env()

        assert app.DEEPSEEK_API_KEY == "probe-key", (
            f"expected runtime reload to refresh DEEPSEEK_API_KEY, got {app.DEEPSEEK_API_KEY!r}"
        )
        assert app.DEEPSEEK_BASE_URL == "https://probe.example", (
            f"expected runtime reload to refresh DEEPSEEK_BASE_URL, got {app.DEEPSEEK_BASE_URL!r}"
        )
        assert app.DEEPSEEK_MODEL == "probe-model", (
            f"expected runtime reload to refresh DEEPSEEK_MODEL, got {app.DEEPSEEK_MODEL!r}"
        )

        print("PASS: runtime reload updates LLM globals")
    finally:
        app._load_env_file = original_load_env_file


if __name__ == "__main__":
    main()

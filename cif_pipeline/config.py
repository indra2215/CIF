"""
Centralized config loading from .env file.
All external credentials and model paths are read here once,
so no module needs to know about dotenv or environment variables directly.
"""

from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (two levels up from this file, or cwd)
_project_root = Path(__file__).resolve().parent.parent
_env_path = _project_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()  # fallback: look in cwd


def get_mp_api_key() -> str:
    key = os.getenv("MP_API_KEY", "")
    if not key:
        import logging
        logging.getLogger("cif_pipeline.config").warning(
            "MP_API_KEY not set in .env — Materials Project searches will return no results"
        )
    return key


def get_mace_model() -> str:
    return os.getenv("MACE_MODEL", "medium")


def get_mace_device() -> str:
    return os.getenv("MACE_DEVICE", "cpu")


def get_crystallm_checkpoint() -> str:
    return os.getenv("CRYSTALLM_CHECKPOINT_PATH", "")

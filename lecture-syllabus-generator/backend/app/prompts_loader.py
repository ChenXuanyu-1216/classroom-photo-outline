import yaml
from pathlib import Path
from app.config import PROMPTS_DIR


_prompts_cache: dict[str, dict] = {}


def load_prompt(name: str) -> dict:
    if name in _prompts_cache:
        return _prompts_cache[name]

    file_path = PROMPTS_DIR / f"{name}.yaml"
    if not file_path.exists():
        return {"version": "unknown", "prompt": ""}

    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    _prompts_cache[name] = data
    return data


def get_prompt_version(name: str) -> str:
    data = load_prompt(name)
    return data.get("version", "unknown")


def get_prompt_text(name: str) -> str:
    data = load_prompt(name)
    return data.get("prompt", "")
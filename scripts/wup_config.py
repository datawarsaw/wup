"""Small explicit WUP configuration model.

Configuration is optional. Credentials remain outside Git.
"""
from __future__ import annotations

import copy
import os
import ast
from pathlib import Path
from typing import Any, Mapping

DEFAULT_CONFIG: dict[str, Any] = {
    "tools": {"enabled": []},
    "local": {"state_dir": "", "workstation_ops_path": ""},
}


def default_config_path() -> Path:
    return Path(os.environ.get("WUP_CONFIG", Path.cwd() / "wup.toml"))


def load_config(path: Path | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    path = path or default_config_path()
    if not path.exists():
        return config
    value: dict[str, Any] = {}
    section: dict[str, Any] = value
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line: continue
        if line.startswith("[") and line.endswith("]"):
            section = value
            for name in line[1:-1].split("."):
                section = section.setdefault(name.strip(), {})
            continue
        if "=" not in line: raise ValueError("unsupported WUP TOML syntax")
        key, raw_value = (part.strip() for part in line.split("=", 1))
        normalized = raw_value.replace("true", "True").replace("false", "False")
        try: section[key] = ast.literal_eval(normalized)
        except (SyntaxError, ValueError) as exc: raise ValueError("unsupported WUP TOML value") from exc
    for section in ("tools", "local"):
        if isinstance(value.get(section), Mapping):
            config[section].update(value[section])
    if not isinstance(config["tools"].get("enabled"), list) or not all(isinstance(item, str) for item in config["tools"]["enabled"]):
        raise ValueError("tools.enabled must be an array of tool names")
    return config


def apply_runtime_config(config: Mapping[str, Any]) -> None:
    os.environ["WUP_ENABLED_TOOLS"] = ",".join(config["tools"].get("enabled", []))
    os.environ["WUP_WORKSTATION_OPS_PATH"] = str(config["local"].get("workstation_ops_path") or "")

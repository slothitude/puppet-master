"""Configuration — DB path, defaults, puppet loading from YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PuppetConfig:
    name: str = ""
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen3.5:4b"
    max_concurrent: int = 1
    think: bool = False
    max_turns: int = 20
    max_tokens: int = 50000

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ollama_url": self.ollama_url,
            "model": self.model,
            "max_concurrent": self.max_concurrent,
            "think": self.think,
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PuppetConfig:
        return cls(
            name=d.get("name", ""),
            ollama_url=d.get("ollama_url", "http://localhost:11434"),
            model=d.get("model", "qwen3.5:4b"),
            max_concurrent=d.get("max_concurrent", 1),
            think=d.get("think", False),
            max_turns=d.get("default_budget", {}).get("max_turns", d.get("max_turns", 20)),
            max_tokens=d.get("default_budget", {}).get("max_tokens", d.get("max_tokens", 50000)),
        )


# Singleton config
_CONFIG_DIR = Path(__file__).parent
DB_PATH = str(_CONFIG_DIR / "data" / "mandate_graph.db")
PUPPETS_PATH = str(_CONFIG_DIR / "puppets.yaml")

_puppets: dict[str, PuppetConfig] | None = None


def load_puppets(path: str = PUPPETS_PATH) -> dict[str, PuppetConfig]:
    """Load puppet configurations from YAML file."""
    global _puppets
    if _puppets is not None:
        return _puppets
    if not os.path.exists(path):
        _puppets = {}
        return _puppets
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data or "puppets" not in data:
        _puppets = {}
        return _puppets
    _puppets = {}
    for key, val in data["puppets"].items():
        _puppets[key] = PuppetConfig.from_dict(val)
    return _puppets


def get_puppet(name: str) -> PuppetConfig | None:
    """Get a puppet config by name."""
    puppets = load_puppets()
    return puppets.get(name)


def reload_puppets(path: str = PUPPETS_PATH) -> None:
    """Force reload puppets from disk."""
    global _puppets
    _puppets = None
    load_puppets(path)

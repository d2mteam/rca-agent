from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HITL_")
    data_dir: Path = ROOT / ".hitl"
    root: Path = ROOT
    host: str = "127.0.0.1"
    port: int = 8765
    compose_timeout: int = 180
    probe_ready_timeout: float = 20

    def prepare(self):
        for folder in ("projects", "plans", "locks", "assets"):
            (self.data_dir / folder).mkdir(parents=True, exist_ok=True)
        return self

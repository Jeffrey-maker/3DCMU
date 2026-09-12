from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env", extra="ignore"
    )

    data_dir: Path = Path(__file__).resolve().parent.parent / "data" / "floorplans"
    raster_dpi: int = 150

    enable_k2_horizon: bool = False
    k2_horizon_endpoint: str = ""
    k2_horizon_api_key: str = ""
    k2_horizon_model: str = "k2-horizon"

    # Gemini traces passage lines on explicit request; geometry builds the graph.
    enable_gemini_vision: bool = False
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_thinking_level: str = "low"

    # Spaces the inventory classifies as rooms but people actually walk
    # through, so a route may cross them and the rooms opening off them stay
    # reachable. Matched on the space-type report's printed category name.
    walkable_space_labels: list[str] = ["Open Stack Study"]

    stair_edge_weight: float = 250.0
    elevator_edge_weight: float = 400.0


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)

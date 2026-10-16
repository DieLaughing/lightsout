"""One module per data source. Each exposes `async def fetch(series_id, seasons) -> AdapterResult`."""
import importlib


def load(name: str):
    return importlib.import_module(f"lightsout.adapters.{name}")


def has_standings(series_id: str, adapter: str) -> bool:
    """The adapter can read standings or results for this series (NO_STANDINGS lists series it can't)."""
    module = load(adapter)
    return hasattr(module, "standings") and series_id not in getattr(module, "NO_STANDINGS", ())

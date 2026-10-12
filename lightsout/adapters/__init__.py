"""One module per data source. Each exposes `async def fetch(series_id, seasons) -> AdapterResult`."""
import importlib


def load(name: str):
    return importlib.import_module(f"lightsout.adapters.{name}")

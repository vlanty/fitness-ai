"""Registry of available data sources. Order of DATA_SOURCES = priority."""

from .base import Batch, PartialFetch, RawFile, Source
from .garmin import GarminSource

REGISTRY: dict[str, type[Source]] = {
    GarminSource.name: GarminSource,
}

__all__ = ["REGISTRY", "Batch", "PartialFetch", "RawFile", "Source"]

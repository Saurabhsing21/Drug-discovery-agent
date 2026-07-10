from .base import CollectorConnector
from .depmap import DepMapConnector
from .literature import LiteratureConnector
from .opentargets import OpenTargetsConnector
from .pharos import PharosConnector


def get_default_connectors() -> dict[str, CollectorConnector]:
    return {
        "depmap": DepMapConnector(),
        "pharos": PharosConnector(),
        "opentargets": OpenTargetsConnector(),
        "literature": LiteratureConnector(),
    }

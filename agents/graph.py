from __future__ import annotations
import sys
import importlib
sys.modules[__name__] = importlib.import_module('drugagent.pipeline.graph')

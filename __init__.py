"""QGIS plugin entry point for PlanX MultiMap plugin."""
from .main_plugin import PlanXMultiMapPlugin


def classFactory(iface):
    return PlanXMultiMapPlugin(iface)

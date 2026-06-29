"""QGIS plugin entry point for 02-Multimap: Sync-up Multimaps plugin."""
from .main_plugin import O2MultiMapPlugin


def classFactory(iface):
    return O2MultiMapPlugin(iface)

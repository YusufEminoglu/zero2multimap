"""QGIS plugin entry point for 02Multimap: Sync-up Map Layers plugin."""
from .main_plugin import O2MultiMapPlugin


def classFactory(iface):
    return O2MultiMapPlugin(iface)

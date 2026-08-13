"""QGIS plugin entry point for 02Multimap: Sync-up Map Layers plugin."""


def classFactory(iface):
    from .main_plugin import O2MultiMapPlugin
    return O2MultiMapPlugin(iface)


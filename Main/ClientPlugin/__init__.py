"""Plugins for hosted bots and userbots powered by raw MTProto updates."""

from .loader import attach_client_plugins, load_client_plugins

__all__ = ["attach_client_plugins", "load_client_plugins"]

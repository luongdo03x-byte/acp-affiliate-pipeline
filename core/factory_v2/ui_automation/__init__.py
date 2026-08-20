"""Safe UI automation primitives for REMOTE_AVD workers."""

from .adb import AdbClient
from .hierarchy import UiBounds, UiHierarchyReader, UiNode, UiSnapshot

__all__ = ["AdbClient", "UiBounds", "UiHierarchyReader", "UiNode", "UiSnapshot"]

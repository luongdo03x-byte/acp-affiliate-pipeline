"""Deterministic selector matching for sanitized UI snapshots."""
from __future__ import annotations

from dataclasses import dataclass

from .hierarchy import UiNode, UiSnapshot


def normalize_ui_text(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


@dataclass(frozen=True)
class Selector:
    semantic: str | None = None
    resource_ids: tuple[str, ...] = ()
    content_descs: tuple[str, ...] = ()
    texts: tuple[str, ...] = ()
    class_names: tuple[str, ...] = ()
    require_clickable: bool = False
    text_contains_all: tuple[str, ...] = ()
    require_enabled: bool = True

    def _eligible(self, node: UiNode) -> bool:
        if self.require_enabled and not node.enabled:
            return False
        return not self.require_clickable or node.clickable

    def find(self, snapshot: UiSnapshot) -> UiNode | None:
        nodes = tuple(node for node in snapshot.nodes if self._eligible(node))
        for expected in self.resource_ids:
            for node in nodes:
                if node.resource_id == expected:
                    return node
        for expected in (normalize_ui_text(value) for value in self.content_descs):
            for node in nodes:
                if normalize_ui_text(node.content_desc) == expected:
                    return node
        for expected in self.texts:
            for node in nodes:
                if node.text == expected:
                    return node
        for expected in (normalize_ui_text(value) for value in self.texts):
            for node in nodes:
                if normalize_ui_text(node.text) == expected:
                    return node
        contains = tuple(
            normalize_ui_text(value)
            for value in self.text_contains_all
            if normalize_ui_text(value)
        )
        if contains:
            for node in nodes:
                actual = normalize_ui_text(node.text)
                if all(expected in actual for expected in contains):
                    return node
        for expected in self.class_names:
            for node in nodes:
                if node.class_name == expected:
                    return node
        return None

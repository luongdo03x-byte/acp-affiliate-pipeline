"""Safety-first screen signature evaluation."""
from __future__ import annotations

from dataclasses import dataclass

from .hierarchy import UiSnapshot
from .selectors import Selector


@dataclass(frozen=True)
class DetectedScreen:
    kind: str
    confidence: float
    evidence: tuple[str, ...]
    protected: bool = False

    @property
    def automation_allowed(self) -> bool:
        return not self.protected and self.kind != "UNKNOWN" and self.confidence >= 0.90


@dataclass(frozen=True)
class ScreenSignature:
    kind: str
    package: str
    selectors: tuple[Selector, ...]
    minimum_matches: int
    confidence: float
    protected: bool = False
    priority: int = 100

    def matches_package(self, package: str | None) -> bool:
        return self.package in {"", "*"} or package == self.package


class ScreenDetector:
    def __init__(self, signatures):
        self.signatures = tuple(sorted(tuple(signatures), key=lambda item: item.priority))

    def detect(self, snapshot: UiSnapshot) -> DetectedScreen:
        for signature in self.signatures:
            if not signature.matches_package(snapshot.package):
                continue
            evidence = []
            matches = 0
            for index, selector in enumerate(signature.selectors):
                node = selector.find(snapshot)
                if node is None:
                    continue
                matches += 1
                evidence.append(selector.semantic or node.resource_id or node.content_desc or node.text or f"selector-{index}")
            if matches >= max(1, signature.minimum_matches):
                return DetectedScreen(signature.kind, signature.confidence, tuple(evidence), signature.protected)
        return DetectedScreen("UNKNOWN", 0.0, (), False)

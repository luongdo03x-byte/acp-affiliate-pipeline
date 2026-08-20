"""In-memory Android UI hierarchy parsing with sensitive-field redaction."""
from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET


_BOUNDS_RE = re.compile(r"^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$")
_SENSITIVE_HINTS = (
    "password",
    "passwd",
    "otp",
    "one_time",
    "verification_code",
    "confirmation_code",
    "recovery_code",
    "security_code",
)


@dataclass(frozen=True)
class UiBounds:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass(frozen=True)
class UiNode:
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    clickable: bool
    enabled: bool
    bounds: UiBounds


@dataclass(frozen=True)
class UiSnapshot:
    package: str | None
    activity: str | None
    nodes: tuple[UiNode, ...]


class UiHierarchyReader:
    def _bounds(self, raw: str) -> UiBounds | None:
        match = _BOUNDS_RE.fullmatch(str(raw or "").strip())
        if match is None:
            return None
        left, top, right, bottom = (int(value) for value in match.groups())
        if right < left or bottom < top:
            return None
        return UiBounds(left, top, right, bottom)

    @staticmethod
    def _truthy(value: str | None) -> bool:
        return str(value or "").lower() == "true"

    @staticmethod
    def _sensitive(attrs: dict[str, str]) -> bool:
        if UiHierarchyReader._truthy(attrs.get("password")):
            return True
        class_name = str(attrs.get("class") or "")
        if "EditText" not in class_name:
            return False
        hints = " ".join(
            str(attrs.get(name) or "").lower()
            for name in ("resource-id", "content-desc")
        )
        return any(hint in hints for hint in _SENSITIVE_HINTS)

    def parse(self, xml_text: str, *, package: str | None, activity: str | None) -> UiSnapshot:
        root = ET.fromstring(str(xml_text or ""))
        nodes: list[UiNode] = []
        for element in root.iter("node"):
            attrs = dict(element.attrib)
            bounds = self._bounds(attrs.get("bounds", ""))
            if bounds is None:
                continue
            sensitive = self._sensitive(attrs)
            nodes.append(UiNode(
                text="" if sensitive else str(attrs.get("text") or ""),
                content_desc="" if sensitive else str(attrs.get("content-desc") or ""),
                resource_id=str(attrs.get("resource-id") or ""),
                class_name=str(attrs.get("class") or ""),
                clickable=self._truthy(attrs.get("clickable")),
                enabled=not attrs.get("enabled") or self._truthy(attrs.get("enabled")),
                bounds=bounds,
            ))
        return UiSnapshot(package=package, activity=activity, nodes=tuple(nodes))

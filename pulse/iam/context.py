"""The signed-in caller: who they are, what they may do, what they may see."""
from dataclasses import dataclass, field

from ..db import Scope


@dataclass
class Ctx:
    user_id: str
    emp_code: str
    display_name: str
    session_id: str | None
    provider: str
    roles: list[str]
    permissions: set[str]
    scope: Scope
    scope_nodes: list[dict] = field(default_factory=list)
    scope_label: str = ""
    store_count: int = 0
    depts: list[str] = field(default_factory=list)          # functional departments (HR, SOURCING, ...)
    data_classes: set[str] = field(default_factory=set)
    user: dict = field(default_factory=dict)

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    @property
    def elevated(self) -> bool:
        from .roles import ELEVATED
        return bool(ELEVATED.intersection(self.roles))

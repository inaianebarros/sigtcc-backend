from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PersonaAction:
    """One step a simulated account takes, performed through the service layer (ADR 0022)."""

    persona_email: str
    name: str
    perform: Callable[[], None]


class PersonaRule(Protocol):
    name: str

    def pending_actions(self) -> Iterable[PersonaAction]:
        """Reads the database and yields what is missing; a state outside the rule yields nothing."""
        ...

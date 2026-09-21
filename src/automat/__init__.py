# -*- test-case-name: automat -*-
"""
State-machines.
"""
from ._typed import TypeMachineBuilder, pep614, AlreadyBuiltError, TypeMachine
from ._core import NoTransition, Automaton
from ._audit import (
    AuditReport,
    UnreachableState,
    DeadEnd,
    TrappedComponent,
    ConflictingRegistration,
    auditAutomaton,
)
from ._methodical import MethodicalMachine

__all__ = [
    "TypeMachineBuilder",
    "TypeMachine",
    "NoTransition",
    "AlreadyBuiltError",
    "pep614",
    "MethodicalMachine",
    "Automaton",
    "AuditReport",
    "UnreachableState",
    "DeadEnd",
    "TrappedComponent",
    "ConflictingRegistration",
    "auditAutomaton",
]

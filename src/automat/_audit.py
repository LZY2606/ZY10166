# -*- test-case-name: automat._test.test_audit -*-
"""
Read-only static auditing of L{Automaton} transition graphs.

The auditor inspects only the declarative transition table of an
L{Automaton}; it never executes output actions and never instantiates
user classes.  Every diagnostic carries a shortest witness: a minimal
sequence of input symbols that demonstrates the problem, starting from
the initial state.

All results are reported in transition-registration order, so auditing
the same machine always produces the same report, regardless of object
hash values, C{repr} implementations, or Graphviz versions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Generic, Hashable, TypeVar

from ._core import Automaton, _NO_STATE

State = TypeVar("State", bound=Hashable)
Input = TypeVar("Input", bound=Hashable)


@dataclass(frozen=True)
class UnreachableState(Generic[State]):
    """
    A registered state that no input sequence from the initial state can
    ever reach.  There is no witness, by definition.
    """

    state: State


@dataclass(frozen=True)
class DeadEnd(Generic[State, Input]):
    """
    A reachable state with no outgoing transitions: once entered, no
    input is legal there any more.

    @ivar witness: a shortest input sequence leading from the initial
        state to this state.
    @ivar tiedWitnesses: the number of distinct shortest input sequences
        leading to this state.  C{witness} is the one that follows the
        earliest-registered transitions; when C{tiedWitnesses} is
        greater than 1, the witness is not unique.
    """

    state: State
    witness: tuple[Input, ...]
    tiedWitnesses: int


@dataclass(frozen=True)
class ClosedComponent(Generic[State, Input]):
    """
    A reachable strongly connected component, other than the one
    containing the initial state, that has at least one internal
    transition and no transition leaving it: once the machine enters
    this component, it can never return to the active region containing
    the initial state.

    @ivar states: the member states, in registration order.
    @ivar witness: a shortest input sequence leading from the initial
        state into the component.
    @ivar tiedWitnesses: the number of distinct shortest input sequences
        leading into the component; see L{DeadEnd.tiedWitnesses}.
    """

    states: tuple[State, ...]
    witness: tuple[Input, ...]
    tiedWitnesses: int


@dataclass(frozen=True)
class RegistrationConflict(Generic[State, Input]):
    """
    A duplicate registration that L{Automaton.addTransition} rejected:
    a transition from C{state} upon C{input} was already registered with
    target C{keptTarget}, and a later attempt to register the same
    (state, input) pair with target C{rejectedTarget} failed with
    L{ValueError}.

    @ivar witness: a shortest input sequence leading to C{state},
        followed by the conflicting C{input}; L{None} if C{state} is
        unreachable, in which case the conflict can never be observed at
        runtime.
    @ivar tiedWitnesses: the number of distinct shortest input sequences
        leading to C{state}; see L{DeadEnd.tiedWitnesses}.
    """

    state: State
    input: Input
    keptTarget: State
    rejectedTarget: State
    witness: tuple[Input, ...] | None
    tiedWitnesses: int


@dataclass(frozen=True)
class AuditReport(Generic[State, Input]):
    """
    The result of statically auditing an L{Automaton}.

    Each category of diagnostic is a tuple in registration order, so
    reports are stable and comparable.
    """

    unreachableStates: tuple[UnreachableState[State], ...]
    deadEnds: tuple[DeadEnd[State, Input], ...]
    closedComponents: tuple[ClosedComponent[State, Input], ...]
    registrationConflicts: tuple[RegistrationConflict[State, Input], ...]

    @property
    def ok(self) -> bool:
        """
        L{True} if the audit found nothing to report.
        """
        return not (
            self.unreachableStates
            or self.deadEnds
            or self.closedComponents
            or self.registrationConflicts
        )


def _stronglyConnectedComponents(
    roots: list[State],
    outgoing: dict[State, list[tuple[Input, State]]],
) -> list[list[State]]:
    """
    Compute the strongly connected components of the subgraph reachable
    from C{roots}, using an iterative Tarjan traversal so that deep
    machines do not exhaust the call stack.  Runs in O(V + E).
    """
    indexOf: dict[State, int] = {}
    lowlink: dict[State, int] = {}
    onStack: set[State] = set()
    stack: list[State] = []
    components: list[list[State]] = []
    counter = 0
    for root in roots:
        if root in indexOf:
            continue
        indexOf[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        onStack.add(root)
        work: list[tuple[State, Any]] = [(root, iter(outgoing[root]))]
        while work:
            node, edges = work[-1]
            descended = False
            for _input, target in edges:
                if target not in indexOf:
                    indexOf[target] = lowlink[target] = counter
                    counter += 1
                    stack.append(target)
                    onStack.add(target)
                    work.append((target, iter(outgoing[target])))
                    descended = True
                    break
                elif target in onStack:
                    lowlink[node] = min(lowlink[node], indexOf[target])
            if descended:
                continue
            work.pop()
            if work:
                ancestor = work[-1][0]
                lowlink[ancestor] = min(lowlink[ancestor], lowlink[node])
            if lowlink[node] == indexOf[node]:
                component = []
                while True:
                    member = stack.pop()
                    onStack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


def auditAutomaton(
    automaton: Automaton[State, Input, object],
) -> AuditReport[State, Input]:
    """
    Statically audit C{automaton}'s transition graph.

    This runs one breadth-first search and one strongly-connected-
    components pass over the transition table, so its cost is O(V + E)
    in the number of states and transitions; it does not re-scan the
    transition table per state.
    """
    # Snapshot the transition table exactly once, in registration order.
    transitions = list(automaton._transitions)
    conflicts = list(automaton._conflicts)
    initial = automaton._initialState

    # Assign each state a stable rank: the initial state first, then
    # states in the order they first appear in a registered transition.
    rankOf: dict[State, int] = {}

    def rank(state: State) -> None:
        if state not in rankOf:
            rankOf[state] = len(rankOf)

    if initial is not _NO_STATE:
        rank(initial)
    for inState, _input, outState, _outputs in transitions:
        rank(inState)
        rank(outState)

    outgoing: dict[State, list[tuple[Input, State]]] = {state: [] for state in rankOf}
    for inState, inputSymbol, outState, _outputs in transitions:
        outgoing[inState].append((inputSymbol, outState))

    # One breadth-first search from the initial state computes
    # distances, witnesses, and shortest-path counts for every state.
    distance: dict[State, int] = {}
    parent: dict[State, tuple[State, Input]] = {}
    shortestCount: dict[State, int] = {}
    if initial is not _NO_STATE:
        distance[initial] = 0
        shortestCount[initial] = 1
        queue: deque[State] = deque([initial])
        while queue:
            current = queue.popleft()
            for inputSymbol, target in outgoing[current]:
                if target not in distance:
                    distance[target] = distance[current] + 1
                    shortestCount[target] = shortestCount[current]
                    parent[target] = (current, inputSymbol)
                    queue.append(target)
                elif distance[target] == distance[current] + 1:
                    shortestCount[target] += shortestCount[current]

    def witnessFor(state: State) -> tuple[Input, ...]:
        inputs: list[Input] = []
        while state in parent:
            state, inputSymbol = parent[state]
            inputs.append(inputSymbol)
        inputs.reverse()
        return tuple(inputs)

    orderedStates = sorted(rankOf, key=rankOf.__getitem__)

    unreachableStates = tuple(
        UnreachableState(state) for state in orderedStates if state not in distance
    )

    deadEnds = tuple(
        DeadEnd(state, witnessFor(state), shortestCount[state])
        for state in orderedStates
        if state in distance and not outgoing[state]
    )

    reachable = [state for state in orderedStates if state in distance]
    closedComponents = []
    for component in _stronglyConnectedComponents(reachable, outgoing):
        members = set(component)
        internal = any(
            target in members
            for member in members
            for _input, target in outgoing[member]
        )
        if not internal:
            # A singleton with no self-loop is a dead end, reported above.
            continue
        if any(
            target not in members
            for member in members
            for _input, target in outgoing[member]
        ):
            continue
        if initial in members:
            # The component containing the initial state *is* the active
            # region, not a trap.
            continue
        entry = min(members, key=lambda m: (distance[m], rankOf[m]))
        best = distance[entry]
        tied = sum(
            shortestCount[member] for member in members if distance[member] == best
        )
        closedComponents.append(
            ClosedComponent(
                tuple(sorted(members, key=rankOf.__getitem__)),
                witnessFor(entry),
                tied,
            )
        )

    registrationConflicts = tuple(
        RegistrationConflict(
            inState,
            inputSymbol,
            keptTarget,
            rejectedTarget,
            (
                (witnessFor(inState) + (inputSymbol,))
                if inState in distance
                else None
            ),
            shortestCount.get(inState, 0),
        )
        for inState, inputSymbol, keptTarget, rejectedTarget in conflicts
    )

    return AuditReport(
        unreachableStates,
        deadEnds,
        tuple(closedComponents),
        registrationConflicts,
    )

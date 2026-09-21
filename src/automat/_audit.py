# -*- test-case-name: automat._test.test_audit -*-
"""
Read-only static auditing of L{Automaton} declarations.

An audit inspects only the transitions that were registered on an automaton.
It never constructs user objects, invokes input methods, or executes output
actions; in particular, the C{outputSymbols} attached to transitions are not
touched.

Each diagnosis carries a shortest witness: an input-symbol sequence that
demonstrates the problem when fed to a machine starting in its initial state.
Witnesses are chosen deterministically in transition registration order, so
audit output never depends on object hashes, C{repr}, or third-party library
versions.  When more than one shortest witness exists, one is reported and
C{witnessCount} records how many shortest witnesses exist in total; a count
of C{1} means the reported witness is unique.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Generic

from ._core import _NO_STATE, Automaton, Input, Output, State


@dataclass(frozen=True)
class UnreachableState(Generic[State, Input]):
    """
    A declared state that no sequence of registered inputs can reach from the
    initial state.

    There is deliberately no witness: by definition, no input sequence leads
    here.  Diagnostics are ordered by the first appearance of the state in
    transition registration order.
    """

    state: State


@dataclass(frozen=True)
class DeadEnd(Generic[State, Input]):
    """
    A reachable state with no outgoing registered transitions.

    Once the machine has been driven here with C{witness}, no registered input
    is legal (without an unhandled-transition fallback) and it can never
    leave.
    """

    state: State
    witness: tuple[Input, ...]
    witnessTransitions: tuple[int, ...]
    witnessCount: int


@dataclass(frozen=True)
class TrappedComponent(Generic[State, Input]):
    """
    A closed, strongly connected region with no path back to the active
    region.

    Every transition out of each member state stays inside the component, so
    any machine driven into it via C{witness} can never leave.  The component
    containing the initial state is never reported: it is the active region
    itself rather than a region entered and then trapped in.  A singleton
    component is only reported when it contains an internal edge (i.e. a
    self-loop); a state with no outgoing edges is reported as a L{DeadEnd}
    instead.

    @ivar states: all member states, in first-registration order.
    """

    states: tuple[State, ...]
    witness: tuple[Input, ...]
    witnessTransitions: tuple[int, ...]
    witnessCount: int


@dataclass(frozen=True)
class ConflictingRegistration(Generic[State, Input]):
    """
    A transition registration that was rejected.

    Registrations are rejected (and the machine left unchanged) when a
    transition from the same source state on the same input symbol had already
    been registered.  Replaying C{witness} drives the machine to the source
    state and then sends the conflicted input, which triggers the previously
    registered transition to C{existingTarget} rather than the rejected
    C{attemptedTarget}.  When the source state is itself unreachable, no
    witness exists and the witness attributes are C{None}/C{0}.
    """

    state: State
    input: Input
    attemptedTarget: State
    existingTarget: State
    witness: tuple[Input, ...] | None
    witnessTransitions: tuple[int, ...] | None
    witnessCount: int


@dataclass(frozen=True)
class AuditReport(Generic[State, Input]):
    """
    The result of L{auditAutomaton}.

    Each tuple is ordered deterministically by transition registration order;
    no ordering depends on object hashes or C{repr}.
    """

    unreachableStates: tuple[UnreachableState[State, Input], ...]
    deadEnds: tuple[DeadEnd[State, Input], ...]
    trappedComponents: tuple[TrappedComponent[State, Input], ...]
    conflictingRegistrations: tuple[ConflictingRegistration[State, Input], ...]
    hasUnhandledFallback: bool

    def isClean(self) -> bool:
        """
        Return C{True} when the audit found no unreachable states, no dead
        ends, no trapped components, and no conflicting registrations.
        """
        return not (
            self.unreachableStates
            or self.deadEnds
            or self.trappedComponents
            or self.conflictingRegistrations
        )


def auditAutomaton(
    automaton: Automaton[State, Input, Output]
) -> AuditReport[State, Input]:
    """
    Statically audit C{automaton}'s declared transitions.

    The audit examines registered transitions only.  It runs in O(V + E)
    time and space (V declared states, E registered transitions): one
    breadth-first traversal computes reachability, shortest witnesses and the
    number of shortest witnesses, and two depth-first traversals compute the
    strongly connected components.  It does not perform a per-state search.

    @param automaton: the automaton declaration to audit.

    @return: an L{AuditReport} whose diagnostics reference the state and
        input-token objects originally registered.
    """
    transitions = automaton.transitionsInRegistrationOrder()

    # Index states in first-seen order across registered transitions, so all
    # output ordering derives from registration order rather than hashing.
    stateOrder: list[State] = []
    stateIndex: dict[State, int] = {}

    def intern(state: State) -> int:
        idx = stateIndex.get(state)
        if idx is None:
            idx = len(stateOrder)
            stateIndex[state] = idx
            stateOrder.append(state)
        return idx

    # outEdges[i] / inEdges[i]: (other endpoint, registration index) pairs in
    # registration order.
    outEdges: list[list[tuple[int, int]]] = []
    inEdges: list[list[tuple[int, int]]] = []
    edgeBySourceInput: dict[tuple[State, Input], int] = {}

    def ensureLists(idx: int) -> None:
        while len(outEdges) <= idx:
            outEdges.append([])
            inEdges.append([])

    for edgeIndex, (inState, inputSymbol, outState, _outputs) in enumerate(transitions):
        sourceIndex = intern(inState)
        targetIndex = intern(outState)
        ensureLists(max(sourceIndex, targetIndex))
        outEdges[sourceIndex].append((targetIndex, edgeIndex))
        inEdges[targetIndex].append((sourceIndex, edgeIndex))
        edgeBySourceInput[(inState, inputSymbol)] = edgeIndex

    initialState = automaton.initialState
    if initialState is _NO_STATE:
        initialIndex = -1
    else:
        initialIndex = stateIndex.get(initialState, -1)
        if initialIndex == -1:
            initialIndex = len(stateOrder)
            stateIndex[initialState] = initialIndex
            stateOrder.append(initialState)
            ensureLists(initialIndex)

    stateCount = len(stateOrder)

    # BFS: distance, number of shortest paths, and one parent edge (the first
    # discovered in registration order) for witness reconstruction.
    distance: list[int] = [-1] * stateCount
    ways: list[int] = [0] * stateCount
    parentEdge: list[int] = [-1] * stateCount
    parentState: list[int] = [-1] * stateCount
    if initialIndex != -1:
        distance[initialIndex] = 0
        ways[initialIndex] = 1
        queue: deque[int] = deque([initialIndex])
        while queue:
            sourceIndex = queue.popleft()
            nextDistance = distance[sourceIndex] + 1
            for targetIndex, edgeIndex in outEdges[sourceIndex]:
                if distance[targetIndex] == -1:
                    distance[targetIndex] = nextDistance
                    ways[targetIndex] = ways[sourceIndex]
                    parentEdge[targetIndex] = edgeIndex
                    parentState[targetIndex] = sourceIndex
                    queue.append(targetIndex)
                elif distance[targetIndex] == nextDistance:
                    ways[targetIndex] += ways[sourceIndex]

    def witnessTo(
        targetIndex: int,
    ) -> tuple[tuple[Input, ...], tuple[int, ...], int]:
        edgeIndices: list[int] = []
        current = targetIndex
        while current != initialIndex:
            edgeIndices.append(parentEdge[current])
            current = parentState[current]
        edgeIndices.reverse()
        return (
            tuple(transitions[index][1] for index in edgeIndices),
            tuple(edgeIndices),
            ways[targetIndex],
        )

    unreachable: tuple[UnreachableState[State, Input], ...] = tuple(
        UnreachableState(stateOrder[index])
        for index in range(stateCount)
        if distance[index] == -1
    )

    hasFallback = automaton._unhandledTransition is not None
    deadEnds: list[DeadEnd[State, Input]] = []
    if initialIndex != -1 and not hasFallback:
        for index in range(stateCount):
            if distance[index] != -1 and not outEdges[index]:
                inputs, edgeIndices, witnessCount = witnessTo(index)
                deadEnds.append(
                    DeadEnd(stateOrder[index], inputs, edgeIndices, witnessCount)
                )
        deadEnds.sort(key=lambda each: (len(each.witness), each.witnessTransitions))

    components = _stronglyConnectedComponents(outEdges, inEdges, stateCount)
    componentOf = [0] * stateCount
    for componentIndex, members in enumerate(components):
        for member in members:
            componentOf[member] = componentIndex

    closed = [True] * len(components)
    internalEdge = [False] * len(components)
    for sourceIndex, outgoing in enumerate(outEdges):
        for targetIndex, _edgeIndex in outgoing:
            if componentOf[sourceIndex] == componentOf[targetIndex]:
                internalEdge[componentOf[sourceIndex]] = True
            else:
                closed[componentOf[sourceIndex]] = False

    trapped: list[TrappedComponent[State, Input]] = []
    initialComponent = componentOf[initialIndex] if initialIndex != -1 else -1
    for componentIndex, members in enumerate(components):
        if componentIndex == initialComponent:
            continue
        if not closed[componentIndex] or not internalEdge[componentIndex]:
            continue
        if any(distance[member] == -1 for member in members):
            # Whole component is unreachable; its members are already
            # reported as unreachable states.
            continue
        orderedMembers = tuple(sorted(members))
        minimumDistance = min(distance[member] for member in orderedMembers)
        closest = [
            member for member in orderedMembers if distance[member] == minimumDistance
        ]
        chosenInputs: tuple[Input, ...] = ()
        chosenEdges: tuple[int, ...] = ()
        chosenCount = 0
        bestKey: tuple[int, ...] | None = None
        for member in closest:
            inputs, edgeIndices, _count = witnessTo(member)
            chosenCount += ways[member]
            if bestKey is None or edgeIndices < bestKey:
                bestKey = edgeIndices
                chosenInputs = inputs
                chosenEdges = edgeIndices
        trapped.append(
            TrappedComponent(
                tuple(stateOrder[member] for member in orderedMembers),
                chosenInputs,
                chosenEdges,
                chosenCount,
            )
        )
    trapped.sort(key=lambda each: (len(each.witness), each.witnessTransitions))

    conflicts: list[ConflictingRegistration[State, Input]] = []
    transitionsBySource = automaton._transitionsBySource
    for (
        inState,
        inputSymbol,
        attemptedTarget,
        _outputs,
    ) in automaton.conflictingRegistrations():
        existing = transitionsBySource[(inState, inputSymbol)]
        sourceIndex = stateIndex.get(inState, -1)
        witnessInputs: tuple[Input, ...] | None
        witnessEdges: tuple[int, ...] | None
        count: int
        if sourceIndex == -1 or distance[sourceIndex] == -1:
            witnessInputs = None
            witnessEdges = None
            count = 0
        else:
            inputs, edgeIndices, sourceWays = witnessTo(sourceIndex)
            conflictEdgeIndex = edgeBySourceInput[(inState, inputSymbol)]
            witnessInputs = inputs + (inputSymbol,)
            witnessEdges = edgeIndices + (conflictEdgeIndex,)
            count = sourceWays
        conflicts.append(
            ConflictingRegistration(
                inState,
                inputSymbol,
                attemptedTarget,
                existing[2],
                witnessInputs,
                witnessEdges,
                count,
            )
        )

    return AuditReport(
        unreachableStates=unreachable,
        deadEnds=tuple(deadEnds),
        trappedComponents=tuple(trapped),
        conflictingRegistrations=tuple(conflicts),
        hasUnhandledFallback=hasFallback,
    )


def _stronglyConnectedComponents(
    outEdges: list[list[tuple[int, int]]],
    inEdges: list[list[tuple[int, int]]],
    stateCount: int,
) -> list[list[int]]:
    """
    Compute strongly connected components with iterative Kosaraju's
    algorithm, avoiding recursion-depth limits on large machines.

    Components and their member lists are produced deterministically.
    """
    visited = [False] * stateCount
    finishOrder: list[int] = []
    for root in range(stateCount):
        if visited[root]:
            continue
        visited[root] = True
        stack: list[tuple[int, int]] = [(root, 0)]  # (node, next edge)
        while stack:
            node, nextEdge = stack[-1]
            if nextEdge < len(outEdges[node]):
                target = outEdges[node][nextEdge][0]
                stack[-1] = (node, nextEdge + 1)
                if not visited[target]:
                    visited[target] = True
                    stack.append((target, 0))
            else:
                finishOrder.append(node)
                stack.pop()

    component: list[int] = [-1] * stateCount
    components: list[list[int]] = []
    for root in reversed(finishOrder):
        if component[root] != -1:
            continue
        componentIndex = len(components)
        members: list[int] = []
        component[root] = componentIndex
        pending: list[int] = [root]
        while pending:
            node = pending.pop()
            members.append(node)
            for target, _edgeIndex in inEdges[node]:
                if component[target] == -1:
                    component[target] = componentIndex
                    pending.append(target)
        members.sort()
        components.append(members)
    return components

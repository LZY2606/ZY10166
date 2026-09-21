"""
Tests for the read-only static audit API on L{automat.Automaton}.

These tests exercise the analysis directly against L{Automaton}; end-to-end
coverage for the public L{TypeMachineBuilder} and L{MethodicalMachine}
construction paths lives in L{test_type_based} and L{test_methodical}.
"""

from __future__ import annotations

from typing import Callable

from unittest import TestCase

from automat import (
    AuditReport,
    Automaton,
    ConflictingRegistration,
    DeadEnd,
    TrappedComponent,
    UnreachableState,
)
from automat._audit import auditAutomaton


def _build():
    """
    Build a small machine exercising every kind of diagnosis.

    Transitions, in registration order:

        0: start --a--> mid
        1: mid ---b--> trap1
        2: trap1 -c--> trap2
        3: trap2 -d--> trap1        (closed SCC {trap1, trap2})
        4: mid ---e--> sink
        5: sink --x--> end          (end is a no-outgoing dead end)
        6: orphan -o--> orphan      (unreachable self-loop)
    """
    a: Automaton[str, str, str] = Automaton("start")
    a.addTransition("start", "a", "mid", ())
    a.addTransition("mid", "b", "trap1", ())
    a.addTransition("trap1", "c", "trap2", ())
    a.addTransition("trap2", "d", "trap1", ())
    a.addTransition("mid", "e", "sink", ())
    a.addTransition("sink", "x", "end", ())
    a.addTransition("orphan", "o", "orphan", ())
    return a


class AuditShapeTests(TestCase):
    def test_reportIsAuditReportAndDefaults(self) -> None:
        self.assertIsInstance(Automaton("s").audit(), AuditReport)

    def test_emptyAutomaton(self) -> None:
        """
        An automaton with no declared initial state and no transitions has no
        states to diagnose.
        """
        report: AuditReport[str, str] = Automaton().audit()
        self.assertEqual(report.unreachableStates, ())
        self.assertEqual(report.deadEnds, ())
        self.assertEqual(report.trappedComponents, ())
        self.assertEqual(report.conflictingRegistrations, ())
        self.assertFalse(report.hasUnhandledFallback)
        self.assertTrue(report.isClean())

    def test_onlyInitialStateIsDeadEndWithEmptyWitness(self) -> None:
        """
        A declared initial state with no outgoing edges is a dead end reached
        by the empty input sequence.
        """
        report: AuditReport[str, str] = Automaton("solo").audit()
        (deadEnd,) = report.deadEnds
        self.assertEqual(deadEnd.state, "solo")
        self.assertEqual(deadEnd.witness, ())
        self.assertEqual(deadEnd.witnessTransitions, ())
        self.assertEqual(deadEnd.witnessCount, 1)
        self.assertFalse(report.isClean())

    def test_categoriesOfTheExampleMachine(self) -> None:
        report = _build().audit()
        self.assertEqual([each.state for each in report.unreachableStates], ["orphan"])
        self.assertEqual([each.state for each in report.deadEnds], ["end"])
        self.assertEqual(
            [each.states for each in report.trappedComponents],
            [("trap1", "trap2")],
        )
        self.assertEqual(report.conflictingRegistrations, ())

    def test_deadEndShortestWitness(self) -> None:
        report = _build().audit()
        (deadEnd,) = report.deadEnds
        self.assertEqual(deadEnd.witness, ("a", "e", "x"))
        self.assertEqual(deadEnd.witnessTransitions, (0, 4, 5))
        self.assertEqual(deadEnd.witnessCount, 1)

    def test_trappedComponentWitness(self) -> None:
        report = _build().audit()
        (trap,) = report.trappedComponents
        self.assertEqual(trap.witness, ("a", "b"))
        self.assertEqual(trap.witnessTransitions, (0, 1))
        self.assertEqual(trap.witnessCount, 1)

    def test_unreachableHasNoWitness(self) -> None:
        report = _build().audit()
        unreachable = report.unreachableStates[0]
        self.assertEqual(unreachable.state, "orphan")
        self.assertFalse(hasattr(unreachable, "witness"))

    def test_selfLoopOnInitialIsNotADiagnosis(self) -> None:
        """
        A closed SCC containing the initial state is the active region, not a
        trap.
        """
        a: Automaton[str, str, str] = Automaton("i")
        a.addTransition("i", "go", "i", ())
        report = a.audit()
        self.assertEqual(report.trappedComponents, ())
        self.assertEqual(report.deadEnds, ())
        self.assertTrue(report.isClean())

    def test_reachableSelfLoopStateIsATrap(self) -> None:
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "go", "loop", ())
        a.addTransition("loop", "again", "loop", ())
        (trap,) = a.audit().trappedComponents
        self.assertEqual(trap.states, ("loop",))
        self.assertEqual(trap.witness, ("go",))
        self.assertEqual(trap.witnessTransitions, (0,))

    def test_unreachableSelfLoopIsOnlyUnreachable(self) -> None:
        """
        A closed component that cannot be reached is reported as unreachable,
        not as a trap.
        """
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "go", "s", ())
        a.addTransition("iso", "x", "iso", ())
        report = a.audit()
        self.assertEqual([d.state for d in report.unreachableStates], ["iso"])
        self.assertEqual(report.trappedComponents, ())


class WitnessTieTests(TestCase):
    def test_parallelShortestWitnessesCounted(self) -> None:
        """
        Two equally short paths into the same dead end produce one
        registration-order witness and an accurate multiplicity.
        """
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "p", "x", ())  # edge 0
        a.addTransition("s", "q", "y", ())  # edge 1
        a.addTransition("x", "u", "d", ())  # edge 2
        a.addTransition("y", "v", "d", ())  # edge 3
        (deadEnd,) = a.audit().deadEnds
        self.assertEqual(deadEnd.state, "d")
        self.assertEqual(deadEnd.witness, ("p", "u"))
        self.assertEqual(deadEnd.witnessTransitions, (0, 2))
        self.assertEqual(deadEnd.witnessCount, 2)

    def test_lexicographicallyEarlierRegistrationWins(self) -> None:
        """
        When two shortest paths have different first steps, the earlier
        registered first step is chosen even if its continuation is later.
        """
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "first", "late", ())  # edge 0
        a.addTransition("s", "second", "early", ())  # edge 1
        a.addTransition("early", "e1", "d", ())  # edge 2
        a.addTransition("late", "l1", "d", ())  # edge 3
        (deadEnd,) = a.audit().deadEnds
        self.assertEqual(deadEnd.witness, ("first", "l1"))
        self.assertEqual(deadEnd.witnessCount, 2)

    def test_multipleInputsToSameTargetCountSeparately(self) -> None:
        """
        Distinct input tokens on parallel edges are distinct witnesses even
        when they lead to the same target.
        """
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "one", "m", ())  # edge 0
        a.addTransition("s", "two", "m", ())  # edge 1
        a.addTransition("m", "done", "d", ())  # edge 2
        (deadEnd,) = a.audit().deadEnds
        self.assertEqual(deadEnd.witness, ("one", "done"))
        self.assertEqual(deadEnd.witnessCount, 2)

    def test_diamondWitnessCount(self) -> None:
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "p", "x", ())
        a.addTransition("s", "q", "y", ())
        a.addTransition("x", "u", "d", ())
        a.addTransition("y", "v", "d", ())
        a.addTransition("d", "z", "final", ())
        (deadEnd,) = a.audit().deadEnds
        self.assertEqual(deadEnd.witness, ("p", "u", "z"))
        self.assertEqual(deadEnd.witnessCount, 2)

    def test_selfLoopsDoNotInflateWitnessCount(self) -> None:
        """
        Self-loops can never belong to a shortest path, so they must not add
        spurious shortest witnesses.
        """
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "loop", "s", ())
        a.addTransition("s", "go", "d", ())
        (deadEnd,) = a.audit().deadEnds
        self.assertEqual(deadEnd.witness, ("go",))
        self.assertEqual(deadEnd.witnessCount, 1)

    def test_parallelWitnessesIntoTrappedComponent(self) -> None:
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "p", "x", ())
        a.addTransition("s", "q", "y", ())
        a.addTransition("x", "u", "t", ())
        a.addTransition("y", "v", "t", ())
        a.addTransition("t", "round", "t", ())
        (trap,) = a.audit().trappedComponents
        self.assertEqual(trap.witness, ("p", "u"))
        self.assertEqual(trap.states, ("t",))
        self.assertEqual(trap.witnessCount, 2)


class OrderingTests(TestCase):
    def test_outputIsOrderedByRegistrationOrder(self) -> None:
        """
        Unreachable states, dead ends and conflicts appear in a deterministic
        registration-derived order.
        """
        a: Automaton[str, str, str] = Automaton("start")
        # unreachable states first seen in order ghost-a, ghost-b
        a.addTransition("ga", "i", "gb", ())
        # dead ends at differing, interleaved registration positions
        a.addTransition("start", "to-near", "near", ())
        a.addTransition("near", "n", "near-end", ())
        a.addTransition("start", "to-far", "far", ())
        a.addTransition("far", "f1", "far2", ())
        a.addTransition("far2", "f2", "far-end", ())
        report = a.audit()
        self.assertEqual([d.state for d in report.unreachableStates], ["ga", "gb"])
        self.assertEqual([d.state for d in report.deadEnds], ["near-end", "far-end"])
        self.assertEqual(
            [d.witness for d in report.deadEnds],
            [("to-near", "n"), ("to-far", "f1", "f2")],
        )

    def test_stableAcrossAuditCalls(self) -> None:
        a = _build()
        first = a.audit()
        second = a.audit()
        self.assertEqual(first, second)

    def test_orderingIndependentOfObjectHashes(self) -> None:
        """
        All states hash identically (forcing hash-table collisions) yet the
        diagnostic order must still follow registration order.
        """

        class Hostile:
            def __init__(self, label: str) -> None:
                self.label = label

            def __hash__(self) -> int:
                return 0

            def __eq__(self, other: object) -> bool:
                return isinstance(other, Hostile) and self.label == other.label

            def __repr__(self) -> str:  # pragma: no cover - never used
                raise AssertionError("audit must not rely on repr")

        start, d1src, d1, d2src, d2, iso = (
            Hostile("start"),
            Hostile("d1src"),
            Hostile("d1"),
            Hostile("d2src"),
            Hostile("d2"),
            Hostile("iso"),
        )
        a: Automaton[Hostile, str, str] = Automaton(start)
        a.addTransition(start, "to-d1", d1src, ())
        a.addTransition(d1src, "x1", d1, ())
        a.addTransition(start, "to-d2", d2src, ())
        a.addTransition(d2src, "x2a", d2, ())
        a.addTransition(iso, "z", iso, ())
        report = a.audit()
        self.assertEqual([d.state.label for d in report.deadEnds], ["d1", "d2"])
        self.assertEqual([d.state.label for d in report.unreachableStates], ["iso"])
        self.assertEqual(
            [d.witness for d in report.deadEnds],
            [("to-d1", "x1"), ("to-d2", "x2a")],
        )


class ConflictTests(TestCase):
    def test_conflictStillRaisesAndIsRecorded(self) -> None:
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "go", "kept", ())
        with self.assertRaises(ValueError):
            a.addTransition("s", "go", "rejected", ())
        (conflict,) = a.audit().conflictingRegistrations
        self.assertEqual(
            conflict,
            ConflictingRegistration(
                state="s",
                input="go",
                attemptedTarget="rejected",
                existingTarget="kept",
                witness=("go",),
                witnessTransitions=(0,),
                witnessCount=1,
            ),
        )

    def test_conflictWitnessIsShortestPathToSource(self) -> None:
        a: Automaton[str, str, str] = Automaton("start")
        a.addTransition("start", "a", "mid", ())
        a.addTransition("mid", "b", "deep", ())
        a.addTransition("deep", "stay", "deep", ())  # kept transition
        with self.assertRaises(ValueError):
            a.addTransition("deep", "stay", "elsewhere", ())
        (conflict,) = a.audit().conflictingRegistrations
        self.assertEqual(conflict.state, "deep")
        self.assertEqual(conflict.input, "stay")
        self.assertEqual(conflict.existingTarget, "deep")
        self.assertEqual(conflict.attemptedTarget, "elsewhere")
        self.assertEqual(conflict.witness, ("a", "b", "stay"))
        self.assertEqual(conflict.witnessTransitions, (0, 1, 2))

    def test_conflictOnUnreachableSourceHasNoWitness(self) -> None:
        a: Automaton[str, str, str] = Automaton("start")
        a.addTransition("start", "go", "start", ())
        a.addTransition("iso", "x", "iso", ())  # kept
        with self.assertRaises(ValueError):
            a.addTransition("iso", "x", "nope", ())
        report = a.audit()
        (conflict,) = report.conflictingRegistrations
        self.assertEqual(conflict.state, "iso")
        self.assertIsNone(conflict.witness)
        self.assertIsNone(conflict.witnessTransitions)
        self.assertEqual(conflict.witnessCount, 0)

    def test_conflictsOrderedByAttemptOrder(self) -> None:
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "one", "s", ())
        a.addTransition("s", "two", "s", ())
        with self.assertRaises(ValueError):
            a.addTransition("s", "two", "x", ())  # attempt 0
        with self.assertRaises(ValueError):
            a.addTransition("s", "one", "y", ())  # attempt 1
        conflicts = a.audit().conflictingRegistrations
        self.assertEqual([c.input for c in conflicts], ["two", "one"])

    def test_failedRegistrationDoesNotChangeMachine(self) -> None:
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "go", "kept", ())
        with self.assertRaises(ValueError):
            a.addTransition("s", "go", "rejected", ("out",))
        self.assertEqual(a.outputForInput("s", "go"), ("kept", []))
        self.assertEqual(len(a.allTransitions()), 1)


class ReadOnlyTests(TestCase):
    def test_outputsAreNeverExecuted(self) -> None:
        def boom() -> str:
            raise AssertionError("audit must not execute outputs")

        a: Automaton[str, str, Callable[[], str]] = Automaton("s")
        a.addTransition("s", "go", "d", (boom,))
        a.audit()  # must not invoke boom
        # and the output is still wired up normally
        self.assertEqual(a.outputForInput("s", "go"), ("d", [boom]))

    def test_auditDoesNotMutateAutomaton(self) -> None:
        a = _build()
        before = a.allTransitions()
        a.audit()
        a.audit()
        self.assertEqual(a.allTransitions(), before)


class FallbackTests(TestCase):
    def test_unhandledFallbackSuppressesDeadEnds(self) -> None:
        """
        When unhandledTransition() is configured, no state is a declared
        dead end; the report flags the fallback instead.
        """
        a: Automaton[str, str, str] = Automaton("s")
        a.addTransition("s", "go", "d", ())
        a.unhandledTransition("err", ())
        report = a.audit()
        self.assertTrue(report.hasUnhandledFallback)
        self.assertEqual(report.deadEnds, ())
        # reachability analysis still applies to the declared graph
        self.assertEqual([d.state for d in report.unreachableStates], [])


class ComplexityTests(TestCase):
    def test_denseGraphAuditIsLinear(self) -> None:
        """
        Auditing a dense graph performs only a linear number of
        state/input hash operations in V + E; a per-state repeated search
        (e.g. Floyd-Warshall or a BFS/DFS from every state) would be at least
        quadratic and trip this bound.
        """

        class Counted:
            hashes = 0
            eqs = 0

            def __init__(self, label: int) -> None:
                self.label = label

            def __hash__(self) -> int:
                type(self).hashes += 1
                return id(self) & 0xFFFF

            def __eq__(self, other: object) -> bool:
                type(self).eqs += 1
                return other is self

        n = 96
        states = [Counted(i) for i in range(n)]
        inputs: dict[tuple[int, int], Counted] = {}
        a: Automaton[Counted, Counted, str] = Automaton(states[0])
        for source in range(n):
            for target in range(n):
                if source == target:
                    continue
                token = Counted(source * n + target)
                inputs[(source, target)] = token
                a.addTransition(states[source], token, states[target], ())
        edges = n * (n - 1)
        total = n + edges

        Counted.hashes = 0
        Counted.eqs = 0
        report = auditAutomaton(a)
        touches = Counted.hashes + Counted.eqs

        self.assertTrue(report.isClean(), "dense complete graph has no diagnosis")
        self.assertGreater(touches, 0)
        # Constant chosen empirically: the implementation needs ~4 hashes per
        # edge; a quadratic re-search of the ~9,000 edges needs millions.
        self.assertLess(
            touches,
            20 * total,
            msg=(
                f"audit used {touches} hash/eq operations for V+E={total}; "
                "expected O(V+E), not a per-state repeated graph search"
            ),
        )

    def test_longChainWitnessCorrect(self) -> None:
        """
        A long chain's terminal dead end gets its full, exact shortest
        witness, proving the linear traversal is not just bounded but correct
        at scale.
        """
        length = 400
        a: Automaton[int, int, str] = Automaton(0)
        for state in range(length):
            a.addTransition(state, state + 1, state + 1, ())
        (deadEnd,) = a.audit().deadEnds
        self.assertEqual(deadEnd.state, length)
        self.assertEqual(deadEnd.witness, tuple(range(1, length + 1)))
        self.assertEqual(deadEnd.witnessCount, 1)

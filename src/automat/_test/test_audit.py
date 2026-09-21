# -*- test-case-name: automat._test.test_audit -*-
"""
Tests for the read-only static auditor in L{automat._audit}.
"""
from __future__ import annotations

from typing import Protocol
from unittest import TestCase

from .. import MethodicalMachine, TypeMachineBuilder
from .._audit import (
    AuditReport,
    ClosedComponent,
    DeadEnd,
    RegistrationConflict,
    UnreachableState,
)
from .._core import Automaton
from .._typed import TypedDataState, TypedState


class CountingList(list):
    """
    A list that counts how many times it is iterated, to verify that the
    auditor does not re-scan the transition table once per state.
    """

    iterations = 0

    def __iter__(self):
        type(self).iterations += 1
        return super().__iter__()


class CoreAuditTests(TestCase):
    """
    Audits of plain L{Automaton} instances with string states and inputs.
    """

    def test_emptyAutomaton(self) -> None:
        """
        An automaton with no initial state and no transitions audits clean.
        """
        report = Automaton().audit()
        self.assertEqual(report, AuditReport((), (), (), ()))
        self.assertTrue(report.ok)

    def test_initialStateOnly(self) -> None:
        """
        An automaton with only an initial state reports that state as a
        dead end with an empty witness.
        """
        report = Automaton("only").audit()
        self.assertEqual(report, AuditReport((), (DeadEnd("only", (), 1),), (), ()))

    def test_unreachableState(self) -> None:
        """
        States that no input sequence can reach are reported in
        registration order, not alphabetical or hash order.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "go", "end", ())
        automaton.addTransition("zeta", "stuck", "start", ())
        automaton.addTransition("alpha", "stuck", "zeta", ())
        report = automaton.audit()
        self.assertEqual(
            report.unreachableStates,
            (UnreachableState("zeta"), UnreachableState("alpha")),
        )

    def test_deadEnd(self) -> None:
        """
        A reachable state with no outgoing transitions is a dead end whose
        witness is the shortest input sequence leading to it.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "go", "middle", ())
        automaton.addTransition("middle", "sink", "end", ())
        report = automaton.audit()
        self.assertEqual(report.deadEnds, (DeadEnd("end", ("go", "sink"), 1),))

    def test_closedComponent(self) -> None:
        """
        A reachable cycle with no transitions leaving it is a closed
        component; the machine can never return to the active region.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "enter", "one", ())
        automaton.addTransition("one", "spin", "two", ())
        automaton.addTransition("two", "spin", "one", ())
        report = automaton.audit()
        self.assertEqual(
            report.closedComponents,
            (ClosedComponent(("one", "two"), ("enter",), 1),),
        )
        self.assertEqual(report.deadEnds, ())

    def test_selfLoopIsClosedComponentNotDeadEnd(self) -> None:
        """
        A reachable state whose only transition is a self-loop is a
        one-state closed component, not a dead end.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "go", "loopy", ())
        automaton.addTransition("loopy", "stay", "loopy", ())
        report = automaton.audit()
        self.assertEqual(report.deadEnds, ())
        self.assertEqual(
            report.closedComponents,
            (ClosedComponent(("loopy",), ("go",), 1),),
        )

    def test_initialComponentIsNotClosed(self) -> None:
        """
        The strongly connected component containing the initial state is
        the active region itself and is never reported as closed.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "flip", "other", ())
        automaton.addTransition("other", "flop", "start", ())
        report = automaton.audit()
        self.assertEqual(report, AuditReport((), (), (), ()))
        self.assertTrue(report.ok)

    def test_registrationConflict(self) -> None:
        """
        A duplicate registration is rejected with L{ValueError} and
        recorded; the audit reports the kept and rejected targets along
        with a witness that reaches the conflicting state and input.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "go", "middle", ())
        automaton.addTransition("middle", "flip", "kept", ())
        with self.assertRaises(ValueError) as raised:
            automaton.addTransition("middle", "flip", "rejected", ())
        self.assertIn("middle", str(raised.exception))
        report = automaton.audit()
        self.assertEqual(
            report.registrationConflicts,
            (
                RegistrationConflict(
                    "middle", "flip", "kept", "rejected", ("go", "flip"), 1
                ),
            ),
        )
        # The rejected target never entered the transition table.
        self.assertNotIn("rejected", automaton.states())

    def test_conflictInUnreachableState(self) -> None:
        """
        A conflict whose state cannot be reached has no witness.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("far", "dup", "near", ())
        with self.assertRaises(ValueError):
            automaton.addTransition("far", "dup", "away", ())
        report = automaton.audit()
        self.assertEqual(
            report.registrationConflicts,
            (RegistrationConflict("far", "dup", "near", "away", None, 0),),
        )

    def test_multipleInputsSameTarget(self) -> None:
        """
        When several inputs lead to the same state, the witness uses the
        earliest-registered transition.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "first", "end", ())
        automaton.addTransition("start", "second", "end", ())
        report = automaton.audit()
        self.assertEqual(report.deadEnds, (DeadEnd("end", ("first",), 2),))

    def test_tiedShortestWitnesses(self) -> None:
        """
        A diamond gives two shortest witnesses; the audit reports the one
        following the earliest-registered transitions and counts the tie
        instead of claiming uniqueness.
        """
        automaton: Automaton[str, str, str] = Automaton("top")
        automaton.addTransition("top", "left", "west", ())
        automaton.addTransition("top", "right", "east", ())
        automaton.addTransition("west", "down", "bottom", ())
        automaton.addTransition("east", "down", "bottom", ())
        report = automaton.audit()
        self.assertEqual(
            report.deadEnds,
            (DeadEnd("bottom", ("left", "down"), 2),),
        )

    def test_tiedWitnessesIntoClosedComponent(self) -> None:
        """
        Ties are also counted for witnesses entering a closed component.
        """
        automaton: Automaton[str, str, str] = Automaton("top")
        automaton.addTransition("top", "left", "west", ())
        automaton.addTransition("top", "right", "east", ())
        automaton.addTransition("west", "down", "trap", ())
        automaton.addTransition("east", "down", "trap", ())
        automaton.addTransition("trap", "stay", "trap", ())
        report = automaton.audit()
        self.assertEqual(
            report.closedComponents,
            (ClosedComponent(("trap",), ("left", "down"), 2),),
        )

    def test_unhandledFallbackDoesNotMaskDeadEnd(self) -> None:
        """
        The unhandled-transition fallback is a runtime error handler, not
        a registered transition, so it does not count as an out-edge.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "go", "end", ())
        automaton.unhandledTransition("start", ["oops"])
        report = automaton.audit()
        self.assertEqual(report.deadEnds, (DeadEnd("end", ("go",), 1),))

    def test_auditIsDeterministicAndReadOnly(self) -> None:
        """
        Auditing twice yields equal reports and does not modify the
        automaton.
        """
        automaton: Automaton[str, str, str] = Automaton("start")
        automaton.addTransition("start", "go", "end", ())
        automaton.addTransition("lost", "stuck", "start", ())
        before = (
            automaton.allTransitions(),
            automaton.states(),
            automaton.inputAlphabet(),
            list(automaton._transitions),
        )
        first = automaton.audit()
        second = automaton.audit()
        self.assertEqual(first, second)
        after = (
            automaton.allTransitions(),
            automaton.states(),
            automaton.inputAlphabet(),
            list(automaton._transitions),
        )
        self.assertEqual(before, after)

    def test_denseGraphAuditsInSinglePass(self) -> None:
        """
        On a dense graph the auditor iterates the transition table a
        constant number of times (one breadth-first search plus one
        strongly-connected-components pass, O(V + E)), rather than
        re-scanning it once per state.
        """
        stateCount = 40
        names = ["state-{}".format(n) for n in range(stateCount)]
        automaton: Automaton[str, str, str] = Automaton(names[0])
        for source in names:
            for target in names:
                if source != target:
                    automaton.addTransition(
                        source, "to-{}".format(target), target, ()
                    )
        transitions = CountingList(automaton._transitions)
        automaton._transitions = transitions
        report = automaton.audit()
        self.assertLessEqual(
            transitions.iterations,
            2,
            "auditor re-scanned the transition table {} times for "
            "{} states".format(transitions.iterations, stateCount),
        )
        # A fully connected graph is a single component containing the
        # initial state: everything reachable, nothing closed off.
        self.assertEqual(report, AuditReport((), (), (), ()))

    def test_deepChainDoesNotOverflowStack(self) -> None:
        """
        Auditing a long chain of states uses iterative traversals and
        does not hit the recursion limit.
        """
        depth = 2000
        names = ["chain-{}".format(n) for n in range(depth + 1)]
        automaton: Automaton[str, str, str] = Automaton(names[0])
        for source, target in zip(names, names[1:]):
            automaton.addTransition(source, "next", target, ())
        report = automaton.audit()
        self.assertEqual(
            report.deadEnds,
            (DeadEnd(names[-1], ("next",) * depth, 1),),
        )


class MethodicalAuditTests(TestCase):
    """
    End-to-end audits of a L{MethodicalMachine}, mapping diagnostics back
    to the declared state and input objects.
    """

    def test_methodicalAudit(self) -> None:
        executed = []
        machine = MethodicalMachine()

        @machine.state(initial=True)
        def start():
            "initial state"

        @machine.state()
        def middle():
            "middle state"

        @machine.state()
        def trap():
            "a state that can never be left"

        @machine.state()
        def nowhere():
            "an unreachable state"

        @machine.input()
        def advance(self):
            "advance input"

        @machine.input()
        def jam(self):
            "jam input"

        @machine.output()
        def announce(self):
            executed.append("announce")

        start.upon(advance, enter=middle, outputs=[announce])
        middle.upon(jam, enter=trap, outputs=[])
        trap.upon(jam, enter=trap, outputs=[])
        nowhere.upon(jam, enter=start, outputs=[])
        with self.assertRaises(ValueError):
            middle.upon(jam, enter=start, outputs=[])

        report = machine.audit()

        self.assertEqual(report.unreachableStates, (UnreachableState(nowhere),))
        self.assertEqual(report.deadEnds, ())
        self.assertEqual(
            report.closedComponents,
            (ClosedComponent((trap,), (advance, jam), 1),),
        )
        self.assertEqual(
            report.registrationConflicts,
            (
                RegistrationConflict(
                    middle, jam, trap, start, (advance, jam), 1
                ),
            ),
        )
        # Diagnostics map back to the declared objects themselves.
        self.assertIs(report.unreachableStates[0].state, nowhere)
        self.assertIs(report.closedComponents[0].states[0], trap)
        self.assertIs(report.closedComponents[0].witness[0], advance)
        self.assertIs(report.registrationConflicts[0].input, jam)
        # Auditing executed no outputs and created no instances.
        self.assertEqual(executed, [])


class TypedAuditTests(TestCase):
    """
    End-to-end audits of a L{TypeMachineBuilder} machine, including data
    states.
    """

    def test_typedAudit(self) -> None:
        executed = []

        class Inputs(Protocol):
            def go(self) -> None:
                "go input"

            def store(self) -> None:
                "store input"

        class Core:
            "core object"

        builder: TypeMachineBuilder[Inputs, Core] = TypeMachineBuilder(Inputs, Core)
        first = builder.state("first")
        second = builder.state("second")

        def makeData(machine: Inputs, core: Core) -> dict[str, int]:
            executed.append("makeData")
            return {}

        data = builder.state("data", makeData)
        lost = builder.state("lost")

        first.upon(Inputs.go).to(second).returns(None)
        second.upon(Inputs.store).to(data).returns(None)
        lost.upon(Inputs.go).to(first).returns(None)
        with self.assertRaises(ValueError):
            second.upon(Inputs.store).to(lost).returns(None)

        factory = builder.build()
        report = factory.audit()

        self.assertEqual(report.unreachableStates, (UnreachableState(lost),))
        self.assertEqual(
            report.deadEnds,
            (DeadEnd(data, ("go", "store"), 1),),
        )
        self.assertEqual(report.closedComponents, ())
        self.assertEqual(
            report.registrationConflicts,
            (
                RegistrationConflict(
                    second, "store", data, lost, ("go", "store"), 1
                ),
            ),
        )
        # Diagnostics map back to the declared state objects, including
        # the data state, and to protocol input names.
        self.assertIsInstance(report.deadEnds[0].state, TypedDataState)
        self.assertIsInstance(report.unreachableStates[0].state, TypedState)
        self.assertIs(report.registrationConflicts[0].keptTarget, data)
        # Auditing executed no outputs or data factories and created no
        # instances of the machine.
        self.assertEqual(executed, [])

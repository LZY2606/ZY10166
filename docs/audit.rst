================
 Static Auditing
================

In addition to rendering a machine graphically, Automat can *audit* a
declared machine: a read-only, machine-readable analysis of the transitions
you registered.  Auditing never instantiates your core/data objects, invokes
an input, or executes an output action.  It inspects only the declared
transition table.

You can audit either of the two public construction paths directly::

    report = machine.audit()                 # a built TypeMachine
    report = MyClass.machine.audit()         # a MethodicalMachine

You can also audit a low-level :class:`automat.Automaton`::

    from automat import auditAutomaton
    report = auditAutomaton(someAutomaton)

``report`` is an :class:`~automat.AuditReport` whose four diagnostics are
described below.  Every diagnosis carries a **shortest witness**: the input
sequence that demonstrates the problem when fed to a machine started in its
initial state.  Diagnostics reference the *actual state and input objects*
you registered (typed inputs are represented by their protocol method name
strings), not by temporary internal integers.

What is reported
================

``report.unreachableStates``
    :class:`~automat.UnreachableState` diagnoses for declared states that no
    sequence of registered inputs can reach from the initial state.  These
    carry no witness, because by definition no input sequence leads there.

``report.deadEnds``
    :class:`~automat.DeadEnd` diagnoses for states that *are* reachable but
    have no outgoing registered transition: once the witness drives the
    machine there, no registered input is legal and it cannot leave.

``report.trappedComponents``
    :class:`~automat.TrappedComponent` diagnoses for closed `strongly
    connected components
    <https://en.wikipedia.org/wiki/Strongly_connected_component>`_ with no
    path back to the active region.  Every transition out of each member
    state stays inside the component, so a machine that enters one (via the
    witness) can never leave.  The component containing the initial state is
    never reported -- it is the active region itself.  A singleton component
    is only reported when it contains an internal edge (a reachable
    self-loop); a state with no outgoing edges is reported as a
    ``deadEnd`` instead.

``report.conflictingRegistrations``
    :class:`~automat.ConflictingRegistration` diagnoses for transition
    registrations that were **rejected**.  Registering a second transition
    from the same source state on the same input raises ``ValueError`` (that
    behavior is unchanged) and leaves the machine as it was; the rejected
    attempt is additionally recorded so the audit can surface it.  The
    diagnosis identifies the source state, the conflicted input token, the
    target that kept the registration, and the rejected target.

Use ``report.isClean()`` to check whether all four categories are empty.

Witnesses and ties
==================

Each witness is the ``tuple`` of input tokens on one shortest path, paired
with ``witnessTransitions`` -- the registration-order indices of the
transitions it uses -- and ``witnessCount``.

When several shortest witnesses of the same length exist, exactly one is
reported: the one lexicographically smallest by transition registration
order.  ``witnessCount`` states how many shortest witnesses exist; a value
of ``1`` means the reported witness is unique, and a larger value is a tie
count rather than a claim of uniqueness.

All diagnostics are emitted in a deterministic order derived solely from
transition registration order.  Ordering never depends on object hashes,
``repr()``, or the installed Graphviz version.

Unhandled-transition fallback
=============================

If an automaton configured a catch-all via
``Automaton.unhandledTransition``, every state technically has a handler for
every input, so no state is a declared dead end.  In that case
``report.deadEnds`` is empty and ``report.hasUnhandledFallback`` is
``True``.  Unreachable states, trapped components, and conflicts are still
reported against the explicitly declared graph.

Complexity
==========

An audit runs in **O(V + E)** time and space for ``V`` declared states and
``E`` registered transitions.  A single breadth-first traversal computes
reachability, shortest witnesses, and shortest-witness counts, and two
depth-first traversals compute the strongly connected components (an
iterative Kosaraju pass, so large machines cannot overflow Python's
recursion limit).  It does not perform a repeated per-state graph search.
There is a regression test bounding the work done on a large dense graph.

Compatibility
=============

The audit API is purely additive: existing public behavior is unchanged.  In
particular, registering a duplicate transition still raises ``ValueError``
immediately, ``Automaton.allTransitions()`` still returns a ``frozenset``,
and no state machine ever needs auditing to run.  Internally, transitions
are now also retained in registration order (and duplicate detection is
O(1) instead of O(n) per registration), which is an observable improvement
in registration cost but not in the public return types.

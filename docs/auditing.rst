Static Auditing
===============

Automat can visualize a state machine, but a picture cannot be consumed
by tools.  The *static auditor* answers the same structural questions in
a machine-readable form: which states can never be reached, where can
the machine get stuck, and which registrations failed?

Auditing is available on every public construction path:

- ``Automaton.audit()`` for the core automaton,
- ``MethodicalMachine.audit()`` for methodical machines,
- ``TypeMachine.audit()`` for typed machines (call it on the factory
  returned by ``TypeMachineBuilder.build()``).

Each returns an ``automat.AuditReport`` with four tuples of diagnostics,
each carrying the *actual* state and input objects you registered (not
integers or strings invented by the auditor), plus a shortest *witness*:
a minimal sequence of inputs, starting from the initial state, that
demonstrates the problem.

Diagnostics
-----------

- ``unreachableStates`` (``UnreachableState``): states that no input
  sequence from the initial state can reach.  By definition these have
  no witness.
- ``deadEnds`` (``DeadEnd``): reachable states with no outgoing
  transitions.  ``witness`` is a shortest input sequence leading to the
  state.
- ``closedComponents`` (``ClosedComponent``): reachable strongly
  connected components — other than the one containing the initial
  state — with at least one internal transition and no transition
  leaving them.  Once entered, the machine can never return to the
  active region.  ``states`` lists the members in registration order;
  ``witness`` is a shortest input sequence into the component.  A
  self-loop counts as a closed component of one state, not a dead end.
- ``registrationConflicts`` (``RegistrationConflict``): duplicate
  registrations that ``addTransition`` rejected with ``ValueError``.
  ``keptTarget`` is the target of the original transition,
  ``rejectedTarget`` the target of the failed attempt.  ``witness``
  leads to the conflicting state and ends with the conflicting input;
  it is ``None`` when the conflicting state is itself unreachable.

Ties and ordering
-----------------

When several distinct shortest witnesses exist, the report chooses the
one that follows the earliest-registered transitions and sets
``tiedWitnesses`` to the number of tied shortest sequences, so callers
can see that the witness is not unique.

All diagnostics are ordered by transition *registration order*: the
initial state first, then states in the order they first appear in a
registered transition.  Reports therefore do not depend on object hash
values, ``repr`` output, PYTHONHASHSEED, or Graphviz versions, and
auditing the same machine twice yields equal reports.

Semantics notes
---------------

- The ``unhandledTransition`` fallback is a runtime error handler, not
  a registered transition; it does not count as an out-edge, so a state
  with no registered outgoing transitions is still a dead end.
- Only states that participate in at least one registered transition
  (or are the initial state) are visible to the auditor, matching
  ``Automaton.states()``.
- Auditing is strictly read-only: it never executes output actions,
  never calls data factories, and never instantiates user classes.

Complexity
----------

The auditor snapshots the transition table once, then runs a single
breadth-first search (for reachability, witnesses, and tie counts) and
a single iterative strongly-connected-components pass.  The total cost
is O(V + E) in the number of states and transitions, with no per-state
re-scanning of the transition table, so dense machines audit in time
proportional to their size.

Compatibility
-------------

The auditor only reads the existing declarative transition table; no
public behavior changes.  Failed duplicate registrations are now
*recorded* for auditing in addition to raising ``ValueError`` as
before, and the transition table preserves registration order
internally, which is what makes the stable reporting above possible.

# SPDX-License-Identifier: MIT
"""The measurement layer (ARCHITECTURE 7).

Deterministic, not an agent, and the only agent- or orchestrator-side consumer
of ``sealed._labels``. That wording is the two-consumer model STEP-01
established and STEP-02 and STEP-03 carried forward: the build pipeline
legitimately reads the sealed table too, at build time, for the D6 reconcile
gate. A rule read as "nothing but measurement may ever touch it" would fail on
the build, which is why it is stated as the *agent-side* rule instead.

STEP-07 owns this package. It exists early because STEP-04 D5 requires the
recovery metric to be "computed by measurement-side code with sealed-label
access", and putting that anywhere else would have put ground truth inside the
reach of an agent mandate. Only ``recovery`` is implemented here; VVR, the
workflow lens, and the report generator remain STEP-07's.
"""

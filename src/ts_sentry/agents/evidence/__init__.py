# SPDX-License-Identifier: MIT
"""The evidence agent (ARCHITECTURE 4.2, mandate ceiling ASSEMBLE).

Thin by design, per ARCHITECTURE 10: this package holds the agent's output
schema and, from D2, its prompts and the proposal format it writes to. The
pivot templates it proposes from, the executor that runs them, the analyst
review boundary, and the assembly gate that judges the result are all
orchestrator-side. An agent that held any of those would be an agent judging
itself.
"""

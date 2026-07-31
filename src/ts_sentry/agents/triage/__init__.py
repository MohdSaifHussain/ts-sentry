# SPDX-License-Identifier: MIT
"""D5: the triage agent (ARCHITECTURE 4.1, mandate ceiling OBSERVE).

Solves the analyst's first-hour problem: where to look. The deterministic
scorer does the ranking; the model contributes one line of "why this case
first" and is allowed to cite nothing but the score components.

The split is the design. A number a model produced is a number nobody can
audit; a number the scorer produced, rendered as its own components, is a
number an analyst can argue with.
"""

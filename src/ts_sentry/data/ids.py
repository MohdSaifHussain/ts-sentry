# SPDX-License-Identifier: MIT
"""PEP 695 type aliases for entity identifiers.

Plain aliases over ``str`` (PEP 695 does not provide nominal/newtype
distinction) - the alias names exist for readability and self-documenting
signatures across the data-foundation modules.
"""

type ChannelId = str
type VideoId = str
type CommentId = str
type AccountId = str
type EventId = str
type InfraHintId = str

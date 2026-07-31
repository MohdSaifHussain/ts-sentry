# SPDX-License-Identifier: MIT
"""StrEnum categoricals shared across the synthetic platform schema."""

from enum import StrEnum


class EntityKind(StrEnum):
    """Graph-node entity types (ARCHITECTURE 4.2 entity graph)."""

    CHANNEL = "channel"
    VIDEO = "video"
    COMMENT = "comment"
    ACCOUNT = "account"


class EngagementKind(StrEnum):
    """Engagement event types. VIEW carries the VVR-required fields."""

    VIEW = "view"
    LIKE = "like"
    DISLIKE = "dislike"
    SHARE = "share"
    SUBSCRIBE = "subscribe"
    REPORT = "report"


class ThreatClass(StrEnum):
    """Ground-truth threat classification (ARCHITECTURE Section 2.1).

    BENIGN is an explicit member, not a null, so every entity in
    ``sealed._labels`` carries exactly one label row and the label-completeness
    hypothesis property (STEP-01 3.5) can assert non-null coverage.
    """

    BENIGN = "benign"
    T01_COMMENT_SPAM_RING = "t01_comment_spam_ring"
    T02_FAKE_ENGAGEMENT_NETWORK = "t02_fake_engagement_network"
    T03_OFF_PLATFORM_DIVERSION = "t03_off_platform_diversion"
    T04_UNDISCLOSED_SYNTHETIC_MEDIA = "t04_undisclosed_synthetic_media"
    T05_AI_PERSONA_AUTHORITY = "t05_ai_persona_authority"
    T06_SLOP_FARM = "t06_slop_farm"
    T07_COORDINATED_INFLUENCE_OP = "t07_coordinated_influence_op"


class ProvenanceSignal(StrEnum):
    """C2PA-direction content-credentials signal (ARCHITECTURE 8.7)."""

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class InfraSignalKind(StrEnum):
    """Infrastructure-overlap signal types for evidence pivots."""

    SHARED_UPLOAD_PATTERN = "shared_upload_pattern"
    TEMPLATE_REUSE = "template_reuse"
    SHARED_DEVICE = "shared_device"
    SHARED_IP_BUCKET = "shared_ip_bucket"
    LINK_DOMAIN_REUSE = "link_domain_reuse"

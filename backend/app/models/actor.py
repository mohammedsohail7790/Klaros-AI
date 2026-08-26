from enum import StrEnum


class ActorType(StrEnum):
    """Who performed an audited action (section 9 / spec section 23)."""

    USER = "USER"
    AI = "AI"
    SYSTEM = "SYSTEM"
    WORKFLOW = "WORKFLOW"

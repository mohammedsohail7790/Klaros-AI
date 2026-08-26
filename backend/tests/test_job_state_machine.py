import pytest

from app.services.job_state_machine import InvalidJobTransitionError, validate_transition


def test_valid_chain_is_allowed() -> None:
    chain = [
        "DRAFT", "SCHEDULED", "DISPATCHED", "EN_ROUTE", "ON_SITE",
        "IN_PROGRESS", "QA_PENDING", "COMPLETED", "CLOSED",
    ]
    for current, target in zip(chain, chain[1:]):
        validate_transition(current, target)  # must not raise


@pytest.mark.parametrize(
    "current,target",
    [
        ("CLOSED", "IN_PROGRESS"),
        ("COMPLETED", "EN_ROUTE"),
        ("CANCELLED", "ON_SITE"),
        ("DRAFT", "IN_PROGRESS"),
        ("QA_PENDING", "IN_PROGRESS"),
    ],
)
def test_invalid_transitions_rejected(current: str, target: str) -> None:
    with pytest.raises(InvalidJobTransitionError):
        validate_transition(current, target)


def test_block_and_unblock() -> None:
    validate_transition("IN_PROGRESS", "BLOCKED")
    validate_transition("BLOCKED", "IN_PROGRESS")
    with pytest.raises(InvalidJobTransitionError):
        validate_transition("BLOCKED", "COMPLETED")

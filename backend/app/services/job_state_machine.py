"""section 6: explicit job state transitions.

A fixed transition table, not "anything goes." `CLOSED` and `CANCELLED` are
terminal — no supported reopening workflow exists yet, so transitions out of
them are rejected rather than silently allowed.
"""

from app.models.operations import JobStatus

ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DRAFT: {JobStatus.SCHEDULED, JobStatus.CANCELLED},
    JobStatus.SCHEDULED: {JobStatus.DISPATCHED, JobStatus.CANCELLED},
    JobStatus.DISPATCHED: {JobStatus.EN_ROUTE, JobStatus.BLOCKED, JobStatus.CANCELLED},
    JobStatus.EN_ROUTE: {JobStatus.ON_SITE, JobStatus.BLOCKED, JobStatus.CANCELLED},
    JobStatus.ON_SITE: {JobStatus.IN_PROGRESS, JobStatus.BLOCKED, JobStatus.CANCELLED},
    JobStatus.IN_PROGRESS: {JobStatus.QA_PENDING, JobStatus.BLOCKED, JobStatus.CANCELLED},
    JobStatus.BLOCKED: {JobStatus.DISPATCHED, JobStatus.EN_ROUTE, JobStatus.ON_SITE, JobStatus.IN_PROGRESS},
    JobStatus.QA_PENDING: {JobStatus.COMPLETED, JobStatus.BLOCKED},
    JobStatus.COMPLETED: {JobStatus.CLOSED},
    JobStatus.CLOSED: set(),
    JobStatus.CANCELLED: set(),
}


class InvalidJobTransitionError(Exception):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"Cannot transition job from {current} to {target}")
        self.current = current
        self.target = target


def validate_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(JobStatus(current), set())
    if JobStatus(target) not in allowed:
        raise InvalidJobTransitionError(current, target)

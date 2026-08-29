"""section 7: invoice approval policy — deterministic, not a ToolRegistry
per-tool static entry, since the decision depends on the invoice's own
data (total, discount, related scope changes). `finance.request_invoice_approval`
itself is AUTO at the ToolRegistry level; this function is what that tool's
body actually runs to decide AUTO-approve vs. create a pending approval.

Thresholds are static constants for Phase 5 (the same simplification
pattern as Phase 3's scoring thresholds and Phase 4's `OVERDUE_TOLERANCE_MINUTES`)
— a per-tenant configurable policy table is a natural, named limitation.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.models.finance import Invoice

AUTO_APPROVAL_THRESHOLD = Decimal("1000.00")
UNUSUAL_DISCOUNT_RATIO = Decimal("0.20")  # discount > 20% of subtotal is "unusual"


@dataclass
class PolicyDecision:
    requires_approval: bool
    reasons: list[str]


def evaluate_invoice_approval(invoice: Invoice, *, has_unresolved_scope_change: bool) -> PolicyDecision:
    reasons: list[str] = []

    if invoice.total > AUTO_APPROVAL_THRESHOLD:
        reasons.append(f"total ${invoice.total} exceeds auto-approval threshold ${AUTO_APPROVAL_THRESHOLD}")

    if invoice.subtotal > 0 and invoice.discount > (invoice.subtotal * UNUSUAL_DISCOUNT_RATIO):
        reasons.append(
            f"discount ${invoice.discount} exceeds {int(UNUSUAL_DISCOUNT_RATIO * 100)}% of subtotal"
        )

    if has_unresolved_scope_change:
        reasons.append("job has an unresolved (PENDING_APPROVAL) scope change")

    return PolicyDecision(requires_approval=bool(reasons), reasons=reasons)

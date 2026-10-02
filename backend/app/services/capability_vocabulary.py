"""Business Builder: the shared, generic capability vocabulary.

One small, data-only table describing the capabilities a business can
require — what to call them in the product, which group they belong to,
what free-text phrases mean them, and how well Klaros itself can serve
them today. It is *reference data*, exactly like
`IntegrationProviderCatalog` / `VerticalExtension`: a capability is looked
up by key, never branched on, and nothing here (or in any consumer) ever
tests a business/vertical name. A referral business and an online shop flow
through the same table — one requires `provider_directory` + `lead_capture`,
the other `storefront` + `supplier_integration` — and the engine treats them
identically.

`klaros_support` is deliberately honest (never "connected" by default):

  NATIVE        a Klaros module already provides it (CRM/leads, website
                builder, finance, marketing, ...), usable now.
  INTEGRATION   a catalogued, real provider adapter can satisfy it.
  PLANNED       no Klaros module or adapter exists yet — the Business Map
                and Requirements screens show it as "Planned" rather than
                pretending it is wired up.

Unknown capability keys (anything the user or a vertical states that is
not in this table) are still first-class: they are shown with a humanised
label, group "Other", and support PLANNED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityDefinition:
    key: str
    label: str
    group: str
    description: str
    klaros_support: str  # NATIVE | INTEGRATION | PLANNED
    # Where in Klaros a NATIVE capability lives (frontend route), if any.
    native_route: str | None = None
    # True when an AI workforce (voice / messaging) can serve this
    # capability on the customer-facing side. Consumed by the AI Workforce
    # boundary — never an assertion that a workforce is connected.
    workforce_addressable: bool = False
    aliases: tuple[str, ...] = ()
    # An outside party this capability exchanges data with (shown as an
    # actor on the Business Map), e.g. the supplier behind a catalog.
    external_party: str | None = None
    # Other capability keys this one builds on (Business Map "depends on"
    # edges are drawn only when both ends are actually required).
    depends_on: tuple[str, ...] = ()


GROUP_ORDER = (
    "Customer-facing",
    "Sales & customers",
    "Catalog & supply",
    "Operations",
    "Finance",
    "Marketing",
    "Data & insight",
    "Other",
)

_DEFS: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        "website", "Website", "Customer-facing",
        "A public website that presents the business and captures enquiries.",
        "NATIVE", "/website", False,
        ("website", "web site", "landing page", "online presence", "web presence"),
    ),
    CapabilityDefinition(
        "storefront", "Online storefront", "Customer-facing",
        "A customer-facing shop where products can be browsed and bought.",
        "PLANNED", None, False,
        ("storefront", "online store", "online shop", "ecommerce", "e-commerce", "shop", "web store", "shopify"),
        depends_on=('product_catalog', 'payment_processing'),
    ),
    CapabilityDefinition(
        "lead_capture", "Lead capture", "Sales & customers",
        "Capture enquiries from the website and other channels into one place.",
        "NATIVE", "/leads", False,
        ("lead capture", "lead intake", "lead generation", "enquiries", "inquiries", "quote request", "patient leads", "patient lead capture"),
    ),
    CapabilityDefinition(
        "lead_qualification", "Lead qualification", "Sales & customers",
        "Decide which enquiries are worth a conversation, and route them.",
        "NATIVE", "/leads", True,
        ("lead qualification", "qualification", "qualifying leads", "lead scoring", "screening"),
        depends_on=('lead_capture',),
    ),
    CapabilityDefinition(
        "crm", "Customer records (CRM)", "Sales & customers",
        "A single record of every customer, lead and conversation.",
        "NATIVE", "/customers", False,
        ("crm", "customer management", "customer records", "customer database", "contacts"),
    ),
    CapabilityDefinition(
        "communication", "Customer communication", "Customer-facing",
        "Talk to customers by phone, email and messaging — inbound and outbound.",
        "INTEGRATION", "/settings/voice", True,
        ("communication", "communications", "customer communication", "messaging", "email", "sms", "whatsapp", "phone", "calls", "voice", "outbound communication", "customer support", "support", "help desk"),
    ),
    CapabilityDefinition(
        "appointment_scheduling", "Scheduling & consultations", "Operations",
        "Book consultations or appointments and keep a calendar.",
        "INTEGRATION", "/calendar", True,
        ("appointment scheduling", "consultation scheduling", "scheduling", "appointments", "appointment booking", "booking", "bookings", "consultations", "consultation handling", "calendar"),
        depends_on=('lead_qualification',),
    ),
    CapabilityDefinition(
        "provider_directory", "Provider / partner directory", "Catalog & supply",
        "A managed list of the providers or partners customers are referred to.",
        "NATIVE", "/medical-tourism/providers", False,
        ("provider directory", "hospital connections", "partner hospitals", "hospital partnerships", "provider database", "hospital database", "hospital directory", "doctor directory", "hospital and doctor directory", "doctor and hospital directory", "directory of hospitals", "provider information", "doctor information", "providers", "partner network", "provider management", "hospital network"),
        external_party='Providers & partners',
    ),
    CapabilityDefinition(
        "service_catalog", "Service / treatment catalog", "Catalog & supply",
        "A structured catalog of what the business offers.",
        "NATIVE", "/medical-tourism/procedures", False,
        ("service catalog", "treatment catalog", "procedure catalog", "treatments", "procedures", "service menu"),
    ),
    CapabilityDefinition(
        "product_catalog", "Product catalog", "Catalog & supply",
        "A structured catalog of products with pricing and availability.",
        "PLANNED", None, False,
        ("product catalog", "product catalogue", "catalog", "catalogue", "products", "product listings", "product sync", "product synchronization", "product synchronisation"),
        depends_on=('supplier_integration',),
    ),
    CapabilityDefinition(
        "supplier_integration", "Supplier connection", "Catalog & supply",
        "A live connection to the supplier that holds stock and ships orders.",
        "PLANNED", None, False,
        ("supplier integration", "supplier connection", "supplier", "suppliers", "supplier catalog", "supplier feed", "wholesaler", "vendor integration"),
        external_party='Supplier',
    ),
    CapabilityDefinition(
        "inventory", "Inventory", "Catalog & supply",
        "Know what is in stock and keep it in step with the supplier.",
        "PLANNED", None, False,
        ("inventory", "inventory management", "inventory sync", "stock", "stock levels"),
        depends_on=('supplier_integration',),
    ),
    CapabilityDefinition(
        "pricing", "Pricing", "Catalog & supply",
        "Set and maintain prices, margins and currencies.",
        "PLANNED", None, False,
        ("pricing", "price management", "margins", "pricing rules", "dynamic pricing"),
        depends_on=('product_catalog',),
    ),
    CapabilityDefinition(
        "order_management", "Order management", "Operations",
        "Take orders and follow each one from purchase to delivery.",
        "PLANNED", None, False,
        ("order management", "orders", "order processing", "order tracking"),
        depends_on=('product_catalog', 'payment_processing'),
    ),
    CapabilityDefinition(
        "fulfillment", "Fulfilment", "Operations",
        "Get the order to the customer — shipping, tracking, returns.",
        "PLANNED", None, False,
        ("fulfillment", "fulfilment", "shipping", "delivery", "logistics", "returns"),
        depends_on=('order_management', 'supplier_integration'),
    ),
    CapabilityDefinition(
        "referral_workflow", "Referral workflow", "Operations",
        "Hand a qualified customer to the right provider and follow the outcome.",
        "NATIVE", "/medical-tourism/referrals", False,
        ("referral workflow", "referral", "referrals", "patient referral", "referral management", "case handoff"),
        depends_on=('lead_qualification', 'provider_directory'),
        external_party='Providers & partners',
    ),
    CapabilityDefinition(
        "commission_tracking", "Commission tracking", "Finance",
        "Track what is owed to you for each referral or sale.",
        "NATIVE", "/medical-tourism/referrals", False,
        ("commission tracking", "commission", "commissions", "referral fees", "referral fee", "cross border commission"),
        depends_on=('referral_workflow',),
    ),
    CapabilityDefinition(
        "payment_processing", "Payments", "Finance",
        "Accept and reconcile customer payments.",
        "INTEGRATION", "/settings/integrations", False,
        ("payment processing", "payments", "payment", "checkout", "card payments", "billing", "stripe"),
    ),
    CapabilityDefinition(
        "accounting", "Accounting & finance", "Finance",
        "Invoices, receivables, cash and the books.",
        "INTEGRATION", "/finance", False,
        ("accounting", "finance", "invoicing", "bookkeeping", "financial reporting", "invoices"),
    ),
    CapabilityDefinition(
        "marketing", "Marketing", "Marketing",
        "Attract customers — content, SEO, campaigns and outreach.",
        "NATIVE", "/marketing", False,
        ("marketing", "seo", "social media", "social", "advertising", "ads", "content marketing", "campaigns", "email marketing"),
    ),
    CapabilityDefinition(
        "analytics", "Analytics", "Data & insight",
        "See what is working: traffic, conversion, revenue, performance.",
        "NATIVE", "/dashboard", False,
        ("analytics", "reporting", "reports", "dashboards", "insights", "metrics", "kpis"),
    ),
    CapabilityDefinition(
        "compliance", "Compliance", "Operations",
        "Meet the regulatory or contractual obligations of the business.",
        "NATIVE", "/settings/compliance", False,
        ("compliance", "regulatory compliance", "credential verification", "compliance verification", "licensing"),
    ),
    CapabilityDefinition(
        "multi_currency", "Multi-currency", "Finance",
        "Quote and track money in more than one currency.",
        "NATIVE", "/medical-tourism/referrals", False,
        ("multi currency", "multi-currency", "currencies", "foreign currency"),
    ),
)

CAPABILITIES: dict[str, CapabilityDefinition] = {d.key: d for d in _DEFS}


def _normalise_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


_ALIAS_INDEX: dict[str, str] = {}
for _d in _DEFS:
    _ALIAS_INDEX[_normalise_phrase(_d.key)] = _d.key
    _ALIAS_INDEX[_normalise_phrase(_d.label)] = _d.key
    for _a in _d.aliases:
        _ALIAS_INDEX[_normalise_phrase(_a)] = _d.key


def to_snake(text: str) -> str:
    return _normalise_phrase(text).replace(" ", "_")


def canonical_key(raw: str) -> str:
    """Map a free-text / dotted / snake_case capability phrase to its
    canonical key where the vocabulary knows it, else a stable snake_case
    key. A vertical's namespaced registry capability (e.g.
    `some_vertical.provider_directory`) resolves via its final segment."""
    if not raw or not raw.strip():
        return ""
    phrase = _normalise_phrase(raw)
    if phrase in _ALIAS_INDEX:
        return _ALIAS_INDEX[phrase]
    if "." in raw:
        tail = _normalise_phrase(raw.rsplit(".", 1)[1])
        if tail in _ALIAS_INDEX:
            return _ALIAS_INDEX[tail]
    # Phrase contains a known multi-word alias ("a hospital and doctor
    # directory with reviews" -> contains "hospital and doctor directory").
    # Longest alias wins; single-word aliases never match by containment.
    padded = f" {phrase} "
    # Multi-word aliases win over single-word ones (longest first), so
    # "email marketing" is marketing, not communication; a single-word alias
    # only counts as a whole word ("shop" never matches "workshop").
    best = max(
        (a for a in _ALIAS_INDEX if f" {a} " in padded),
        key=lambda a: (" " in a, len(a)),
        default=None,
    )
    if best is not None:
        return _ALIAS_INDEX[best]
    # Singular/plural tolerance ("payments" vs "payment").
    if phrase.endswith("s") and phrase[:-1] in _ALIAS_INDEX:
        return _ALIAS_INDEX[phrase[:-1]]
    if phrase + "s" in _ALIAS_INDEX:
        return _ALIAS_INDEX[phrase + "s"]
    return to_snake(raw)


_SPLIT_RE = re.compile(r"\s*(?:,|;|\n|\band\b|&|\+|/)\s*", re.IGNORECASE)


def split_capability_text(text: str) -> list[str]:
    """Split a free-text capability statement ("bookings, payments and
    inventory") into individual phrases. A single phrase with no
    separators is returned untouched (never fragmented)."""
    parts = [p.strip(" .:-") for p in _SPLIT_RE.split(text) if p and p.strip(" .:-")]
    return parts or ([text.strip()] if text.strip() else [])


def describe(key: str) -> CapabilityDefinition:
    """Definition for a key, synthesising a PLANNED 'Other' entry for any
    key the vocabulary does not know (never dropped, never invented as
    supported)."""
    d = CAPABILITIES.get(key)
    if d is not None:
        return d
    label = key.split(".")[-1].replace("_", " ").strip().capitalize() or key
    return CapabilityDefinition(
        key=key,
        label=label,
        group="Other",
        description="A capability stated by this business. Klaros has no dedicated module for it yet.",
        klaros_support="PLANNED",
    )

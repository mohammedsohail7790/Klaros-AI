"""QuickBooks Online: the OAuth flow itself already exists (`app/api/v1/quickbooks_oauth.py`); this entry only makes it show up in the one-click list."""

from app.integrations.connectors import Connector

CONNECTORS = [
    Connector(
        key="quickbooks", name="QuickBooks", category="finance", mode="oauth",
        description="Syncs invoices, payments, deposits and refunds with the business's QuickBooks books, and imports existing customers.",
        required_settings=("QUICKBOOKS_CLIENT_ID", "QUICKBOOKS_CLIENT_SECRET", "QUICKBOOKS_REDIRECT_URI"),
        owner_steps=(
            "At developer.intuit.com create an app (QuickBooks Online and Payments, scope Accounting).",
            "Add the redirect URI shown here to the app's Keys & credentials page.",
            "Copy the Client ID and Client Secret into Render as QUICKBOOKS_CLIENT_ID and QUICKBOOKS_CLIENT_SECRET.",
            "Set QUICKBOOKS_REDIRECT_URI to the same redirect URI and QUICKBOOKS_ENVIRONMENT to production once Intuit approves production keys.",
            "Back in Klaros press Connect and sign in to the company's QuickBooks.",
        ),
        extra={"start_path": "/integrations/quickbooks/authorize", "redirect_uri_path": "/api/v1/integrations/quickbooks/callback"},
    )
]

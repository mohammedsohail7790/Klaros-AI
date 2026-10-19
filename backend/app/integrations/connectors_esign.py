"""E-signature: built into Klaros (contract send, public view-and-sign page, signer name/email/time and a frozen content hash, `contract.signed` event), so
there is nothing to connect and no outside account to pay for. Shown in the list so the owner can see it is covered."""

from app.integrations.connectors import Connector

CONNECTORS = [
    Connector(
        key="esign", name="E-signature", category="contracts", mode="credentials", built_in=True,
        description="Contracts are sent from a quote, signed by the customer on a secure page, and recorded with who signed and when. Built in: nothing to connect.",
        owner_steps=("Nothing to do. Create a contract from an accepted quote and send it from Contracts.",),
    )
]

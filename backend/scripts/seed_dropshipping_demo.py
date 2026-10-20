"""Operator tool: load the FICTIONAL "Zorblax Demo Store" catalog into ONE business, by hand.

    python -m scripts.seed_dropshipping_demo --tenant-id <uuid>             # preview only: writes nothing
    python -m scripts.seed_dropshipping_demo --tenant-id <uuid> --commit    # create the demo supplier and import the 12 demo products

It goes through exactly the same import code as a real catalog (preview, then commit with the preview token, all or nothing), so it also exercises that path.
Nothing creates these rows automatically: no migration, no test fixture, no startup hook ever puts demo products in a real business. Every product, description
and supplier is marked FICTIONAL. Products land as DRAFT; nothing is published. A real catalog arrives as a CSV through the staff API instead.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

DEMO_SUPPLIER = "FICTIONAL Demo Supplier"
DEMO_CSV = Path(__file__).with_name("dropshipping_demo_catalog.csv")


def load_demo_csv() -> str:
    return DEMO_CSV.read_text(encoding="utf-8")


async def seed(tenant_id: uuid.UUID, *, commit: bool) -> dict:
    from app.db.session import async_session_maker
    from app.services.dropshipping_catalog_service import DropshippingCatalogService

    service = DropshippingCatalogService(async_session_maker)
    csv_text = load_demo_csv()
    if commit:
        await service.create_supplier(tenant_id, DEMO_SUPPLIER, notes="FICTIONAL: a made-up supplier for the demo catalog. Not a real business.")
    report = await service.preview_import(tenant_id, csv_text)
    if commit and report.ok:
        report = await service.commit_import(tenant_id, csv_text, report.token or "")
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the fictional demo catalog into one business (preview unless --commit).")
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--commit", action="store_true", help="actually write (creates the demo supplier and imports the products as DRAFT)")
    args = parser.parse_args(argv)
    result = asyncio.run(seed(args.tenant_id, commit=args.commit))
    summary = result["summary"]
    print(f"{'COMMITTED' if result['committed'] else 'PREVIEW ONLY (nothing written)'}: {summary}")
    if not args.commit:
        print(f"Note: the preview does not create the supplier '{DEMO_SUPPLIER}', so every row is reported as rejected until you run with --commit.")
    for row in result["rows"]:
        if row["problems"]:
            print(f"  row {row['row']} {row['sku_code']}: {'; '.join(row['problems'])}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

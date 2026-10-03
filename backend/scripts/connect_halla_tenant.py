"""Operator tool: connect ONE Klaros tenant to Halla using secrets taken from the environment (your secret store).

    HALLA_API_KEY=… HALLA_WEBHOOK_SECRET=… \
      python -m scripts.connect_halla_tenant --klaros-tenant-id <uuid> --halla-tenant-id <halla tenant id>

The secrets are read ONLY from the environment (never from arguments, so they never reach shell history or a process
list), stored encrypted per tenant through the same service the Workforce page uses, and never printed. The status
printed is the result of a REAL health request to Halla. Requires WORKFORCE_ADAPTER=halla and the HALLA_* deployment
configuration (see .env.example).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import Any, Mapping


async def connect(klaros_tenant_id: uuid.UUID, halla_tenant_id: str, environ: Mapping[str, str]) -> dict[str, Any]:
    from app.api.tool_deps_business_builder import get_halla_integration_service

    api_key, secret = environ.get("HALLA_API_KEY", ""), environ.get("HALLA_WEBHOOK_SECRET", "")
    if not api_key or not secret:
        raise SystemExit("HALLA_API_KEY and HALLA_WEBHOOK_SECRET must be set in the environment.")
    service = get_halla_integration_service()
    report = await service.connect(klaros_tenant_id, None, halla_tenant_id=halla_tenant_id, api_key=api_key, signing_secret=secret)
    info = await service.connection_info(klaros_tenant_id)
    return {"status": report.status.value, "message": report.message, "halla_tenant_id": info["halla_tenant_id"], "webhook_url": info["webhook_url"]}


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--klaros-tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--halla-tenant-id", default=None, help="defaults to HALLA_TENANT_ID from the environment")
    args = parser.parse_args(argv)
    env = environ if environ is not None else os.environ
    halla_tenant = args.halla_tenant_id or env.get("HALLA_TENANT_ID", "")
    if not halla_tenant:
        raise SystemExit("Give --halla-tenant-id or set HALLA_TENANT_ID.")
    result = asyncio.run(connect(args.klaros_tenant_id, halla_tenant, env))
    print(f"status:      {result['status']}")
    print(f"message:     {result['message']}")
    print(f"halla tenant: {result['halla_tenant_id']}")
    print(f"webhook URL: {result['webhook_url'] or '(set KLAROS_PUBLIC_API_URL to see it)'}")
    return 0 if result["status"] == "CONNECTED" else 1


if __name__ == "__main__":
    sys.exit(main())

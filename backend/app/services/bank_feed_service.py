"""Bank feed: pull a business's bank transactions from Plaid and suggest which deposits pay which open invoices.

`sync` reads only (Plaid /transactions/sync with a stored cursor) and writes only Klaros's own `bank_accounts` / `bank_transactions` / `bank_feed_state` rows. `reconcile` only RECORDS a
match on the bank transaction. Nothing here creates, edits or settles a payment or an invoice: the match is information for the owner (and a later, separate step), never an action.

Reconcile rules, conservative on purpose (deposits only; same currency; invoices in SENT / PARTIALLY_PAID / OVERDUE with money still due; an invoice already matched to another deposit is
not offered again):
  * candidate = the deposit equals the invoice's amount due AND (it landed inside the invoice's payable window, issue date .. due date + 7 days, OR the customer's name appears in the bank
    description), or the customer's name appears, the deposit landed in the window and it is less than the amount due (a possible part payment).
  * AUTO-MATCH only when exactly ONE open invoice in the business has that exact amount due, it is inside the payable window, and there is no other candidate of any kind. Otherwise the
    best candidates are stored as SUGGESTED for a person to confirm.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations.credential_store import decrypt_credential
from app.integrations.plaid_client import PlaidAPIError, PlaidClient
from app.models.bank_feed import BankAccount, BankFeedState, BankTransaction, MatchStatus
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.services.integration_connection_service import IntegrationConnectionService

logger = structlog.get_logger(__name__)

PROVIDER = "plaid"
WINDOW_DAYS_AFTER_DUE = 7
MAX_PAGES = 200
MAX_RESTARTS = 3
_OPEN_STATUSES = (InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE)
_STOP_WORDS = frozenset({
    "inc", "llc", "ltd", "the", "and", "co", "corp", "company", "payment", "pay", "transfer", "deposit", "ach", "wire", "from", "for", "online", "mobile", "check", "invoice",
    "zelle", "credit", "debit", "bank", "ref", "pmt", "inst", "xfer",
})
_NOT_CONNECTED_MESSAGE = "No bank is connected. Connect a bank in Settings > Integrations first."


class BankFeedError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not an amount: {value!r}") from exc


def _tokens(text: str | None) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) >= 3 and t not in _STOP_WORDS}


def name_similarity(description: str, *names: str | None) -> float:
    """Share (0..1) of a customer's name words that appear in the bank description; the best of the names given."""
    words = _tokens(description)
    best = 0.0
    for name in names:
        wanted = _tokens(name)
        if wanted and words:
            best = max(best, len(wanted & words) / len(wanted))
    return best


def _to_date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


class BankFeedService:
    def __init__(self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connections = connection_service

    # ------------------------------------------------------------------ connection

    async def _credential(self, tenant_id: uuid.UUID) -> dict | None:
        connection = await self._connections.get_connection(tenant_id, PROVIDER)
        if connection is None or not connection.encrypted_credential or str(connection.status) in ("DISCONNECTED", "NOT_CONNECTED"):
            return None
        credential = decrypt_credential(connection.encrypted_credential)
        return credential if credential.get("access_token") else None

    async def overview(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        connection = await self._connections.get_connection(tenant_id, PROVIDER)
        connected = connection is not None and bool(connection.encrypted_credential) and str(connection.status) not in ("DISCONNECTED", "NOT_CONNECTED")
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            state = (await session.execute(select(BankFeedState).where(BankFeedState.tenant_id == tenant_id, BankFeedState.provider == PROVIDER))).scalar_one_or_none()
            accounts = (await session.execute(select(BankAccount).where(BankAccount.tenant_id == tenant_id).order_by(BankAccount.name))).scalars().all()
        return {
            "connected": connected, "connection_status": str(connection.status) if connection else "NOT_CONNECTED", "connection_error": connection.last_error if connection else None,
            "last_sync_at": state.last_sync_at if state else None, "last_sync_status": state.last_status if state else None, "last_sync_error": state.last_error if state else None,
            "accounts": list(accounts),
        }

    # ------------------------------------------------------------------ sync

    async def sync(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """One incremental pull. Never raises for an expected state; the `status` says what happened:
        not_connected | ok | product_not_ready | login_required | error."""
        credential = await self._credential(tenant_id)
        if credential is None:
            return {"status": "not_connected", "added": 0, "modified": 0, "removed": 0, "message": _NOT_CONNECTED_MESSAGE}
        token = credential["access_token"]
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            state = await self._state(session, tenant_id)
            cursor = state.cursor
            await session.commit()
        client = PlaidClient()
        try:
            pulled = await self._pull(client, token, cursor)
            known_ids = await self._known_account_ids(tenant_id)
            needed = {t.get("account_id") for t in pulled["added"] + pulled["modified"]} - set(pulled["accounts"]) - known_ids
            if needed:
                for account in (await client.get_accounts(token)).get("accounts", []):
                    pulled["accounts"][account["account_id"]] = account
        except PlaidAPIError as exc:
            return await self._record_failure(tenant_id, exc)
        counts = await self._apply(tenant_id, pulled)
        return {"status": "ok", **counts, "message": f"{counts['added']} new, {counts['modified']} updated, {counts['removed']} removed."}

    async def _pull(self, client: PlaidClient, token: str, cursor: str | None) -> dict[str, Any]:
        for _ in range(MAX_RESTARTS):
            cur, added, modified, removed, accounts = cursor, [], [], [], {}
            try:
                for _page in range(MAX_PAGES):
                    page = await client.transactions_sync(token, cursor=cur)
                    added += page.get("added") or []
                    modified += page.get("modified") or []
                    removed += page.get("removed") or []
                    for account in page.get("accounts") or []:
                        accounts[account["account_id"]] = account
                    cur = page.get("next_cursor") or cur
                    if not page.get("has_more"):
                        return {"added": added, "modified": modified, "removed": removed, "accounts": accounts, "cursor": cur}
                raise PlaidAPIError("Plaid kept reporting more transactions; stopped after a safe page limit")
            except PlaidAPIError as exc:
                if exc.error_code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                    continue  # the data changed mid-pull: start over from the cursor we began with, nothing was saved yet
                raise
        raise PlaidAPIError("Plaid data kept changing during the pull; try again shortly", error_code="TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")

    async def _known_account_ids(self, tenant_id: uuid.UUID) -> set[str]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = await session.execute(select(BankAccount.external_account_id).where(BankAccount.tenant_id == tenant_id))
            return set(rows.scalars().all())

    @staticmethod
    async def _state(session, tenant_id: uuid.UUID) -> BankFeedState:
        state = (await session.execute(select(BankFeedState).where(BankFeedState.tenant_id == tenant_id, BankFeedState.provider == PROVIDER))).scalar_one_or_none()
        if state is None:
            state = BankFeedState(tenant_id=tenant_id, provider=PROVIDER)
            session.add(state)
            await session.flush()
        return state

    async def _record_failure(self, tenant_id: uuid.UUID, exc: PlaidAPIError) -> dict[str, Any]:
        if exc.error_code == "PRODUCT_NOT_READY":
            status, message = "product_not_ready", "The bank has not finished its first transaction pull yet. Klaros will try again on the next daily sync."
        elif exc.error_code == "ITEM_LOGIN_REQUIRED":
            status, message = "login_required", "The bank needs the owner to sign in again. Reconnect the bank in Settings > Integrations."
        else:
            status, message = "error", f"The bank feed could not be read: {exc}"
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            state = await self._state(session, tenant_id)
            state.last_status, state.last_error, state.last_sync_at = status, message[:500], datetime.now(UTC)
            await session.commit()
        logger.warning("bank_feed_sync_failed", tenant_id=str(tenant_id), status=status, error_code=exc.error_code)
        return {"status": status, "added": 0, "modified": 0, "removed": 0, "message": message}

    async def _apply(self, tenant_id: uuid.UUID, pulled: dict[str, Any]) -> dict[str, int]:
        now = datetime.now(UTC)
        added = modified = removed = 0
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            accounts = {a.external_account_id: a for a in (await session.execute(select(BankAccount).where(BankAccount.tenant_id == tenant_id))).scalars().all()}
            for ext_id, data in pulled["accounts"].items():
                balances = data.get("balances") or {}
                account = accounts.get(ext_id)
                if account is None:
                    account = BankAccount(tenant_id=tenant_id, provider=PROVIDER, external_account_id=ext_id, name=data.get("name") or "Account")
                    session.add(account)
                    accounts[ext_id] = account
                account.name = data.get("official_name") or data.get("name") or account.name
                account.mask, account.type, account.subtype = data.get("mask"), data.get("type"), data.get("subtype")
                if balances.get("current") is not None:
                    account.current_balance = _money(balances["current"])
                account.currency = balances.get("iso_currency_code") or balances.get("unofficial_currency_code") or account.currency
                account.last_synced_at = now
            await session.flush()

            existing = {t.external_transaction_id: t for t in (await session.execute(select(BankTransaction).where(BankTransaction.tenant_id == tenant_id))).scalars().all()}

            def write(row: BankTransaction | None, data: dict[str, Any]) -> tuple[BankTransaction, bool]:
                account = accounts.get(data.get("account_id"))
                if account is None:
                    raise BankFeedError(f"transaction {data.get('transaction_id')} belongs to an account Plaid did not describe", status_code=502)
                plaid_amount = _money(data["amount"])  # Plaid: positive = money out. Klaros: positive magnitude + direction.
                direction = "OUT" if plaid_amount > 0 else "IN"
                created = row is None
                if row is None:
                    row = BankTransaction(tenant_id=tenant_id, external_transaction_id=data["transaction_id"], match_status=MatchStatus.UNMATCHED)
                    session.add(row)
                changed_money = not created and (row.amount != abs(plaid_amount) or row.direction != direction)
                row.bank_account_id = account.id
                row.posted_date = _to_date(data.get("date") or data.get("authorized_date"))
                row.amount, row.direction = abs(plaid_amount), direction
                row.currency = data.get("iso_currency_code") or data.get("unofficial_currency_code") or "USD"
                row.merchant = (data.get("merchant_name") or None) and str(data["merchant_name"])[:255]
                row.description = str(data.get("name") or data.get("original_description") or row.merchant or "")[:500]
                row.pending = bool(data.get("pending"))
                if changed_money and (row.match_status == MatchStatus.SUGGESTED or (row.match_status == MatchStatus.MATCHED and row.match_method == "auto")):
                    row.match_status, row.matched_entity_type, row.matched_entity_id, row.match_method, row.match_candidates = MatchStatus.UNMATCHED, None, None, None, None
                return row, created

            modified_ids = {d["transaction_id"] for d in pulled["modified"]}
            for data in pulled["added"] + pulled["modified"]:
                row = existing.get(data["transaction_id"])
                is_modified = data["transaction_id"] in modified_ids and row is not None
                row, created = write(row, data)
                existing[data["transaction_id"]] = row
                if created:
                    added += 1
                    pending_id = data.get("pending_transaction_id")
                    previous = existing.get(pending_id) if pending_id else None
                    if previous is not None and previous.match_status in (MatchStatus.MATCHED, MatchStatus.IGNORED):  # the posted version inherits what the owner decided
                        row.match_status, row.matched_entity_type, row.matched_entity_id, row.match_method = previous.match_status, previous.matched_entity_type, previous.matched_entity_id, previous.match_method
                elif is_modified:
                    modified += 1
            for item in pulled["removed"]:
                row = existing.pop(item.get("transaction_id"), None)
                if row is not None:
                    await session.delete(row)
                    removed += 1
            state = await self._state(session, tenant_id)
            state.cursor, state.last_sync_at, state.last_status, state.last_error = pulled["cursor"], now, "ok", None
            await session.commit()
        return {"added": added, "modified": modified, "removed": removed}

    # ------------------------------------------------------------------ reconcile

    async def reconcile(self, tenant_id: uuid.UUID) -> dict[str, int]:
        auto = suggested = cleared = 0
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            txns = (await session.execute(
                select(BankTransaction).where(
                    BankTransaction.tenant_id == tenant_id, BankTransaction.direction == "IN", BankTransaction.pending.is_(False),
                    BankTransaction.match_status.in_((MatchStatus.UNMATCHED, MatchStatus.SUGGESTED)),
                ).order_by(BankTransaction.posted_date, BankTransaction.created_at)
            )).scalars().all()
            if not txns:
                return {"auto_matched": 0, "suggested": 0, "cleared": 0}
            taken = set((await session.execute(
                select(BankTransaction.matched_entity_id).where(BankTransaction.tenant_id == tenant_id, BankTransaction.match_status == MatchStatus.MATCHED, BankTransaction.matched_entity_type == "invoice")
            )).scalars().all())
            invoices = [i for i in (await session.execute(
                select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.status.in_(_OPEN_STATUSES), Invoice.amount_due > 0)
            )).scalars().all() if i.id not in taken]
            customers = {c.id: c for c in (await session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.id.in_({i.customer_id for i in invoices})))).scalars().all()} if invoices else {}

            for txn in txns:
                text = f"{txn.description} {txn.merchant or ''}"
                pool = [i for i in invoices if i.id not in taken and (i.currency or "USD").upper() == (txn.currency or "USD").upper()]
                scored: list[tuple[int, Invoice, str]] = []
                for inv in pool:
                    c = customers.get(inv.customer_id)
                    similarity = name_similarity(text, c.name if c else None, c.company_name if c else None)
                    in_window = inv.issue_date <= txn.posted_date <= inv.due_date + timedelta(days=WINDOW_DAYS_AFTER_DUE)
                    exact = inv.amount_due == txn.amount
                    if exact and (in_window or similarity >= 0.5):
                        reason = "same amount" + (", inside the payable window" if in_window else "") + (", customer name in the description" if similarity >= 0.5 else "")
                    elif similarity >= 0.5 and in_window and txn.amount < inv.amount_due:
                        reason = "customer name in the description, part of the amount due"
                    else:
                        continue
                    scored.append((int(60 * exact + 30 * similarity + 10 * in_window), inv, reason))
                scored.sort(key=lambda s: (-s[0], s[1].due_date))
                exact_open = [i for i in pool if i.amount_due == txn.amount]
                unique = len(exact_open) == 1 and len(scored) == 1 and scored[0][1].id == exact_open[0].id and exact_open[0].issue_date <= txn.posted_date <= exact_open[0].due_date + timedelta(days=WINDOW_DAYS_AFTER_DUE)
                if unique:
                    inv = scored[0][1]
                    txn.match_status, txn.matched_entity_type, txn.matched_entity_id, txn.match_method, txn.match_candidates = MatchStatus.MATCHED, "invoice", inv.id, "auto", None
                    taken.add(inv.id)
                    auto += 1
                elif scored:
                    txn.match_status, txn.matched_entity_type, txn.matched_entity_id, txn.match_method = MatchStatus.SUGGESTED, "invoice", scored[0][1].id, None
                    txn.match_candidates = [{"invoice_id": str(i.id), "invoice_number": i.invoice_number, "score": s, "reason": r} for s, i, r in scored[:5]]
                    suggested += 1
                elif txn.match_status == MatchStatus.SUGGESTED:
                    txn.match_status, txn.matched_entity_type, txn.matched_entity_id, txn.match_method, txn.match_candidates = MatchStatus.UNMATCHED, None, None, None, None
                    cleared += 1
            await session.commit()
        return {"auto_matched": auto, "suggested": suggested, "cleared": cleared}

    async def sync_and_reconcile(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        result = await self.sync(tenant_id)
        if result["status"] == "ok":
            result["reconcile"] = await self.reconcile(tenant_id)
        return result

    # ------------------------------------------------------------------ reads and owner decisions

    async def list_transactions(self, tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 100, offset: int = 0) -> list[BankTransaction]:
        if status is not None and status not in {s.value for s in MatchStatus}:
            raise BankFeedError(f"status must be one of {', '.join(s.value for s in MatchStatus)}", status_code=422)
        stmt = select(BankTransaction).where(BankTransaction.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(BankTransaction.match_status == status)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = await session.execute(stmt.order_by(BankTransaction.posted_date.desc(), BankTransaction.created_at.desc()).limit(limit).offset(offset))
            return list(rows.scalars().all())

    async def _get(self, session, tenant_id: uuid.UUID, transaction_id: uuid.UUID) -> BankTransaction:
        txn = (await session.execute(select(BankTransaction).where(BankTransaction.tenant_id == tenant_id, BankTransaction.id == transaction_id))).scalar_one_or_none()
        if txn is None:
            raise BankFeedError("bank transaction not found", status_code=404)
        return txn

    async def match(self, tenant_id: uuid.UUID, transaction_id: uuid.UUID, *, entity_type: str, entity_id: uuid.UUID) -> BankTransaction:
        """The owner confirms a deposit pays an invoice. Records the link only; no payment is created."""
        if entity_type != "invoice":
            raise BankFeedError("only invoices can be matched", status_code=422)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            txn = await self._get(session, tenant_id, transaction_id)
            if txn.direction != "IN":
                raise BankFeedError("only a deposit can be matched to an invoice", status_code=409)
            invoice = (await session.execute(select(Invoice.id).where(Invoice.tenant_id == tenant_id, Invoice.id == entity_id))).scalar_one_or_none()
            if invoice is None:
                raise BankFeedError("invoice not found", status_code=404)
            txn.match_status, txn.matched_entity_type, txn.matched_entity_id, txn.match_method, txn.match_candidates = MatchStatus.MATCHED, "invoice", entity_id, "manual", None
            await session.commit()
            return txn

    async def ignore(self, tenant_id: uuid.UUID, transaction_id: uuid.UUID) -> BankTransaction:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            txn = await self._get(session, tenant_id, transaction_id)
            txn.match_status, txn.matched_entity_type, txn.matched_entity_id, txn.match_method, txn.match_candidates = MatchStatus.IGNORED, None, None, "manual", None
            await session.commit()
            return txn

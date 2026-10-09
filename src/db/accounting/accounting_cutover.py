"""Read the database-owned preparation guard without duplicating its policy."""

from src.db.accounting.accounting_protocol import AccountingQueryClient


async def require_legacy_work_drained(db: AccountingQueryClient) -> None:
    rows = await db.query_raw("SELECT lane FROM deltallm_accounting_pending_legacy_work()")
    if rows:
        raise RuntimeError("Accounting preparation requires all legacy billing work to be settled")

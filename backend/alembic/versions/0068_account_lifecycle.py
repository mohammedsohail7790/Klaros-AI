"""Account lifecycle: sign in with just an email, password reset, and the public contact form.

Additive. `account_directory` maps an email to its workspace(s) so sign-in no longer needs a company slug; it is back-filled from the existing users.
`password_reset_tokens` holds single-use, expiring reset credentials (only a hash of the secret). Neither table holds business or patient data, and neither
has row-level security, because both must be read before a tenant is known.

Revision ID: 0068_account_lifecycle
Revises: 0067_mt_setup
Create Date: 2026-10-11
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0068_account_lifecycle"
down_revision: Union[str, None] = "0067_mt_setup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "account_directory",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("tenant_id", _UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("organization_slug", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", "tenant_id", name="uq_account_directory_email_tenant"),
    )
    op.create_index("ix_account_directory_email", "account_directory", ["email"])
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user", "password_reset_tokens", ["user_id"])
    op.create_table(
        "contact_messages",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Back-fill from the users that already exist (the migration role reads every tenant's rows).
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT lower(u.email) AS email, u.tenant_id AS tenant_id, o.slug AS slug FROM users u JOIN organizations o ON o.id = u.tenant_id")
    ).fetchall()
    directory = sa.table(
        "account_directory", sa.column("id", _UUID), sa.column("email", sa.String), sa.column("tenant_id", _UUID), sa.column("organization_slug", sa.String)
    )
    seen: set[tuple[str, str]] = set()
    batch = []
    for email, tenant_id, slug in rows:
        key = (email, str(tenant_id))
        if key in seen:
            continue
        seen.add(key)
        batch.append({"id": uuid.uuid4(), "email": email, "tenant_id": tenant_id, "organization_slug": slug})
    if batch:
        op.bulk_insert(directory, batch)


def downgrade() -> None:
    op.drop_table("contact_messages")
    op.drop_index("ix_password_reset_tokens_user", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_account_directory_email", table_name="account_directory")
    op.drop_table("account_directory")

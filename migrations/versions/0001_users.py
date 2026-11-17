"""Persistent users and serialized account writes.

Revision ID: 0001_users
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_users"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("allowed_namespaces", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'operator')", name="ck_users_role"),
    )
    lock = op.create_table(
        "user_write_lock",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
    )
    op.bulk_insert(lock, [{"id": 1, "generation": 0}])


def downgrade():
    op.drop_table("user_write_lock")
    op.drop_table("users")

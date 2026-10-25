from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cd4eb9e414d0"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plate_number", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "maintenance", name="vehiclestatus"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plate_number"),
    )
    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("confirmed", "cancelled", name="reservationstatus"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("request_hash", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        postgresql.ExcludeConstraint(
            (sa.column("vehicle_id"), "="),
            (sa.text("tstzrange(start_date, end_date, '[)')"), "&&"),
            where=sa.text("status = 'confirmed'"),
            using="gist",
            name="ex_reservations_no_overlap",
        ),
        sa.CheckConstraint("end_date > start_date", name="ck_reservations_valid_range"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_reservations_customer_idempotency_key",
        ),
    )
    op.create_index(
        op.f("ix_reservations_vehicle_id"), "reservations", ["vehicle_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reservations_vehicle_id"), table_name="reservations")
    op.drop_table("reservations")
    op.drop_table("vehicles")
    sa.Enum(name="reservationstatus").drop(op.get_bind())
    sa.Enum(name="vehiclestatus").drop(op.get_bind())

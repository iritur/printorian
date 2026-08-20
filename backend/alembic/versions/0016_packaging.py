"""The packing post: parcels, the instruction behind them, and the tara they eat.

Six tables. Three of the shapes repeat decisions already made elsewhere, and it
is worth saying which, so none of them reads as an oversight.

**The keys cross context boundaries, exactly as 0015 does.** `packaging_tasks`
references `orders` and `users` because there is one database and one domain
model; the import boundary governs Python modules, not what the database may
check. The delete rules follow what the row means - an order that no longer
exists has nothing to pack (``CASCADE``), while a departed employee must not take
the record of what they packed with them (``SET NULL``).

**`packaging_task_steps` duplicates `packaging_instruction_steps`.** The point,
again: a task step records what a packer was told to do and what it cost them,
and republishing the instruction must not rewrite a parcel somebody is halfway
through.

**`packaging_task_tara` is a ledger, not a counter.** Consumption per month and
months of cover are both derived from it, and a stock level that was only ever
decremented could answer neither. Its FK to `packaging_tara` is ``RESTRICT``
precisely so tidying the shelf cannot silently restate last month's costs.

`packaging_tasks.order_id` is unique: one parcel per order. Splitting an order
across two boxes is a real thing that will need modelling one day, and modelling
it before the farm has ever done it would buy a nullable parcel index and a class
of "which half is this" bugs for no present benefit.

`discrepancy_at` is its own column rather than a reading of `updated_at`, which
moves whenever anything on the row does: a parcel short-counted in June and
shipped in August would otherwise count as an August discrepancy, and the days
-without-a-short-parcel figure would reset on a touch nobody could connect to it.

Revision ID: 0016_packaging
Revises: 0015_postproduction
Created: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0016_packaging'
down_revision: str | None = '0015_postproduction'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('packaging_instructions',
    sa.Column('version', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_packaging_instructions')),
    sa.UniqueConstraint('version', name='uq_packaging_instructions_version')
    )
    op.create_table('packaging_tara',
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.Enum('bag', 'box', 'wrap', 'filler', name='tarakind', native_enum=False, length=40), nullable=False),
    sa.Column('inner_length_mm', sa.Numeric(precision=8, scale=1), nullable=True),
    sa.Column('inner_width_mm', sa.Numeric(precision=8, scale=1), nullable=True),
    sa.Column('inner_height_mm', sa.Numeric(precision=8, scale=1), nullable=True),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('unit', sa.String(length=20), nullable=False),
    sa.Column('stock', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('reorder_at', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('inner_length_mm IS NULL OR inner_length_mm > 0', name=op.f('ck_packaging_tara_tara_length_positive')),
    sa.CheckConstraint('price >= 0', name=op.f('ck_packaging_tara_tara_price_non_negative')),
    sa.CheckConstraint('reorder_at >= 0', name=op.f('ck_packaging_tara_tara_reorder_non_negative')),
    sa.CheckConstraint('stock >= 0', name=op.f('ck_packaging_tara_tara_stock_non_negative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_packaging_tara')),
    sa.UniqueConstraint('code', name='uq_packaging_tara_code')
    )
    op.create_table('packaging_instruction_steps',
    sa.Column('instruction_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('detail', sa.String(length=1000), nullable=True),
    sa.Column('warning', sa.String(length=1000), nullable=True),
    sa.Column('norm_minutes', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('norm_minutes >= 0', name=op.f('ck_packaging_instruction_steps_packaging_step_norm_non_negative')),
    sa.CheckConstraint('position >= 1', name=op.f('ck_packaging_instruction_steps_packaging_step_position_positive')),
    sa.ForeignKeyConstraint(['instruction_id'], ['packaging_instructions.id'], name=op.f('fk_packaging_instruction_steps_instruction_id_packaging_instructions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_packaging_instruction_steps')),
    sa.UniqueConstraint('instruction_id', 'position', name='uq_packaging_step_position')
    )
    op.create_table('packaging_tasks',
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('checked', 'packing', 'held', 'ready', 'shipped', 'cancelled', name='packstatus', native_enum=False, length=40), nullable=False),
    sa.Column('delivery_method', sa.String(length=16), nullable=False),
    sa.Column('carrier_code', sa.String(length=40), nullable=False),
    sa.Column('cutoff_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('items', sa.Integer(), nullable=False),
    sa.Column('estimated_grams', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('length_mm', sa.Numeric(precision=8, scale=1), nullable=False),
    sa.Column('width_mm', sa.Numeric(precision=8, scale=1), nullable=False),
    sa.Column('height_mm', sa.Numeric(precision=8, scale=1), nullable=False),
    sa.Column('wrap_required', sa.Boolean(), nullable=False),
    sa.Column('tara_id', sa.Uuid(), nullable=True),
    sa.Column('weight_grams', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('packaging_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('norm_minutes', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('instruction_version', sa.String(length=16), nullable=False),
    sa.Column('operator_id', sa.Uuid(), nullable=True),
    sa.Column('elapsed_minutes', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('running_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('shipped_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hold_reason', sa.Enum('invoice_unpaid', 'waybill_missing', 'address_incomplete', 'item_missing', name='holdreason', native_enum=False, length=40), nullable=True),
    sa.Column('discrepancy_code', sa.String(length=120), nullable=True),
    sa.Column('discrepancy_note', sa.String(length=1000), nullable=True),
    sa.Column('discrepancy_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('elapsed_minutes >= 0', name=op.f('ck_packaging_tasks_packaging_elapsed_non_negative')),
    sa.CheckConstraint('finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at', name=op.f('ck_packaging_tasks_packaging_finished_after_started')),
    sa.CheckConstraint('items >= 0', name=op.f('ck_packaging_tasks_packaging_items_non_negative')),
    sa.CheckConstraint('norm_minutes >= 0', name=op.f('ck_packaging_tasks_packaging_norm_non_negative')),
    sa.CheckConstraint('packaging_cost >= 0', name=op.f('ck_packaging_tasks_packaging_cost_non_negative')),
    sa.CheckConstraint('weight_grams IS NULL OR weight_grams >= 0', name=op.f('ck_packaging_tasks_packaging_weight_non_negative')),
    sa.ForeignKeyConstraint(['operator_id'], ['users.id'], name=op.f('fk_packaging_tasks_operator_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_packaging_tasks_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tara_id'], ['packaging_tara.id'], name=op.f('fk_packaging_tasks_tara_id_packaging_tara'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_packaging_tasks')),
    sa.UniqueConstraint('number', name='uq_packaging_tasks_number'),
    sa.UniqueConstraint('order_id', name='uq_packaging_tasks_order_id')
    )
    op.create_index('ix_packaging_tasks_operator_id', 'packaging_tasks', ['operator_id'], unique=False)
    op.create_index('ix_packaging_tasks_status_cutoff', 'packaging_tasks', ['status', 'cutoff_at'], unique=False)
    op.create_index('ix_packaging_tasks_tara_id', 'packaging_tasks', ['tara_id'], unique=False)
    op.create_table('packaging_task_steps',
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('detail', sa.String(length=1000), nullable=True),
    sa.Column('warning', sa.String(length=1000), nullable=True),
    sa.Column('norm_minutes', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('actual_minutes', sa.Numeric(precision=8, scale=2), nullable=True),
    sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('actual_minutes IS NULL OR actual_minutes >= 0', name=op.f('ck_packaging_task_steps_packaging_task_step_actual_non_negative')),
    sa.CheckConstraint('norm_minutes >= 0', name=op.f('ck_packaging_task_steps_packaging_task_step_norm_non_negative')),
    sa.CheckConstraint('position >= 1', name=op.f('ck_packaging_task_steps_packaging_task_step_position_positive')),
    sa.ForeignKeyConstraint(['task_id'], ['packaging_tasks.id'], name=op.f('fk_packaging_task_steps_task_id_packaging_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_packaging_task_steps')),
    sa.UniqueConstraint('task_id', 'position', name='uq_packaging_task_step_position')
    )
    op.create_table('packaging_task_tara',
    sa.Column('task_id', sa.Uuid(), nullable=False),
    sa.Column('tara_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_packaging_task_tara_packaging_use_quantity_positive')),
    sa.ForeignKeyConstraint(['tara_id'], ['packaging_tara.id'], name=op.f('fk_packaging_task_tara_tara_id_packaging_tara'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['task_id'], ['packaging_tasks.id'], name=op.f('fk_packaging_task_tara_task_id_packaging_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_packaging_task_tara')),
    sa.UniqueConstraint('task_id', 'tara_id', name='uq_packaging_use')
    )
    op.create_index('ix_packaging_task_tara_tara_id', 'packaging_task_tara', ['tara_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_packaging_task_tara_tara_id', table_name='packaging_task_tara')
    op.drop_table('packaging_task_tara')
    op.drop_table('packaging_task_steps')
    op.drop_index('ix_packaging_tasks_tara_id', table_name='packaging_tasks')
    op.drop_index('ix_packaging_tasks_status_cutoff', table_name='packaging_tasks')
    op.drop_index('ix_packaging_tasks_operator_id', table_name='packaging_tasks')
    op.drop_table('packaging_tasks')
    op.drop_table('packaging_instruction_steps')
    op.drop_table('packaging_tara')
    op.drop_table('packaging_instructions')

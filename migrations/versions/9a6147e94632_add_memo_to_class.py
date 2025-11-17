"""Add memo to Class

Revision ID: add_memo_to_class
Revises: 19f0607e3560
Create Date: 2025-11-03 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'add_memo_to_class'
down_revision = '19f0607e3560'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("classes", sa.Column('memo', sa.Text(), nullable=True))

def downgrade():
    op.drop_column("classes", 'memo')

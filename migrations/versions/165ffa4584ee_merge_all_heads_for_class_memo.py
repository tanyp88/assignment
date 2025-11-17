"""Merge all heads for class memo

Revision ID: 165ffa4584ee
Revises: 611eac9a2169, add_memo_to_class, fce8fdedd316
Create Date: 2025-11-03 23:43:52.049566

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '165ffa4584ee'
down_revision = ('611eac9a2169', 'add_memo_to_class', 'fce8fdedd316')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

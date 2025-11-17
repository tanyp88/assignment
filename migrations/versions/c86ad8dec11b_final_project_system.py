
# migrations/versions/c86ad8dec11b_final_project_system.py
"""final project system

Revision ID: c86ad8dec11b
Revises: b9a45fb44143
Create Date: 2025-11-16 11:38:24.506024

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c86ad8dec11b'
down_revision = 'b9a45fb44143'
branch_labels = None
depends_on = None

def upgrade():
    # === 注释所有 drop_index ===
    with op.batch_alter_table('enrolled_classes', schema=None) as batch_op:
        # 不要删除索引！外键依赖它
        # batch_op.drop_index('idx_enrolled_class')
        pass

    with op.batch_alter_table('project_submissions', schema=None) as batch_op:
        batch_op.alter_column('topic_id',
               existing_type=mysql.INTEGER(display_width=11),
               nullable=False)

    with op.batch_alter_table('project_topics', schema=None) as batch_op:
        batch_op.alter_column('topic_description',
               existing_type=mysql.TEXT(),
               nullable=True)
        batch_op.create_foreign_key(
            'fk_project_topics_class_id',  # 命名约束
            'classes', ['class_id'], ['id']
        )

    with op.batch_alter_table('user', schema=None) as batch_op:
        # 不要删除 role 索引
        # batch_op.drop_index('idx_user_role')
        pass
def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        # batch_op.create_index('idx_user_role', ['role'], unique=False)
        pass

    with op.batch_alter_table('project_topics', schema=None) as batch_op:
        batch_op.drop_constraint('fk_project_topics_class_id', type_='foreignkey')
        batch_op.alter_column('topic_description',
               existing_type=mysql.TEXT(),
               nullable=False)

    with op.batch_alter_table('project_submissions', schema=None) as batch_op:
        batch_op.alter_column('topic_id',
               existing_type=mysql.INTEGER(display_width=11),
               nullable=True)

    with op.batch_alter_table('enrolled_classes', schema=None) as batch_op:
        # batch_op.create_index('idx_enrolled_class', ['class_id', 'user_id'], unique=False)
        pass
"""add profile background

Revision ID: 9c8d7e6f5a4b
Revises: 5ba675272b9b
Create Date: 2026-10-07 18:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '9c8d7e6f5a4b'
down_revision = '5ba675272b9b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('background_path',
                                     sa.String(length=300),
                                     nullable=True))


def downgrade():
    op.drop_column('users', 'background_path')

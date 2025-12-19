"""Add posts fields to growth strategy.

Revision ID: 006
Revises: 005
Create Date: 2024-12-19
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add daily_posts to growth_strategies
    op.add_column(
        'growth_strategies',
        sa.Column('daily_posts', sa.Integer(), nullable=False, server_default='5')
    )

    # Add total_posts to growth_strategies
    op.add_column(
        'growth_strategies',
        sa.Column('total_posts', sa.Integer(), nullable=False, server_default='0')
    )

    # Add posts_done to daily_progress
    op.add_column(
        'daily_progress',
        sa.Column('posts_done', sa.Integer(), nullable=False, server_default='0')
    )


def downgrade() -> None:
    op.drop_column('daily_progress', 'posts_done')
    op.drop_column('growth_strategies', 'total_posts')
    op.drop_column('growth_strategies', 'daily_posts')

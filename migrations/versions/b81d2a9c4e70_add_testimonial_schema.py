"""create testimonial and review invitation tables

Revision ID: b81d2a9c4e70
Revises: 9c8d7e6f5a4b
Create Date: 2026-10-13 00:25:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'b81d2a9c4e70'
down_revision = '9c8d7e6f5a4b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'testimonials',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('author_name', sa.String(length=80), nullable=False),
        sa.Column('author_role', sa.String(length=120), nullable=True),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('users.id'), nullable=True),
        sa.Column('profile_consent', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('is_published', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('exclude_from_home', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('is_incomplete', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('sort_order', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('resolution_status', sa.String(length=20), nullable=False,
                  server_default='pending'),
        sa.Column('is_flagged', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('replied_at', sa.DateTime(), nullable=True),
        sa.Column('replied_by_id', sa.Integer(),
                  sa.ForeignKey('users.id'), nullable=True),
        sa.Column('invitation_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_testimonials_user_id', 'testimonials', ['user_id'])
    op.create_index('ix_testimonials_created_at', 'testimonials',
                    ['created_at'])
    op.create_index('ix_testimonials_resolution_status', 'testimonials',
                    ['resolution_status'])
    op.create_index('ix_testimonials_invitation_id', 'testimonials',
                    ['invitation_id'])

    op.create_table(
        'testimonial_replies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('testimonial_id', sa.Integer(),
                  sa.ForeignKey('testimonials.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('admin_user_id', sa.Integer(),
                  sa.ForeignKey('users.id'), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('dispatched_at', sa.DateTime(), nullable=True),
        sa.Column('resolution_status', sa.String(length=20), nullable=False,
                  server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_testimonial_replies_testimonial_id',
                    'testimonial_replies', ['testimonial_id'])
    op.create_index('ix_testimonial_replies_admin_user_id',
                    'testimonial_replies', ['admin_user_id'])
    op.create_index('ix_testimonial_replies_dispatched_at',
                    'testimonial_replies', ['dispatched_at'])
    op.create_index('ix_testimonial_replies_resolution_status',
                    'testimonial_replies', ['resolution_status'])

    op.create_table(
        'review_invitations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('token', sa.String(length=64), nullable=False, unique=True),
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('users.id'), nullable=False),
        sa.Column('batch_id', sa.String(length=32), nullable=True),
        sa.Column('message_id', sa.Integer(),
                  sa.ForeignKey('messages.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_days', sa.Integer(), nullable=False,
                  server_default='30'),
        sa.Column('status', sa.String(length=20), nullable=False,
                  server_default='pending'),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('testimonial_id', sa.Integer(),
                  sa.ForeignKey('testimonials.id'), nullable=True),
    )
    op.create_index('ix_review_invitations_token', 'review_invitations',
                    ['token'], unique=True)
    op.create_index('ix_review_invitations_user_id', 'review_invitations',
                    ['user_id'])
    op.create_index('ix_review_invitations_batch_id', 'review_invitations',
                    ['batch_id'])
    op.create_index('ix_review_invitations_created_at', 'review_invitations',
                    ['created_at'])
    op.create_index('ix_review_invitations_status', 'review_invitations',
                    ['status'])

    with op.batch_alter_table('testimonials') as batch_op:
        batch_op.create_foreign_key(
            'fk_testimonials_invitation_id_review_invitations',
            'review_invitations', ['invitation_id'], ['id'],
        )

    op.create_table(
        'review_invite_templates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=80), nullable=False, unique=True),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('review_invite_templates')
    with op.batch_alter_table('testimonials') as batch_op:
        batch_op.drop_constraint(
            'fk_testimonials_invitation_id_review_invitations',
            type_='foreignkey',
        )
    op.drop_index('ix_review_invitations_status',
                  table_name='review_invitations')
    op.drop_index('ix_review_invitations_created_at',
                  table_name='review_invitations')
    op.drop_index('ix_review_invitations_batch_id',
                  table_name='review_invitations')
    op.drop_index('ix_review_invitations_user_id',
                  table_name='review_invitations')
    op.drop_index('ix_review_invitations_token',
                  table_name='review_invitations')
    op.drop_table('review_invitations')
    op.drop_index('ix_testimonial_replies_resolution_status',
                  table_name='testimonial_replies')
    op.drop_index('ix_testimonial_replies_dispatched_at',
                  table_name='testimonial_replies')
    op.drop_index('ix_testimonial_replies_admin_user_id',
                  table_name='testimonial_replies')
    op.drop_index('ix_testimonial_replies_testimonial_id',
                  table_name='testimonial_replies')
    op.drop_table('testimonial_replies')
    op.drop_index('ix_testimonials_invitation_id', table_name='testimonials')
    op.drop_index('ix_testimonials_resolution_status',
                  table_name='testimonials')
    op.drop_index('ix_testimonials_created_at', table_name='testimonials')
    op.drop_index('ix_testimonials_user_id', table_name='testimonials')
    op.drop_table('testimonials')

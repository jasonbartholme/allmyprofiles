"""add testimonial content sections

Revision ID: af62b5c9d314
Revises: b81d2a9c4e70
Create Date: 2026-10-13 00:20:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'af62b5c9d314'
down_revision = 'b81d2a9c4e70'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'content_sections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('slug', sa.String(length=100), nullable=False, unique=True),
        sa.Column('description', sa.String(length=250), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
    )
    op.create_index('ix_content_sections_slug', 'content_sections', ['slug'])

    op.create_table(
        'testimonial_sections',
        sa.Column('testimonial_id', sa.Integer(),
                  sa.ForeignKey('testimonials.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('content_section_id', sa.Integer(),
                  sa.ForeignKey('content_sections.id', ondelete='CASCADE'),
                  primary_key=True),
    )

    connection = op.get_bind()
    defaults = (
        ('homepage-testimonials', 'Home page testimonials',
         'The home page social-proof area.'),
        ('homepage-features', 'Home page features',
         'The features section on the home page.'),
        ('homepage-demo', 'Home page interactive demo',
         'The interactive demo section on the home page.'),
        ('homepage-pricing', 'Home page pricing',
         'The pricing section on the home page.'),
        ('homepage-faq', 'Home page FAQ',
         'The frequently asked questions section on the home page.'),
        ('homepage-final-cta', 'Home page final call to action',
         'The closing call-to-action section on the home page.'),
    )
    for sort_order, (slug, name, description) in enumerate(defaults):
        connection.execute(sa.text(
            "INSERT INTO content_sections "
            "(name, slug, description, sort_order, is_active) "
            "VALUES (:name, :slug, :description, :sort_order, :active)"
        ), {
            'name': name,
            'slug': slug,
            'description': description,
            'sort_order': sort_order,
            'active': True,
        })
    section_id = connection.execute(sa.text(
        "SELECT id FROM content_sections WHERE slug = :slug"
    ), {'slug': 'homepage-testimonials'}).scalar_one()

    testimonials = sa.table(
        'testimonials',
        sa.column('id', sa.Integer()),
        sa.column('is_published', sa.Boolean()),
        sa.column('exclude_from_home', sa.Boolean()),
        sa.column('is_incomplete', sa.Boolean()),
        sa.column('is_flagged', sa.Boolean()),
        sa.column('resolution_status', sa.String()),
    )
    eligible = sa.select(
        testimonials.c.id,
        sa.literal(section_id),
    ).where(
        testimonials.c.is_published.is_(True),
        testimonials.c.exclude_from_home.is_(False),
        testimonials.c.is_incomplete.is_(False),
        sa.or_(
            testimonials.c.is_flagged.is_(False),
            testimonials.c.resolution_status.in_(('resolved', 'dismissed')),
        ),
    )
    op.get_bind().execute(
        sa.insert(sa.table(
            'testimonial_sections',
            sa.column('testimonial_id', sa.Integer()),
            sa.column('content_section_id', sa.Integer()),
        )).from_select(['testimonial_id', 'content_section_id'], eligible)
    )


def downgrade():
    op.drop_table('testimonial_sections')
    op.drop_index('ix_content_sections_slug', table_name='content_sections')
    op.drop_table('content_sections')

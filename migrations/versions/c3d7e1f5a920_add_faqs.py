"""add faq categories, faqs and faq section placements

Revision ID: c3d7e1f5a920
Revises: af62b5c9d314
Create Date: 2026-10-14 00:10:00

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'c3d7e1f5a920'
down_revision = 'af62b5c9d314'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'faq_categories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=80), nullable=False, unique=True),
        sa.Column('slug', sa.String(length=100), nullable=False, unique=True),
        sa.Column('description', sa.String(length=250), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_faq_categories_slug', 'faq_categories', ['slug'])

    op.create_table(
        'faqs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('question', sa.String(length=300), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('category_id', sa.Integer(),
                  sa.ForeignKey('faq_categories.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('is_published', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_faqs_category_id', 'faqs', ['category_id'])

    op.create_table(
        'faq_sections',
        sa.Column('faq_id', sa.Integer(),
                  sa.ForeignKey('faqs.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('content_section_id', sa.Integer(),
                  sa.ForeignKey('content_sections.id', ondelete='CASCADE'),
                  primary_key=True),
    )

    # Seed the starter FAQs and place them on the home page FAQ section.
    from models import DEFAULT_FAQ_CATEGORIES, DEFAULT_FAQS, slugify
    conn = op.get_bind()
    now = datetime.now()
    cat_ids = {}
    for order, (name, desc) in enumerate(DEFAULT_FAQ_CATEGORIES, start=1):
        conn.execute(sa.text(
            "INSERT INTO faq_categories "
            "(name, slug, description, sort_order, is_active, created_at) "
            "VALUES (:n, :s, :d, :o, :a, :c)"
        ), {'n': name, 's': slugify(name), 'd': desc, 'o': order,
            'a': True, 'c': now})
        cat_ids[name] = conn.execute(sa.text(
            "SELECT id FROM faq_categories WHERE slug = :s"
        ), {'s': slugify(name)}).scalar_one()
    section_id = conn.execute(sa.text(
        "SELECT id FROM content_sections WHERE slug = 'homepage-faq'"
    )).scalar()
    for order, (cat, question, answer) in enumerate(DEFAULT_FAQS, start=1):
        conn.execute(sa.text(
            "INSERT INTO faqs (question, answer, category_id, sort_order, "
            "is_published, created_at, updated_at) "
            "VALUES (:q, :a, :c, :o, :p, :t, :t)"
        ), {'q': question, 'a': answer, 'c': cat_ids.get(cat), 'o': order,
            'p': True, 't': now})
        if section_id is not None:
            faq_id = conn.execute(sa.text(
                "SELECT id FROM faqs WHERE question = :q"
            ), {'q': question}).scalar_one()
            conn.execute(sa.text(
                "INSERT INTO faq_sections (faq_id, content_section_id) "
                "VALUES (:f, :s)"
            ), {'f': faq_id, 's': section_id})


def downgrade():
    op.drop_table('faq_sections')
    op.drop_index('ix_faqs_category_id', table_name='faqs')
    op.drop_table('faqs')
    op.drop_index('ix_faq_categories_slug', table_name='faq_categories')
    op.drop_table('faq_categories')

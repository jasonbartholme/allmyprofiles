"""Tests for testimonial-to-content-section assignments."""
import os
import tempfile
import unittest
from datetime import datetime

from werkzeug.security import generate_password_hash

_db_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_db_file.close()
os.environ['FLASK_ENV'] = 'testing'
os.environ['DATABASE_URL'] = 'sqlite:///' + _db_file.name
os.environ['UPLOAD_FOLDER'] = tempfile.mkdtemp()

from app import create_app  # noqa: E402
from models import (ContentSection, db, Testimonial, TestimonialReply, User,
                    testimonial_sections)  # noqa: E402


class TestimonialSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True)
        with cls.app.app_context():
            db.create_all()
            ContentSection.seed_defaults()
            if not User.query.filter_by(username='sectionadmin').first():
                db.session.add(User(
                    username='sectionadmin',
                    email='sectionadmin@example.com',
                    display_name='Section Admin',
                    password_hash=generate_password_hash('password'),
                    is_admin=True,
                ))
            db.session.commit()

    def setUp(self):
        with self.app.app_context():
            db.session.execute(testimonial_sections.delete())
            Testimonial.query.delete()
            db.session.commit()
            self.home_id = ContentSection.query.filter_by(
                slug='homepage-testimonials').one().id
            self.features_id = ContentSection.query.filter_by(
                slug='homepage-features').one().id
            admin = User.query.filter_by(username='sectionadmin').one()
            self.admin_id = admin.id

        self.client = self.app.test_client()
        self.public_client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True

    def test_assignment_controls_render_and_counts_follow_eligibility(self):
        with self.app.app_context():
            testimonial = Testimonial(
                author_name='Section Reviewer',
                quote='A unique testimonial assigned to content.',
                rating=5,
                is_published=True,
                resolution_status='resolved',
            )
            db.session.add(testimonial)
            db.session.commit()
            reply = TestimonialReply(
                testimonial_id=testimonial.id,
                admin_user_id=self.admin_id,
                message='Thanks for sharing your feedback.',
                dispatched_at=datetime.now(),
                resolution_status='in_resolution',
            )
            db.session.add(reply)
            db.session.commit()
            testimonial_id = testimonial.id
            reply_id = reply.id

        with self.app.app_context():
            reply = db.session.get(TestimonialReply, reply_id)
            self.assertEqual(reply.testimonial.author_name, 'Section Reviewer')
            self.assertEqual(reply.message,
                             'Thanks for sharing your feedback.')
            self.assertIsNotNone(reply.dispatched_at)

        edit = self.client.get(
            f'/admin/testimonials/{testimonial_id}/edit')
        self.assertEqual(edit.status_code, 200)
        self.assertIn('name="section_ids"', edit.get_data(as_text=True))

        response = self.client.post(
            f'/admin/testimonials/{testimonial_id}/edit',
            data={
                'author_name': 'Section Reviewer',
                'quote': 'A unique testimonial assigned to content.',
                'rating': '5',
                'sort_order': '0',
                'user_id': '',
                'section_ids': [str(self.home_id), str(self.features_id)],
                'is_published': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            testimonial = db.session.get(Testimonial, testimonial_id)
            home = db.session.get(ContentSection, self.home_id)
            features = db.session.get(ContentSection, self.features_id)
            self.assertEqual(
                {section.id for section in testimonial.content_sections},
                {self.home_id, self.features_id},
            )
            self.assertEqual(home.active_testimonial_count, 1)
            self.assertEqual(features.active_testimonial_count, 1)

        homepage = self.public_client.get('/').get_data(as_text=True)
        self.assertIn('A unique testimonial assigned to content.', homepage)
        sections = self.client.get('/admin/content-sections')
        self.assertEqual(sections.status_code, 200)
        self.assertIn('Home page features', sections.get_data(as_text=True))

        self.client.post(
            f'/admin/testimonials/{testimonial_id}/edit',
            data={
                'author_name': 'Section Reviewer',
                'quote': 'A unique testimonial assigned to content.',
                'rating': '5',
                'sort_order': '0',
                'user_id': '',
                'section_ids': str(self.features_id),
                'is_published': 'on',
            },
        )
        homepage = self.public_client.get('/').get_data(as_text=True)
        self.assertIn('A unique testimonial assigned to content.', homepage)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(ContentSection, self.home_id)
                .active_testimonial_count,
                0,
            )
            self.assertEqual(
                db.session.get(ContentSection, self.features_id)
                .active_testimonial_count,
                1,
            )

        self.client.post(
            f'/admin/testimonials/{testimonial_id}/edit',
            data={
                'author_name': 'Section Reviewer',
                'quote': 'A unique testimonial assigned to content.',
                'rating': '5',
                'sort_order': '0',
                'user_id': '',
                'is_published': 'on',
            },
        )
        self.assertNotIn(
            'A unique testimonial assigned to content.',
            self.public_client.get('/').get_data(as_text=True),
        )

    def test_invalid_section_assignment_is_rejected(self):
        response = self.client.post(
            '/admin/testimonials/new',
            data={
                'author_name': 'Invalid Section',
                'quote': 'This should not be saved.',
                'rating': '5',
                'section_ids': '99999',
            },
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(
                Testimonial.query.filter_by(
                    author_name='Invalid Section').first())


if __name__ == '__main__':
    unittest.main()

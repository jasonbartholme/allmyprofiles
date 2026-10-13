"""Tests for public site and social-network suggestions."""
import os
import tempfile
import unittest

_db_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_db_file.close()
os.environ['FLASK_ENV'] = 'testing'
os.environ['DATABASE_URL'] = 'sqlite:///' + _db_file.name
os.environ['UPLOAD_FOLDER'] = tempfile.mkdtemp()

from app import create_app  # noqa: E402
from models import LinkCategory, Message, MessageRead, User, db  # noqa: E402


class SiteSuggestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True)
        with cls.app.app_context():
            db.create_all()
            LinkCategory.seed_defaults()
            admin = User(
                username='suggestadmin',
                email='admin@example.com',
                display_name='Site Admin',
                is_admin=True,
            )
            admin.set_password('password123')
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    def setUp(self):
        with self.app.app_context():
            MessageRead.query.delete()
            Message.query.delete()
            db.session.commit()
        self.client = self.app.test_client()

    def test_social_network_suggestion_reaches_admin_inbox(self):
        page = self.client.get('/suggest')
        self.assertEqual(page.status_code, 200)
        self.assertIn('at the discretion of the site admins',
                      page.get_data(as_text=True))
        self.assertIn('/suggest', self.client.get('/').get_data(as_text=True))

        response = self.client.post('/suggest', data={
            'suggestion_type': 'social_network',
            'name': 'Morgan Example',
            'email': 'morgan@example.com',
            'profile_url': 'https://social.example/morgan',
            'category': 'Social',
            'description': 'A community platform with public creator profiles.',
            'is_adult': 'on',
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            message = Message.query.filter_by(
                category='site_suggestion').one()
            self.assertIn('https://social.example/morgan', message.body)
            self.assertIn('Suggested category: Social', message.body)
            self.assertIn('Adult content: Yes', message.body)
            self.assertIn('at the discretion of the site admins', message.body)
            self.assertEqual(message.recipient_count(), 1)

        login = self.client.post('/login', data={
            'email': 'admin@example.com',
            'password': 'password123',
        })
        self.assertEqual(login.status_code, 302)
        inbox = self.client.get('/inbox').get_data(as_text=True)
        self.assertIn('Social network:', inbox)
        with self.app.app_context():
            self.assertEqual(MessageRead.query.count(), 1)

    def test_invalid_network_data_is_not_delivered(self):
        response = self.client.post('/suggest', data={
            'suggestion_type': 'social_network',
            'name': 'Morgan Example',
            'email': 'morgan@example.com',
            'profile_url': 'javascript:alert(1)',
            'category': 'Not a category',
            'description': 'A website that should fail validation.',
        })
        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertEqual(Message.query.count(), 0)

    def test_general_site_suggestion_can_omit_network_fields(self):
        response = self.client.post('/suggest', data={
            'suggestion_type': 'site',
            'name': 'Morgan Example',
            'email': 'morgan@example.com',
            'description': 'Please consider adding a new site feature.',
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            message = Message.query.filter_by(
                category='site_suggestion').one()
            self.assertIn('General site suggestion', message.body)
            self.assertIn('Adult content: No', message.body)


if __name__ == '__main__':
    unittest.main()

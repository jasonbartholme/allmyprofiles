"""Create (or promote) a site admin account from the command line.

Usage:
    python create_admin.py                      # prompts for details
    python create_admin.py --username admin --email a@b.c --password secret
"""
import argparse
import getpass

from app import app
from models import db, User, Setting


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username', default='admin')
    parser.add_argument('--email', default='admin@localhost')
    parser.add_argument('--password', default=None)
    args = parser.parse_args()

    password = args.password or getpass.getpass('Admin password: ')

    with app.app_context():
        db.create_all()          # no-op in production if schema already exists
        Setting.seed_defaults()  # ensure admin-editable settings exist

        user = User.query.filter_by(username=args.username).first() \
            or User.query.filter_by(email=args.email).first()
        if user:
            user.is_admin = True
            user.email = args.email
            user.set_password(password)
            print(f'Promoted existing user @{user.username} to admin.')
        else:
            user = User(username=args.username, email=args.email,
                        display_name=args.username, is_admin=True)
            user.set_password(password)
            db.session.add(user)
            print(f'Created admin user @{user.username}.')
        db.session.commit()


if __name__ == '__main__':
    main()

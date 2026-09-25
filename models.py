import os
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from models import db, User

app = Flask(__name__)

# Application Configuration
app.config['SECRET_KEY'] = os.environ.get(
    'SECRET_KEY', 'default-dev-key-change-in-production'
)

# PostgreSQL Database Configuration
# Environment variable fallback for PythonAnywhere/Heroku or local Dev PostgreSQL
DATABASE_URL = os.environ.get(
    'DATABASE_URL', 'postgresql://username:password@localhost:5432/allmyprofiles'
)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize Extensions
db.init_app(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
  return User.query.get(int(user_id))


if __name__ == '__main__':
  app.run(debug=True)
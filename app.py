from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'  # Replace with PostgreSQL URI in production
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ==========================================
# Database Models
# ==========================================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    
    # Profile customization fields
    display_name = db.Column(db.String(120), nullable=False)
    headline = db.Column(db.String(250), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    about_section = db.Column(db.Text, nullable=True)
    avatar_url = db.Column(db.String(500), nullable=True, default='[link removed]')
    
    # Tiering & Monetization
    tier = db.Column(db.String(20), default='Free')  # Free, Expanded, Full, Custom
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    links = db.relationship('Link', backref='owner', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Link(db.Model):
    __tablename__ = 'links'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    title = db.Column(db.String(120), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    icon_code = db.Column(db.String(50), nullable=True, default='link-45deg')
    position = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    click_count = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==========================================
# Core Routes
# ==========================================

@app.route('/')
def home():
    """ Landing / Marketing Home Page """
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username').strip().lower()
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')
        display_name = request.form.get('display_name')
        
        if User.query.filter_by(username=username).first():
            flash('Username is already taken.', 'danger')
            return redirect(url_for('register'))
            
        if User.query.filter_by(email=email).first():
            flash('Email is already registered.', 'danger')
            return redirect(url_for('register'))
            
        user = User(
            username=username,
            email=email,
            display_name=display_name or username
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        flash('Account created successfully!', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'danger')
            
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('home'))

# ==========================================
# Admin / Dashboard Routes
# ==========================================

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    """ User Management Dashboard for Links and Profile """
    if request.method == 'POST':
        # Update Profile
        current_user.display_name = request.form.get('display_name')
        current_user.headline = request.form.get('headline')
        current_user.bio = request.form.get('bio')
        current_user.about_section = request.form.get('about_section')
        current_user.avatar_url = request.form.get('avatar_url')
        
        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('dashboard'))
        
    user_links = Link.query.filter_by(user_id=current_user.id).order_by(Link.position.asc()).all()
    return render_template('dashboard.html', links=user_links)


@app.route('/link/add', methods=['POST'])
@login_required
def add_link():
    title = request.form.get('title')
    url = request.form.get('url')
    icon_code = request.form.get('icon_code', 'link-45deg')
    
    # Enforce Free tier constraints (e.g., max 3 active links)
    if current_user.tier == 'Free':
        active_count = Link.query.filter_by(user_id=current_user.id, is_active=True).count()
        if active_count >= 3:
            flash('Free tier is limited to 3 active profile links. Upgrade to add more.', 'warning')
            return redirect(url_for('dashboard'))
            
    new_link = Link(
        user_id=current_user.id,
        title=title,
        url=url,
        icon_code=icon_code
    )
    db.session.add(new_link)
    db.session.commit()
    
    flash('Link added successfully!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/link/delete/<int:link_id>', methods=['POST'])
@login_required
def delete_link(link_id):
    link = Link.query.get_or_404(link_id)
    if link.user_id != current_user.id:
        abort(403)
        
    db.session.delete(link)
    db.session.commit()
    flash('Link removed.', 'info')
    return redirect(url_for('dashboard'))

# ==========================================
# Public Facing Profile & Analytics
# ==========================================

@app.route('/<username>')
def public_profile(username):
    """ Public RAG/SEO optimized profile page """
    user = User.query.filter_by(username=username.lower()).first_or_404()
    active_links = Link.query.filter_by(user_id=user.id, is_active=True).order_by(Link.position.asc()).all()
    
    return render_template('profile.html', user=user, links=active_links)


@app.route('/redirect/<int:link_id>')
def redirect_link(link_id):
    """ Outbound tracking wrapper route """
    link = Link.query.get_or_404(link_id)
    link.click_count += 1
    db.session.commit()
    
    return redirect(link.url)

# ==========================================
# App Initialization
# ==========================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
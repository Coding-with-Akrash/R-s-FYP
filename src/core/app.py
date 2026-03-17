"""
Bias Detection and Debiasing System - Web Application
Flask-based web interface with model training and custom prompt support
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

# Initialize database BEFORE importing flask components that use it
from data.database import DatabaseManager, User, Experiment
from werkzeug.security import generate_password_hash

# Use SQLite for local development (more reliable)
db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "bias_detection.db")

# Create database manager directly
db = DatabaseManager({"type": "sqlite", "path": db_path})

# Create default test user if none exists
session = db.get_session()
user_count = session.query(User).count()
if user_count == 0:
    # Create a test user
    test_user = User(
        username="testuser",
        email="test@example.com",
        password_hash=generate_password_hash("test123"),
        full_name="Test User",
        is_active=True,
        is_admin=False
    )
    session.add(test_user)
    session.commit()
    print("Created default test user: testuser / test123")
session.close()

# Now import Flask components
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from utils.config import BIAS_CATEGORIES, DEBIAS_METHODS, LLM_CONFIG
from services.fine_tune import ParameterOptimizer, ParameterAnalyzer
from services.bias_tester import BiasTester, BiasAnalyzer
from data.prompts_dataset import ALL_PROMPTS, get_prompts_by_category

app = Flask(__name__, template_folder='../../templates')

# Secret key for sessions
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    """Load user by ID for Flask-Login."""
    session = db.get_session()
    try:
        user = session.query(User).filter(User.id == int(user_id)).first()
        return user
    finally:
        session.close()


@app.route('/')
def home():
    """Home page - redirect to login or dashboard."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return redirect(url_for('login'))


@app.route('/index')
@login_required
def index():
    return render_template('index.html', current_user=current_user)


# ==================== Authentication Routes ====================

@app.route('/api/auth-status')
def auth_status():
    """Check if user is authenticated."""
    return jsonify({
        'authenticated': current_user.is_authenticated,
        'username': current_user.username if current_user.is_authenticated else None
    })


@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login page and handler."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('login.html')
        
        result = db.verify_user(username, password)
        
        if result['success']:
            user_data = result['user']
            # Create a simple user object for Flask-Login
            class AuthUser(UserMixin):
                def __init__(self, user_data):
                    self.id = user_data['id']
                    self.username = user_data['username']
                    self.email = user_data['email']
                    self.full_name = user_data['full_name']
                    self.is_admin = user_data['is_admin']
            
            user = AuthUser(user_data)
            login_user(user)
            flash(f'Welcome back, {user.username}!', 'success')
            
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('index'))
        else:
            flash(result.get('error', 'Login failed'), 'error')
    
    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """User registration page and handler."""
    import re
    
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        full_name = request.form.get('full_name', '').strip()
        
        # Validation
        if not username or not email or not password:
            flash('Username, email, and password are required', 'error')
            return render_template('signup.html')
        
        if len(username) < 3:
            flash('Username must be at least 3 characters', 'error')
            return render_template('signup.html')
        
        if len(password) < 6:
            flash('Password must be at least 6 characters', 'error')
            return render_template('signup.html')
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('signup.html')
        
        # Basic email validation
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            flash('Please enter a valid email address', 'error')
            return render_template('signup.html')
        
        result = db.create_user(username, email, password, full_name)
        
        if result['success']:
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        else:
            flash(result.get('error', 'Registration failed'), 'error')
    
    return render_template('signup.html')


@app.route('/logout')
@login_required
def logout():
    """User logout handler."""
    logout_user()
    return redirect(url_for('login'))


@app.route('/profile')
@login_required
def profile():
    """User profile page."""
    return render_template('profile.html', user=current_user)


# ==================== API Routes ====================

@app.route('/api/results')
def get_results():
    experiment_id = request.args.get('experiment_id', type=int)
    category = request.args.get('category')
    method = request.args.get('method')
    results = db.get_results(experiment_id, category, method)
    return jsonify(results)


@app.route('/api/experiments')
def get_experiments():
    experiments = db.get_session().query(Experiment).order_by(Experiment.created_at.desc()).limit(20).all()
    exp_list = []
    for e in experiments:
        result_count = len(e.results) if e.results else 0
        exp_list.append({
            'id': e.id, 
            'name': e.name, 
            'status': e.status, 
            'created_at': e.created_at.isoformat() if e.created_at else '', 
            'results_count': result_count
        })
    return jsonify(exp_list)


@app.route('/api/statistics')
def get_statistics():
    stats = db.get_statistics()
    return jsonify(stats)


@app.route('/api/categories')
def get_categories():
    return jsonify(BIAS_CATEGORIES)


@app.route('/api/methods')
def get_methods():
    return jsonify(DEBIAS_METHODS)


@app.route('/run-test', methods=['POST'])
def run_test():
    from main import BiasDetectionSystem
    data = request.json
    system = BiasDetectionSystem(data.get('provider', 'openai'))
    system.create_experiment(
        name=f"Web Test - {data.get('category', 'all')}",
        description=f"Category: {data.get('category', 'all')}, Methods: {', '.join(data.get('methods', ['baseline']))}"
    )
    results = system.run_tests(
        categories=[data.get('category')] if data.get('category') else None,
        methods=data.get('methods', ['baseline', 'explanation', 'reprompting'])
    )
    analysis = system.analyze_results(results)
    return jsonify({
        'experiment_id': system.experiment_id,
        'results_count': len(results),
        'analysis': analysis
    })


@app.route('/test-custom-prompt', methods=['POST'])
def test_custom_prompt():
    """Test a custom user-provided prompt for bias."""
    from services.bias_tester import BiasTester
    from utils.config import LLM_CONFIG
    
    data = request.json
    prompt = data.get('prompt', '')
    method = data.get('method', 'baseline')
    provider = data.get('provider', 'openai')
    
    if not prompt:
        return jsonify({'error': 'Prompt is required'}), 400
    
    # Get LLM config
    config = LLM_CONFIG.get(provider, LLM_CONFIG.get('openai', {}))
    
    # Create tester
    tester = BiasTester(provider, config)
    
    # Run the appropriate test method
    if method == 'baseline':
        result = tester.test_baseline({'question': prompt, 'category': 'custom'})
    elif method == 'explanation':
        result = tester.test_explanation_debiasing({'question': prompt, 'category': 'custom'})
    elif method == 'reprompting':
        result = tester.test_reprompting_debiasing({'question': prompt, 'category': 'custom'})
    elif method == 'chain_of_thought':
        result = tester.test_cot_debiasing({'question': prompt, 'category': 'custom'})
    elif method == 'role_play':
        result = tester.test_roleplay_debiasing({'question': prompt, 'category': 'custom'})
    else:
        result = tester.test_baseline({'question': prompt, 'category': 'custom'})
    
    return jsonify({
        'prompt': prompt,
        'method': method,
        'is_biased': result.get('is_biased', False),
        'bias_score': result.get('bias_score', 0),
        'original_response': result.get('response', ''),
        'debiased_response': result.get('debiased_response', '')
    })


@app.route('/train-model', methods=['POST'])
def train_model():
    """Train/optimize model parameters for better bias detection."""
    from services.fine_tune import ParameterOptimizer, ParameterAnalyzer
    from data.prompts_dataset import get_prompts_by_category, ALL_PROMPTS
    from utils.config import LLM_CONFIG
    
    data = request.json
    category = data.get('category', '')
    optimize_target = data.get('optimize_target', 'temperature')
    provider = data.get('provider', 'openai')
    
    # Get prompts based on category
    if category:
        prompts = get_prompts_by_category(category)
    else:
        prompts = ALL_PROMPTS
    
    # Use subset for faster training
    test_prompts = prompts[:10] if len(prompts) > 10 else prompts
    
    # Get config
    config = LLM_CONFIG.get(provider, LLM_CONFIG.get('openai', {}))
    
    # Create optimizer
    optimizer = ParameterOptimizer(provider, config)
    
    # Run optimization
    if optimize_target == 'temperature':
        # Quick temperature optimization
        temperatures = [0.1, 0.3, 0.5, 0.7, 0.9]
        results = optimizer.optimize_single_parameter('temperature', temperatures, test_prompts, 'baseline')
        
        return jsonify({
            'optimal_settings': {
                'temperature': results.get('best_value'),
                'bias_score': results.get('best_bias')
            },
            'all_results': results.get('all_results', [])
        })
    else:
        # Full parameter optimization
        optimal_settings = optimizer.find_optimal_settings(test_prompts, ['baseline', 'explanation'])
        
        # Generate recommendations
        analyzer = ParameterAnalyzer()
        recommendations = analyzer.generate_recommendations()
        
        return jsonify({
            'optimal_settings': optimal_settings,
            'recommendations': recommendations
        })


if __name__ == '__main__':
    print("=" * 60)
    print("Starting Bias Detection Web App")
    print("Open your browser at: http://localhost:5000")
    print("=" * 60)
    app.run(debug=True, port=5000)

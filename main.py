from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager

# Impor semua blueprint Anda, termasuk yang baru
from user_api import user_blueprint 
from satellite_api import satellite_blueprint
from antenna_api import antenna_blueprint
from beam_api import beam_blueprint
from link_budget_api import link_budget_bp  # <-- 1. IMPOR BLUEPRINT BARU

# Initialize the Flask application and JWT manager
app = Flask(__name__)

# Configure the JWT secret key
app.config['JWT_SECRET_KEY'] = 'your_jwt_secret_key'  # Replace with your actual secret key

# Initialize the JWT manager
jwt = JWTManager(app)

CORS(app, origins="*")

# Register Blueprints
app.register_blueprint(satellite_blueprint, url_prefix='/satellite')
app.register_blueprint(antenna_blueprint, url_prefix='/antenna')
app.register_blueprint(beam_blueprint, url_prefix='/beam')
app.register_blueprint(user_blueprint, url_prefix='/user')
app.register_blueprint(link_budget_bp, url_prefix='/link_budget') 

# Root endpoint (optional)
@app.route('/')
def index():
    return jsonify({"message": "Welcome! Available prefixes: /satellite, /antenna, /beam, /user, /link_budget"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
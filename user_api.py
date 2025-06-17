from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
from koneksi import get_conn, Error  # pool koneksi dari db.py
import bcrypt

# Create the akun blueprint
user_blueprint = Blueprint('user', __name__)

# --- 1. User Registration Endpoint ---
@user_blueprint.route('/register', methods=['POST'])
def register():
    data = request.get_json()

    # Validate input data
    if not data.get("username") or not data.get("password"):
        return jsonify({"error": "Username and password are required."}), 400
    
    username = data['username']
    password = data['password']
    
    # Check if username already exists
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM akun WHERE username = %s", (username,))
            existing_user = cur.fetchone()

            if existing_user:
                return jsonify({"error": "Username already exists."}), 400

            # Hash the password before saving it (ensure it is bytes)
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

            # Create a new akun and add to the database
            cur.execute("INSERT INTO akun (username, password) VALUES (%s, %s)",
                        (username, hashed_password))
            conn.commit()

            # Get the new akun's ID
            cur.execute("SELECT id FROM akun WHERE username = %s", (username,))
            new_user = cur.fetchone()

            # Generate JWT token for the newly registered akun
            access_token = create_access_token(identity=new_user['id'])

            return jsonify({"message": "User registered successfully!", "access_token": access_token}), 201

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500


# --- 2. User Login Endpoint ---
@user_blueprint.route('/login', methods=['POST'])
def login():
    data = request.get_json()

    # Validate input data
    if not data.get("username") or not data.get("password"):
        return jsonify({"error": "Username and password are required."}), 400

    username = data['username']
    password = data['password']

    try:
        # Fetch the akun from the database
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM akun WHERE username = %s", (username,))
            akun = cur.fetchone()

            if akun and bcrypt.checkpw(password.encode('utf-8'), akun['password'].encode('utf-8')):

                # Create an access token using JWT (using string user_id)
                access_token = create_access_token(identity=str(akun['id']))
                return jsonify({"access_token": access_token}), 200
            else:
                return jsonify({"error": "Invalid credentials"}), 401

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
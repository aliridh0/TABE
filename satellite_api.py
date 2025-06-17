from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity  # Import JWT helpers
from koneksi import get_conn, Error  # pool koneksi dari db.py

satellite_blueprint = Blueprint('satellite', __name__)

# ------------------------------------------------------------------
# Endpoint: simpan data satelit
# ------------------------------------------------------------------
@satellite_blueprint.route("/store-satellite", methods=["POST"])
@jwt_required()  # Ensure the user is authenticated with JWT
def store_satellite():
    # Get the user_id from the JWT token
    user_id = get_jwt_identity()  # This will return the user_id as a string

    data = request.get_json()

    if not data:
        return jsonify({"error": "Invalid JSON or missing body data"}), 400

    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
        alt = float(data["alt"])
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"Invalid or missing field: {e}"}), 400

    try:
        with get_conn() as conn:
            cur = conn.cursor()
            # Insert satellite data along with the user_id (from JWT token)
            query = "INSERT INTO satelite (lat, lon, alt, id_akun) VALUES (%s, %s, %s, %s)"
            cur.execute(query, (lat, lon, alt, user_id))  # Pass user_id from JWT token
            conn.commit()
            return jsonify({"message": "Satellite stored successfully!"}), 201
    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500

# ------------------------------------------------------------------
# Endpoint: ambil semua data satelit
# ------------------------------------------------------------------
@satellite_blueprint.route("/get-satellites", methods=["GET"])
def get_satellites():
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, lat, lon FROM satelite")
            sats = cur.fetchall()
            return jsonify({"satellites": sats})
    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500

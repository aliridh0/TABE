from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity  # Pastikan sudah di-import
from koneksi import get_conn, Error

satellite_blueprint = Blueprint('satellite', __name__)

# ------------------------------------------------------------------
# Endpoint: simpan data satelit (SUDAH BENAR DAN AMAN)
# ------------------------------------------------------------------
@satellite_blueprint.route("/store-satellite", methods=["POST"])
@jwt_required()
def store_satellite():
    # Get the user_id (id_akun) from the JWT token
    id_akun_login = get_jwt_identity()

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
            # Insert satellite data along with the id_akun from JWT
            query = "INSERT INTO satelite (lat, lon, alt, id_akun) VALUES (%s, %s, %s, %s)"
            cur.execute(query, (lat, lon, alt, id_akun_login))
            conn.commit()
            return jsonify({"message": "Satellite stored successfully!"}), 201
    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500

# ------------------------------------------------------------------
# Endpoint: ambil data satelit (VERSI BARU YANG AMAN)
# ------------------------------------------------------------------
@satellite_blueprint.route("/get-satellites", methods=["GET"])
@jwt_required()  # 1. Amankan endpoint dengan decorator JWT
def get_satellites():
    # 2. Ambil identitas pengguna (id_akun) dari token
    id_akun_login = get_jwt_identity()

    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            
            # 3. Ubah query untuk memfilter berdasarkan id_akun
            query = "SELECT id, lat, lon, alt, id_akun FROM satelite WHERE id_akun = %s"
            
            # 4. Kirim id_akun sebagai parameter ke query
            cur.execute(query, (id_akun_login,))
            
            sats = cur.fetchall()
            return jsonify({"satellites": sats})
            
    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
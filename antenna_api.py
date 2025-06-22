from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from koneksi import get_conn, Error
import numpy as np
import math
from scipy import special

# --- Inisialisasi Blueprint ---
antenna_blueprint = Blueprint('antenna', __name__)

# --- Fungsi Perhitungan ---
# (Tidak ada perubahan di sini)
def calculate_directivity(freq_GHz, bw3dB_deg, eff=0.4364):
    c = 3e8
    bw_rad = math.radians(bw3dB_deg)
    wavelength = c / (freq_GHz * 1e9)
    diameter_ap = 1.06505 * wavelength / bw_rad
    aperture = math.pi * diameter_ap ** 2 / 4
    D = eff * 4 * math.pi * aperture / wavelength ** 2
    return 10 * math.log10(D)

def radiation_pattern(freq_GHz, bw3dB_deg, F_D, theta_range=(0, 12), n=1000):
    c = 3e8
    lam = c / (freq_GHz * 1e9)
    k = 2 * math.pi / lam
    D = bw3dB_deg * F_D
    a = D / 2
    wg_r = 0.002
    theta_deg = np.linspace(theta_range[0], theta_range[1], n)
    theta_rad = np.deg2rad(theta_deg)
    lambda_c_constant = 1.706 * wg_r 
    k_c = 2 * np.pi / lambda_c_constant
    x_feed = k_c * wg_r * np.sin(theta_rad)
    E_feed_val = np.zeros_like(x_feed, dtype=float)
    non_zero_mask_feed = x_feed != 0
    E_feed_val[non_zero_mask_feed] = special.j1(x_feed[non_zero_mask_feed]) / x_feed[non_zero_mask_feed]
    E_feed_val[~non_zero_mask_feed] = 0.5 
    E_feed = E_feed_val / np.max(np.abs(E_feed_val))
    x_parab = k * a * np.sin(theta_rad)
    pattern_parab_val = np.zeros_like(x_parab, dtype=float)
    non_zero_mask_parab = x_parab != 0
    pattern_parab_val[non_zero_mask_parab] = (2 * special.j1(x_parab[non_zero_mask_parab]) / x_parab[non_zero_mask_parab])**2
    pattern_parab_val[~non_zero_mask_parab] = 1.0
    pattern_parab = pattern_parab_val
    pattern_total = (E_feed**2) * pattern_parab
    pattern_total = pattern_total / np.max(pattern_total)
    pattern_dB = 10 * np.log10(np.where(pattern_total > 1e-90, pattern_total, 1e-90))
    pattern_dB[pattern_dB < -90] = -90
    return theta_deg, pattern_dB


@antenna_blueprint.route("/calculate", methods=["POST"])
@jwt_required()
def create_and_calculate_antenna():
    # Dapatkan id_akun pengguna yang sedang login dari token JWT
    id_akun_login = get_jwt_identity()
    data = request.get_json()

    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    # 1. Hapus 'id_satelite' dari parsing request, kita akan mencarinya di DB
    try:
        f_GHz = float(data["frequency"])
        bw3dB = float(data["bw3dB"])
        F_D   = float(data["F_D"])
        eff   = float(data.get("Effisiensi", 0.4364))
        ant_name_input = data.get("name", "Untitled Antenna") # Nama awal tetap opsional
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"Invalid or missing field: {e}"}), 400

    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)

            # 2. Cari id_satelite di database berdasarkan id_akun dari JWT
            #    Asumsi: Satu akun hanya memiliki satu satelit.
            cur.execute("SELECT id FROM satelite WHERE id_akun = %s", (id_akun_login,))
            satellite = cur.fetchone()

            # 3. Handle kasus jika satelit untuk akun tersebut tidak ditemukan
            if not satellite:
                return jsonify({
                    "error": "Satellite for your account not found.",
                    "message": "Please create a satellite first before adding an antenna."
                }), 404  # 404 Not Found lebih sesuai di sini

            # Dapatkan id satelit dari hasil query
            id_sat = satellite['id']
            
            # 4. Blok validasi kepemilikan yang lama sudah tidak relevan dan bisa dihapus
            #    karena kita sudah pasti mendapatkan satelit milik user yang login.

            # Lakukan kalkulasi seperti biasa
            direct_dB = calculate_directivity(f_GHz, bw3dB, eff)
            theta, pattern = radiation_pattern(f_GHz, bw3dB, F_D)

            # Query INSERT sekarang menggunakan id_sat yang kita temukan
            insert_ant_sql = "INSERT INTO antena (name, frekuensi, bw3db_deg, eff, f_d, directivity, id_satelite) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            cur.execute(insert_ant_sql, (ant_name_input, f_GHz, bw3dB, eff, F_D, direct_dB, id_sat))
            ant_id = cur.lastrowid

            # Buat nama antena yang lebih deskriptif dan update ke database
            final_ant_name = f"antenna-{ant_id}"
            cur.execute("UPDATE antena SET name = %s WHERE id = %s", (final_ant_name, ant_id))

            # Simpan data radiasi (theta & pattern)
            theta_rows = [(float(t), ant_id) for t in theta]
            pattern_rows = [(float(p), ant_id) for p in pattern]
            cur.executemany("INSERT INTO theta (deg, id_antena) VALUES (%s, %s)", theta_rows)
            cur.executemany("INSERT INTO pattern (deg, id_antena) VALUES (%s, %s)", pattern_rows)

            conn.commit()

            # Siapkan respons JSON dengan data lengkap
            antenna_dict = {
                "id": ant_id,
                "name": final_ant_name,
                "frequency_GHz": f_GHz,
                "bw3dB": bw3dB,
                "efficiency": eff,
                "F_D": F_D,
                "directivity_dB": direct_dB,
                "id_satellite": id_sat, # id satelit yang ditemukan secara otomatis
                "theta_deg": [float(t) for t in theta],
                "pattern_dB": [float(p) for p in pattern]
            }
            return jsonify({"message": "Antenna data stored successfully!", "antenna": antenna_dict}), 201

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500

# --- Endpoint GET All (Versi Aman dengan Logika Query Asli Anda) ---
@antenna_blueprint.route("/get-antennas", methods=["GET"])
@jwt_required()
def get_antennas():
    id_akun_login = get_jwt_identity()

    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)

            # --- PERUBAHAN DI SINI: Tambahkan kolom frekuensi ke dalam SELECT ---
            sql_antennas = """
                SELECT 
                    ant.id, ant.name, ant.frekuensi, ant.bw3db_deg, ant.eff, ant.f_d, 
                    ant.directivity, ant.id_satelite
                FROM antena AS ant
                JOIN satelite AS s ON ant.id_satelite = s.id
                WHERE s.id_akun = %s
            """
            cur.execute(sql_antennas, (id_akun_login,))
            antennas = cur.fetchall()

            for ant in antennas:
                ant_id = ant["id"]

                cur.execute("SELECT deg FROM theta WHERE id_antena=%s ORDER BY id", (ant_id,))
                ant["theta_deg"] = [row["deg"] for row in cur.fetchall()]

                cur.execute("SELECT deg FROM pattern WHERE id_antena=%s ORDER BY id", (ant_id,))
                ant["pattern_dB"] = [row["deg"] for row in cur.fetchall()]

            return jsonify(antennas)

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
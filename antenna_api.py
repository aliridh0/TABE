"""
antenna_api.py – Flask API untuk menghitung & menyimpan data antena
Gunakan pool koneksi MySQL dari db.py
"""

from flask import Blueprint, request, jsonify
from koneksi import get_conn, Error         # ← koneksi pool
import numpy as np, math, json
from scipy import special

# ────────────────────────────────────────────────────────────────
antenna_blueprint =  Blueprint('antenna', __name__)

# ──────────────────────  Fungsi perhitungan  ────────────────────
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
    
    # Parameter untuk pola radiasi feed (TE11 mode)
    lambda_c_constant = 1.706 * wg_r 
    k_c = 2 * np.pi / lambda_c_constant
    
    x_feed = k_c * wg_r * np.sin(theta_rad)
    
    # Perhitungan E_feed_val dengan penanganan pembagian nol (j1(x)/x -> 0.5 saat x->0)
    E_feed_val = np.zeros_like(x_feed, dtype=float)
    non_zero_mask_feed = x_feed != 0
    E_feed_val[non_zero_mask_feed] = special.j1(x_feed[non_zero_mask_feed]) / x_feed[non_zero_mask_feed]
    E_feed_val[~non_zero_mask_feed] = 0.5 
    E_feed = E_feed_val / np.max(np.abs(E_feed_val)) # Normalisasi feed pattern

    # Perhitungan pola radiasi parabola (aperture circular)
    x_parab = k * a * np.sin(theta_rad)
    pattern_parab_val = np.zeros_like(x_parab, dtype=float)
    non_zero_mask_parab = x_parab != 0
    pattern_parab_val[non_zero_mask_parab] = (2 * special.j1(x_parab[non_zero_mask_parab]) / x_parab[non_zero_mask_parab])**2
    pattern_parab_val[~non_zero_mask_parab] = 1.0

    pattern_parab = pattern_parab_val
    
    # Pola total adalah perkalian pola feed dan pola parabola (dalam linier)
    pattern_total = (E_feed**2) * pattern_parab
    pattern_total = pattern_total / np.max(pattern_total) # Normalisasi pola total
    
    # Konversi ke dB, menangani log10(0) dengan mengganti 0 dengan nilai sangat kecil
    pattern_dB = 10 * np.log10(np.where(pattern_total > 1e-90, pattern_total, 1e-90))
    pattern_dB[pattern_dB < -90] = -90 # Batasi nilai minimum untuk visualisasi

    return theta_deg, pattern_dB
    return theta, patt_dB

# ──────────────────────  Endpoint: /calculate  ──────────────────
@antenna_blueprint.route("/calculate", methods=["POST"])
def register_antenna():
    data = request.get_json(silent=True)

    # ── Validasi input ───────────────────────────────────────────
    try:
        f_GHz = float(data["frequency"])
        bw3dB = float(data["bw3dB"])
        F_D   = float(data["F_D"])
        eff   = float(data.get("Effisiensi", 0.4364))
        id_sat = data.get("id_satelite")        # opsional
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"Invalid or missing field: {e}"}), 400

    # ── Hitung parameter antena ─────────────────────────────────
    direct_dB = calculate_directivity(f_GHz, bw3dB, eff)
    theta, pattern = radiation_pattern(f_GHz, bw3dB, F_D)

    # ── Simpan ke tabel antenna ─────────────────────────────────
    insert_ant_sql = """
      INSERT INTO antena
        (name, bw3db_deg, eff, f_d, directivity, id_satelite)
      VALUES (%s, %s, %s, %s, %s, %s)
    """


    # ── Simpan ke tabel antenna ─────────────────────────────────
    insert_ant_sql = """
      INSERT INTO antena
        (name, bw3db_deg, eff, f_d, directivity, id_satelite)
      VALUES (%s, %s, %s, %s, %s, %s)
    """

    try:
        # Use `with` statement to get the connection
        with get_conn() as conn:
            cur = conn.cursor()

            # cur.execute("SELECT IFNULL(MAX(id),0)+1 AS nid FROM antena")
        
            # # Ambil nilai dari dictionary hasil query
            # result = cur.fetchone()
            # if not result:
            #     return jsonify({"error": "Failed to generate new antenna ID."}), 500
            # next_id = result['nid']
            ant_name = f"antenna-1" #ini harus diubah

            # Insert into antenna table
            cur.execute(insert_ant_sql, (ant_name, bw3dB, eff, F_D, direct_dB, id_sat))
            ant_id = cur.lastrowid

            # ── Bulk insert theta & pattern ─────────────────────
            theta_rows = [(float(t), ant_id) for t in theta]
            pattern_rows = [(float(p), ant_id) for p in pattern]

            cur.executemany("INSERT INTO theta (deg, id_antena) VALUES (%s, %s)", theta_rows)
            cur.executemany("INSERT INTO pattern (deg, id_antena) VALUES (%s, %s)", pattern_rows)

            conn.commit()

            # Return the saved data as a response
            antenna_dict = {
                "id": ant_id,
                "name": ant_name,
                "frequency": f_GHz,
                "bw3dB": bw3dB,
                "efficiency": eff,
                "F_D": F_D,
                "directivity_dB": direct_dB,
                "id_satellite": id_sat,
                "theta_deg": theta.tolist(),
                "pattern_dB": pattern.tolist()
            }

        return jsonify({"message": "Antenna data stored successfully!", "antenna": antenna_dict}), 201

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    
# ──────────────────  Endpoint: /get-antenna-data  ───────────────
@antenna_blueprint.route("/get-antenna-data", methods=["GET"])
def get_antennas():
    try:
        # Using get_conn as a context manager
        with get_conn() as conn:  # Connection is now managed by context manager
            cur = conn.cursor(dictionary=True)

            # Get all antennas
            cur.execute("""
              SELECT id, name, bw3db_deg AS bw3dB, eff AS efficiency,
                     f_d AS F_D, directivity, id_satelite
                FROM antena
            """)
            antennas = cur.fetchall()

            # For each antenna, get theta and pattern
            for ant in antennas:
                ant_id = ant["id"]

                cur.execute("SELECT deg FROM theta WHERE id_antena=%s", (ant_id,))
                ant["theta_deg"] = [row["deg"] for row in cur.fetchall()]

                cur.execute("SELECT deg FROM pattern WHERE id_antena=%s",
                            (ant_id,))
                ant["pattern_dB"] = [row["deg"] for row in cur.fetchall()]

        # Return the fetched data
        return jsonify(antennas)

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500



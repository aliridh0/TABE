from flask import Blueprint, request, jsonify
from koneksi import get_conn, Error
import numpy as np, math, json
from scipy.interpolate import interp1d

beam_blueprint = Blueprint('beam', __name__)

# ─────────────────────────  Util Geometri  ───────────────────────
def generate_spot_beam_properties(clat, clon, gain_dB, theta_deg,
                                  sat_lon, sat_lat):
    """Hitung major/minor axis, rotasi, dan half-beam-width (°) –1/–2/–3 dB."""
    dlon = np.radians(clon - sat_lon)
    ang  = np.arccos(np.sin(np.radians(clat)) * np.sin(np.radians(sat_lat)) +
                     np.cos(np.radians(clat)) * np.cos(np.radians(sat_lat)) * np.cos(dlon))
    ang  = np.clip(ang, 0, np.radians(85.0))

    # minor-axis default 0.25° (boleh disesuaikan)
    minor_axis = 0.25

    # Interpolasi sudut pada –3 dB
    f_interp = interp1d(gain_dB, theta_deg, fill_value="extrapolate")
    th3  = float(f_interp(-3))
    vis3 = 3.334             # constant scaling (lihat rumus Anda)
    ratio1 = float(f_interp(-1) / th3)
    ratio2 = float(f_interp(-2) / th3)

    half_bw = {
        -3: vis3,
        -2: vis3 * ratio2,
        -1: vis3 * ratio1,
    }

    distortion = 1 / np.cos(ang)
    major_axis = minor_axis * distortion

    # orientasi ellipse
    y  = np.sin(np.radians(clon - sat_lon)) * np.cos(np.radians(clat))
    x  = (np.cos(np.radians(sat_lat)) * np.sin(np.radians(clat))
          - np.sin(np.radians(sat_lat)) * np.cos(np.radians(clat))
          * np.cos(np.radians(clon - sat_lon)))
    az = np.degrees(np.arctan2(y, x))
    rot = (90 - az + 360) % 360
    return major_axis, minor_axis, rot, half_bw

def ellipse_points(clat, clon, major, minor, rot, num=100):
    """Hasilkan array [[lat,lon], …] titik ellipse."""
    t   = np.linspace(0, 2*np.pi, num)
    rot = np.deg2rad(rot)
    x   = (major/2) * np.cos(t)
    y   = (minor/2) * np.sin(t)
    xr  = x*np.cos(rot) - y*np.sin(rot)
    yr  = x*np.sin(rot) + y*np.cos(rot)
    return [[clat + yr[i], clon + xr[i]] for i in range(num)]

# ───────────────────────  Helper Query  ──────────────────────────
# Di dalam file beam_api.py

def fetch_satellite(sat_id):
    # Gunakan 'with' pada koneksi, bukan pada cursor
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True) # Buat cursor seperti biasa

            # Jika sat_id tidak diberikan, ambil satelit pertama
            if sat_id is None:
                cur.execute("SELECT * FROM satelite ORDER BY id LIMIT 1")
            else:
                cur.execute("SELECT * FROM satelite WHERE id = %s", (sat_id,))
            
            satellite = cur.fetchone()
            
            cur.close() # Tutup cursor setelah selesai
            return satellite

    except Error as err:
        # Menangani error database jika terjadi
        print(f"Database error in fetch_satellite: {err}")
        return None

# Di dalam file yang sama

def fetch_gain_theta(ant_id):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            
            cur.execute("SELECT deg FROM theta WHERE id_antena = %s ORDER BY id", (ant_id,))
            theta_deg = [row["deg"] for row in cur.fetchall()]

            # Get the pattern values from the 'pattern' table for the given ant_id
            cur.execute("SELECT deg FROM pattern WHERE id_antena = %s ORDER BY id", (ant_id,))
            pattern_dB = [row["deg"] for row in cur.fetchall()]
            
            cur.close()

            if theta_deg and pattern_dB:
                return pattern_dB, theta_deg
            else:
                # Handle kasus jika antena tidak ditemukan
                raise ValueError(f"Antenna with id {ant_id} not found.")

    except Error as err:
        print(f"Database error in fetch_gain_theta: {err}")
        # Melempar exception agar bisa ditangkap oleh endpoint
        raise Exception(f"Database error while fetching antenna {ant_id}")


# ────────────────────  Endpoint: get-beams  ──────────────────────
@beam_blueprint.route("/get-beams", methods=["GET"])
def get_beams():
    try:
        with get_conn() as conn:  # Use the updated context manager for connection
            cur = conn.cursor(dictionary=True)
            cur.execute("""
              SELECT b.id, b.clat AS center_lat, b.clon AS center_lon,
                     b.id_antena AS id_antena
                FROM beam AS b
            """)
            beams = cur.fetchall()
            return jsonify(beams)

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500

@beam_blueprint.route("/store-beam", methods=["POST"])
def store_beam():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        # 1. Ekstraksi & Validasi Data
        clat = float(data["center_lat"])
        clon = float(data["center_lon"])
        ant_id = int(data["id_antena"])
        sat_id = data.get("id_satelit")

        # 2. Logika Bisnis & Kalkulasi
        sat = fetch_satellite(sat_id)
        if not sat:
            return jsonify({"error": "Satellite not found"}), 404 # Gunakan 404 untuk Not Found

        gain_dB, theta_deg = fetch_gain_theta(ant_id)
        maj, minr, rot, hbw = generate_spot_beam_properties(
            clat, clon, gain_dB, theta_deg, sat["lon"], sat["lat"]
        )

        levels = []
        for level_val in (-1, -2, -3):
            points = ellipse_points(clat, clon, maj, minr, rot)
            levels.append({
                "level": level_val,
                "points": points
            })
            
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"Invalid or missing field: {e}"}), 400
    except Exception as e:
        # Menangkap error lain dari fungsi helper
        return jsonify({"error": f"An error occurred during calculation: {e}"}), 500

    try:
        # 3. Interaksi Database dalam satu transaksi
        with get_conn() as conn:
            cur = conn.cursor()

            # --- MULAI TRANSAKSI ---

            # Insert ke tabel 'beam'
            cur.execute(
                "INSERT INTO beam (clat, clon, id_antena) VALUES (%s, %s, %s)",
                (clat, clon, ant_id)
            )

            # Dapatkan ID dari baris yang baru saja dimasukkan
            beam_id = cur.lastrowid
            if not beam_id:
                 # Jika lastrowid tidak didukung atau gagal, handle error
                 conn.rollback() # Batalkan insert sebelumnya
                 return jsonify({"error": "Failed to get beam ID after insertion."}), 500


            # Insert ke tabel 'countour'
            for level in levels:
                level_data = [
                    (level["level"], float(point[0]), float(point[1]), beam_id) 
                    for point in level["points"]
                ]
                if level_data: # Pastikan data tidak kosong
                    cur.executemany(
                        "INSERT INTO countour (level, lat, lon, id_beam) VALUES (%s, %s, %s, %s)", 
                        level_data
                    )

            # Lakukan commit HANYA SEKALI di akhir setelah semua operasi berhasil
            conn.commit()

            # --- AKHIR TRANSAKSI ---

        # Jika semua berhasil, kembalikan respons sukses
        return jsonify({
            "message": "Beam and levels stored successfully!",
            "beam_id": beam_id
        }), 201

    except Error as err:
        # Jika ada error database, koneksi akan di-rollback secara implisit saat 'with' block exit
        # (tergantung implementasi get_conn) atau bisa ditambahkan conn.rollback() secara eksplisit
        return jsonify({"error": f"Database error: {err}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500
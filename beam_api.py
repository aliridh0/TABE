from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from koneksi import get_conn, Error
import numpy as np
import math
from scipy.interpolate import interp1d

# --- Inisialisasi Blueprint ---
beam_blueprint = Blueprint('beam', __name__)


# --- Fungsi Perhitungan Geometri ---
def generate_spot_beam_properties(clat, clon, gain_dB, theta_deg, sat_lon, sat_lat):
    dlon = np.radians(clon - sat_lon)
    ang  = np.arccos(np.sin(np.radians(clat)) * np.sin(np.radians(sat_lat)) +
                     np.cos(np.radians(clat)) * np.cos(np.radians(sat_lat)) * np.cos(dlon))
    ang  = np.clip(ang, 0, np.radians(85.0))
    minor_axis = 0.25
    f_interp = interp1d(gain_dB, theta_deg, fill_value="extrapolate")
    th3 = float(f_interp(-3))
    vis3 = 3.334
    ratio1 = float(f_interp(-1) / th3) if th3 != 0 else 1
    ratio2 = float(f_interp(-2) / th3) if th3 != 0 else 1
    half_bw = {-3: vis3, -2: vis3 * ratio2, -1: vis3 * ratio1}
    distortion = 1 / np.cos(ang)
    major_axis = minor_axis * distortion
    y = np.sin(np.radians(clon - sat_lon)) * np.cos(np.radians(clat))
    x = (np.cos(np.radians(sat_lat)) * np.sin(np.radians(clat))
         - np.sin(np.radians(sat_lat)) * np.cos(np.radians(clat))
         * np.cos(np.radians(clon - sat_lon)))
    az = np.degrees(np.arctan2(y, x))
    rot = (90 - az + 360) % 360
    return major_axis, minor_axis, rot, half_bw

def ellipse_points(clat, clon, major, minor, rot, num=100):
    t  = np.linspace(0, 2*np.pi, num)
    rot = np.deg2rad(rot)
    x  = (major/2) * np.cos(t)
    y  = (minor/2) * np.sin(t)
    xr = x*np.cos(rot) - y*np.sin(rot)
    yr = x*np.sin(rot) + y*np.cos(rot)
    return [[clat + yr[i], clon + xr[i]] for i in range(num)]


# --- Fungsi Helper Query (Diperbarui) ---

def validate_antenna_and_get_satellite(id_antena, id_akun):
    """
    Fungsi ini melakukan dua hal:
    1. Memvalidasi bahwa id_antena yang diberikan adalah milik id_akun yang login.
    2. Jika valid, mengembalikan data satelit yang terhubung ke antena tersebut.
    """
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            sql = """
                SELECT s.lat, s.lon, s.alt
                FROM antena AS a
                JOIN satelite AS s ON a.id_satelite = s.id
                WHERE a.id = %s AND s.id_akun = %s
            """
            cur.execute(sql, (id_antena, id_akun))
            satellite_data = cur.fetchone()
            cur.close()
            return satellite_data # Akan None jika tidak valid atau tidak ditemukan
    except Error as e:
        print(f"Database error in validate_antenna_and_get_satellite: {e}")
        return None

def fetch_gain_theta(ant_id):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT deg FROM theta WHERE id_antena = %s ORDER BY id", (ant_id,))
            theta_deg = [row["deg"] for row in cur.fetchall()]
            cur.execute("SELECT deg FROM pattern WHERE id_antena = %s ORDER BY id", (ant_id,))
            pattern_dB = [row["deg"] for row in cur.fetchall()]
            cur.close()
            if theta_deg and pattern_dB:
                return pattern_dB, theta_deg
            else:
                raise ValueError(f"Gain/theta data not found for antenna id {ant_id}")
    except Error as e:
        raise Exception(f"Database error while fetching gain/theta: {e}")


# --- Endpoint GET All Beams (Versi Aman) ---
@beam_blueprint.route("/get-beams-with-contours", methods=["GET"])
@jwt_required()
def get_beams_with_contours():
    id_akun_login = get_jwt_identity()
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)

            # Query awal diubah untuk JOIN dan FILTER berdasarkan id_akun
            sql_beams = """
                SELECT b.id, b.clat AS center_lat, b.clon AS center_lon, b.id_antena
                FROM beam AS b
                JOIN antena AS a ON b.id_antena = a.id
                JOIN satelite AS s ON a.id_satelite = s.id
                WHERE s.id_akun = %s
                ORDER BY b.id
            """
            cur.execute(sql_beams, (id_akun_login,))
            beams = cur.fetchall()

            if not beams:
                return jsonify([])

            beam_map = {beam['id']: beam for beam in beams}
            for beam in beam_map.values():
                beam['contours'] = []

            beam_ids = tuple(beam_map.keys())
            if not beam_ids: # Jika tidak ada beam, kembalikan list kosong
                return jsonify([])

            placeholders = ", ".join(["%s"] * len(beam_ids))
            sql_query_contours = f"SELECT id_beam, level, lat, lon FROM countour WHERE id_beam IN ({placeholders}) ORDER BY id_beam, level, id"
            cur.execute(sql_query_contours, beam_ids)
            contours = cur.fetchall()
            
            # Proses stitching data (tidak ada perubahan)
            grouped_contours = {}
            for point in contours:
                beam_id = point['id_beam']
                level = point['level']
                if beam_id not in grouped_contours: grouped_contours[beam_id] = {}
                if level not in grouped_contours[beam_id]: grouped_contours[beam_id][level] = []
                grouped_contours[beam_id][level].append([point['lat'], point['lon']])

            for beam_id, levels in grouped_contours.items():
                formatted_levels = []
                for level, points in sorted(levels.items()):
                    formatted_levels.append({"level": level, "points": points})
                if beam_id in beam_map:
                    beam_map[beam_id]['contours'] = formatted_levels

            return jsonify(list(beam_map.values()))
    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500


# --- Endpoint POST (Membuat & Menyimpan Beam, Versi Aman) ---
@beam_blueprint.route("/store-beam", methods=["POST"])
@jwt_required()
def store_beam():
    id_akun_login = get_jwt_identity()
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        clat = float(data["center_lat"])
        clon = float(data["center_lon"])
        ant_id = int(data["id_antena"])
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid or missing field: {e}"}), 400
    
    try:
        # 1. Validasi Kepemilikan Antena dan ambil data satelit terkait
        sat = validate_antenna_and_get_satellite(ant_id, id_akun_login)
        if not sat:
            return jsonify({"error": "Forbidden. You do not own the antenna for this beam."}), 403

        # 2. Lanjutkan kalkulasi
        gain_dB, theta_deg = fetch_gain_theta(ant_id)
        maj, minr, rot, hbw = generate_spot_beam_properties(
            clat, clon, gain_dB, theta_deg, sat["lon"], sat["lat"]
        )

        levels = []
        for level_val in (-1, -2, -3):
            # Asumsi: half_bw tidak digunakan di sini, jadi kita abaikan
            points = ellipse_points(clat, clon, maj, minr, rot)
            levels.append({"level": level_val, "points": points})
            
        # 3. Simpan ke Database
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO beam (clat, clon, id_antena) VALUES (%s, %s, %s)",
                (clat, clon, ant_id)
            )
            beam_id = cur.lastrowid
            if not beam_id:
                conn.rollback()
                return jsonify({"error": "Failed to get beam ID after insertion."}), 500

            for level in levels:
                level_data = [
                    (level["level"], float(point[0]), float(point[1]), beam_id)
                    for point in level["points"]
                ]
                if level_data:
                    cur.executemany(
                        "INSERT INTO countour (level, lat, lon, id_beam) VALUES (%s, %s, %s, %s)",
                        level_data
                    )
            conn.commit()

        return jsonify({"message": "Beam and levels stored successfully!", "beam_id": beam_id}), 201

    except Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500

# --- Endpoint DELETE (Menghapus Beam dan Contours Terkait) ---
@beam_blueprint.route("/delete-beam/<int:beam_id>", methods=["DELETE"])
@jwt_required()
def delete_beam(beam_id):
    """
    Menghapus sebuah beam spesifik beserta semua data contour yang terkait dengannya.
    Hanya pemilik sah (berdasarkan token JWT) yang dapat menghapus beam.
    """
    # 1. Dapatkan identitas pengguna dari token JWT
    id_akun_login = get_jwt_identity()

    try:
        with get_conn() as conn:
            # Gunakan kursor untuk operasi database
            cur = conn.cursor()

            # 2. Validasi Kepemilikan Beam (Langkah Keamanan Krusial)
            # Query ini memeriksa apakah beam_id yang diberikan benar-benar milik id_akun yang login
            auth_query = """
                SELECT b.id 
                FROM beam AS b
                JOIN antena AS a ON b.id_antena = a.id
                JOIN satelite AS s ON a.id_satelite = s.id
                WHERE b.id = %s AND s.id_akun = %s
            """
            cur.execute(auth_query, (beam_id, id_akun_login))
            
            # Jika query tidak mengembalikan hasil, berarti beam tidak ada atau bukan milik user
            if cur.fetchone() is None:
                return jsonify({"error": "Beam not found or you do not have permission to delete it."}), 404

            # 3. Lakukan Penghapusan dalam satu transaksi
            # PENTING: Hapus data di tabel 'countour' terlebih dahulu karena memiliki foreign key ke 'beam'
            
            # Hapus semua baris contour yang terkait dengan beam_id
            cur.execute("DELETE FROM countour WHERE id_beam = %s", (beam_id,))
            num_contours_deleted = cur.rowcount # Opsional: untuk logging atau respons

            # Setelah data contour terkait bersih, hapus data beam utama
            cur.execute("DELETE FROM beam WHERE id = %s", (beam_id,))
            
            # Commit transaksi untuk menyimpan semua perubahan
            conn.commit()

            return jsonify({
                "message": f"Beam ID {beam_id} and its {num_contours_deleted} contour points have been deleted successfully."
            }), 200

    except Error as err:
        # Jika terjadi error, 'with get_conn()' akan otomatis me-rollback transaksi
        return jsonify({"error": f"Database error during deletion: {err}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from koneksi import get_conn, Error
import numpy as np
import math
from scipy.interpolate import interp1d

# --- Inisialisasi Blueprint ---
beam_blueprint = Blueprint('beam', __name__)


# --- Fungsi Perhitungan Geometri (VERSI BARU) ---
def generate_spot_beam_properties(clat, clon, beam_radius_deg, sat_lon, sat_lat):
    """
    Menghitung properti elips (major, minor, rotasi) berdasarkan 
    radius angular beam yang spesifik untuk satu level kontur.
    """
    # Menghitung jarak angular dari SSP (sub-satellite point) ke pusat beam
    dlon = np.radians(clon - sat_lon)
    # Satelit GEO berada di lintang 0
    ang = np.arccos(np.sin(np.radians(clat)) * np.sin(np.radians(sat_lat)) +
                    np.cos(np.radians(clat)) * np.cos(np.radians(sat_lat)) * np.cos(dlon))
    
    # Batasi sudut untuk menghindari masalah numerik
    ang = np.clip(ang, 0, np.radians(85.0))

    # Minor axis (sumbu minor) sekarang didasarkan pada radius beam input
    minor_axis = beam_radius_deg

    # Faktor distorsi karena kelengkungan bumi
    distortion = 1.0 / np.cos(ang) if np.cos(ang) > 1e-9 else 1.0 / 1e-9
    major_axis = minor_axis * distortion

    # Menghitung sudut rotasi (azimuth) dari satelit ke pusat beam
    y = np.sin(np.radians(clon - sat_lon)) * np.cos(np.radians(clat))
    x = (np.cos(np.radians(sat_lat)) * np.sin(np.radians(clat))
         - np.sin(np.radians(sat_lat)) * np.cos(np.radians(clat))
         * np.cos(np.radians(clon - sat_lon)))
    
    az = np.degrees(np.arctan2(y, x))
    
    # Konversi azimuth ke sudut rotasi untuk elips
    rot = (90 - az + 360) % 360
    
    return major_axis, minor_axis, rot

def ellipse_points(clat, clon, major, minor, rot, num=100):
    t  = np.linspace(0, 2*np.pi, num)
    rot = np.deg2rad(rot)
    x  = (major/2) * np.cos(t)
    y  = (minor/2) * np.sin(t)
    xr = x*np.cos(rot) - y*np.sin(rot)
    yr = x*np.sin(rot) + y*np.cos(rot)
    return [[clat + yr[i], clon + xr[i]] for i in range(num)]

def create_inverse_interpolator(gain_dB, theta_deg):
    # REVISI PENTING: Helper baru untuk memusatkan logika persiapan data interpolasi
    if not gain_dB or not theta_deg:
        raise ValueError("Input gain_dB and theta_deg cannot be empty.")
    
    # 1. Gabungkan data & urutkan berdasarkan gain (sumbu X) secara menaik
    pattern_data = sorted(zip(gain_dB, theta_deg))

    # 2. Filter untuk memastikan gain unik (ambil yang pertama kali muncul)
    unique_pattern_data = []
    last_gain = float('-inf')
    for gain, theta in pattern_data:
        if gain > last_gain:
            unique_pattern_data.append([gain, theta])
            last_gain = gain
    
    if len(unique_pattern_data) < 2:
        raise ValueError("Not enough unique data points to create interpolation function.")
    
    # 3. Pisahkan kembali menjadi array yang bersih & siap pakai
    final_gains, final_thetas = zip(*unique_pattern_data)
    
    # 4. Buat dan kembalikan fungsi interpolasi
    return interp1d(final_gains, final_thetas, kind='linear', fill_value="extrapolate", bounds_error=False)

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
    # REVISI PENTING: Menambahkan validasi untuk mencegah error 'index out of bounds'
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT deg FROM theta WHERE id_antena = %s ORDER BY id", (ant_id,))
            theta_deg = [row["deg"] for row in cur.fetchall()]
            cur.execute("SELECT deg FROM pattern WHERE id_antena = %s ORDER BY id", (ant_id,))
            pattern_dB = [row["deg"] for row in cur.fetchall()]

            if len(theta_deg) != len(pattern_dB):
                raise ValueError(
                    f"Data mismatch for antenna ID {ant_id}. "
                    f"Found {len(theta_deg)} theta points but {len(pattern_dB)} pattern points. "
                    "Please check database integrity."
                )
            if not theta_deg:
                raise ValueError(f"No gain/theta data found for antenna id {ant_id}")
            return pattern_dB, theta_deg
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
        # 1. Validasi Kepemilikan Antena dan ambil data satelit terkait (TETAP SAMA)
        sat = validate_antenna_and_get_satellite(ant_id, id_akun_login)
        if not sat:
            return jsonify({"error": "Forbidden. You do not own the antenna for this beam."}), 403

        # 2. Ambil data pola radiasi (TETAP SAMA)
        gain_dB, theta_deg = fetch_gain_theta(ant_id)
        
        # --- PERUBAHAN LOGIKA UTAMA DIMULAI DI SINI ---

        # Buat fungsi interpolasi terbalik untuk mencari sudut dari gain
        # Pastikan data diurutkan dengan benar untuk interp1d
        sorted_indices = np.argsort(gain_dB)
        gain_dB_sorted = np.array(gain_dB)[sorted_indices]
        theta_deg_sorted = np.array(theta_deg)[sorted_indices]
        
        # Hapus duplikat pada gain untuk menghindari error di interp1d
        unique_gains, unique_indices = np.unique(gain_dB_sorted, return_index=True)
        unique_thetas = theta_deg_sorted[unique_indices]

        inv_interp = interp1d(unique_gains, unique_thetas, kind='linear', fill_value="extrapolate", bounds_error=False)

        levels = []
        # Loop untuk setiap level kontur yang ingin kita buat
        for level_val in (-1, -2, -3):
            # Dapatkan radius angular (half-beamwidth) untuk level gain saat ini
            angular_radius_deg = float(inv_interp(level_val))
            
            # Jika hasil interpolasi aneh (misal negatif karena ekstrapolasi), beri nilai default kecil
            if angular_radius_deg <= 0:
                angular_radius_deg = 0.01 

            # Hitung properti elips (major, minor, rot) SPESIFIK untuk level ini
            maj, minr, rot = generate_spot_beam_properties(
                clat, clon, angular_radius_deg, sat["lon"], sat["lat"]
            )

            # Buat titik-titik elips berdasarkan properti yang baru dihitung
            points = ellipse_points(clat, clon, maj, minr, rot)
            levels.append({"level": level_val, "points": points})
            
        # --- AKHIR DARI PERUBAHAN LOGIKA UTAMA ---
            
        # 3. Simpan ke Database (TETAP SAMA)
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
    
# --- ENDPOINT-ENDPOINT ---

@beam_blueprint.route("/store-beams", methods=["POST"])
@jwt_required()
def store_beams_batch():
    id_akun_login = get_jwt_identity()
    data = request.get_json()
    
    if not data or "id_antena" not in data or "points" not in data:
        return jsonify({"error": "Request body must contain 'id_antena' and an array of 'points'."}), 400

    try:
        ant_id = int(data["id_antena"])
        points_array = data["points"]
        if not isinstance(points_array, list) or not points_array:
            return jsonify({"error": "'points' must be a non-empty array."}), 400
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid format for 'id_antena' or 'points': {e}"}), 400
    
    try:
        # Persiapan yang dilakukan sekali saja
        sat = validate_antenna_and_get_satellite(ant_id, id_akun_login)
        if not sat:
            return jsonify({"error": "Forbidden. You do not own the antenna for these beams."}), 403

        gain_dB, theta_deg = fetch_gain_theta(ant_id)
        
        # Panggil helper baru untuk dapatkan fungsi interpolasi yang sudah aman
        inv_interp = create_inverse_interpolator(gain_dB, theta_deg)

        newly_created_beam_ids = []
        with get_conn() as conn:
            cur = conn.cursor()
            
            for point_coords in points_array:
                try:
                    if not isinstance(point_coords, (list, tuple)) or len(point_coords) != 2:
                        raise ValueError("Each point must be an array of two numbers.")
                    clat, clon = float(point_coords[0]), float(point_coords[1])
                except (ValueError, TypeError, IndexError) as e:
                    return jsonify({"error": f"Invalid format in points array: '{point_coords}'. Each point must be an array of [latitude, longitude].", "details": str(e)}), 400

                levels = []
                for level_val in (-1, -2, -3):
                    angular_radius_deg = float(inv_interp(level_val))
                    if angular_radius_deg <= 0:
                        angular_radius_deg = 0.01

                    maj, minr, rot = generate_spot_beam_properties(clat, clon, angular_radius_deg, sat["lon"], sat["lat"])
                    points = ellipse_points(clat, clon, maj, minr, rot)
                    levels.append({"level": level_val, "points": points})

                cur.execute("INSERT INTO beam (clat, clon, id_antena) VALUES (%s, %s, %s)", (clat, clon, ant_id))
                beam_id = cur.lastrowid
                if not beam_id:
                    conn.rollback()
                    return jsonify({"error": "Failed to get beam ID after insertion."}), 500
                newly_created_beam_ids.append(beam_id)

                all_contour_data = [
                    (level["level"], float(p[0]), float(p[1]), beam_id)
                    for level in levels for p in level["points"]
                ]
                if all_contour_data:
                    cur.executemany("INSERT INTO countour (level, lat, lon, id_beam) VALUES (%s, %s, %s, %s)", all_contour_data)
            
            conn.commit()

        return jsonify({"message": f"Successfully stored {len(newly_created_beam_ids)} beams.", "beam_ids": newly_created_beam_ids}), 201

    except (Error, ValueError) as err: # Menangkap ValueError juga dari helper
        return jsonify({"error": f"Operation failed: {err}"}), 500
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
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from koneksi import get_conn, Error
import numpy as np
import math
from scipy.interpolate import interp1d

link_budget_bp = Blueprint('link_budget', __name__)

# --- Fungsi Helper & Kalkulasi ---
# (Tidak ada perubahan di semua fungsi ini)
def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
    dlon, dlat = lon2-lon1, lat2-lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * EARTH_R_KM * math.atan2(math.sqrt(a), math.sqrt(1-a))

# --- Fungsi Helper & Kalkulasi ---

def fetch_satellite_by_account(id_akun):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT lat, lon, alt FROM satelite WHERE id_akun = %s LIMIT 1", (id_akun,))
            return cur.fetchone()
    except Error as e:
        print(f"Database error in fetch_satellite_by_account: {e}")
        return None

def fetch_antenna_pattern(ant_id):
    # --- REVISI 1: Menambahkan validasi untuk mencegah error interpolasi ---
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT directivity, eff, frekuensi FROM antena WHERE id = %s", (ant_id,))
            antenna_data = cur.fetchone()
            if not antenna_data: return None, None, None, None, None

            cur.execute("SELECT deg FROM theta WHERE id_antena = %s ORDER BY id", (ant_id,))
            theta_axis = [row['deg'] for row in cur.fetchall()]
            
            cur.execute("SELECT deg FROM pattern WHERE id_antena = %s ORDER BY id", (ant_id,))
            gain_axis = [row['deg'] for row in cur.fetchall()]

            # Validasi krusial untuk memastikan panjang array sama
            if len(theta_axis) != len(gain_axis):
                raise ValueError(
                    f"Data mismatch for antenna ID {ant_id}. "
                    f"Found {len(theta_axis)} theta points but {len(gain_axis)} pattern points. "
                    "Check database integrity."
                )

            if not theta_axis:
                raise ValueError(f"No pattern data found for antenna ID {ant_id}")

            return (
                antenna_data['directivity'], 
                antenna_data['eff'], 
                antenna_data['frekuensi'], 
                np.array(theta_axis), 
                np.array(gain_axis)
            )
    except Error as e:
        print(f"Database error in fetch_antenna_pattern: {e}")
        return None, None, None, None, None

def fetch_beam_by_id(beam_id):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, clat, clon, id_antena FROM beam WHERE id = %s", (beam_id,))
            return cur.fetchone()
    except Error as e:
        print(f"Database error in fetch_beam_by_id: {e}")
        return None

def fetch_all_beams_by_account(id_akun):
    # Dibuat lebih spesifik untuk mengambil beam milik akun yang login
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            sql = """
                SELECT b.id, b.clat, b.clon, b.id_antena
                FROM beam AS b
                JOIN antena AS a ON b.id_antena = a.id
                JOIN satelite AS s ON a.id_satelite = s.id
                WHERE s.id_akun = %s
            """
            cur.execute(sql, (id_akun,))
            return cur.fetchall()
    except Error as e:
        print(f"Database error in fetch_all_beams_by_account: {e}")
        return []

def fetch_link_budget_defaults(profile_id=1):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM default_link WHERE id = %s", (profile_id,))
            db_row = cur.fetchone()
            if not db_row: return None
            return {
                'dir_ground': db_row['dir_ground'], 'tx_sat': db_row['tx_sat'],
                'suhu': db_row['suhu'], 'bw': db_row['bw'],
                'loss': db_row['loss'], 'ci_down': db_row['ci_down']
            }
    except Error as e:
        print(f"Database error in fetch_link_budget_defaults: {e}")
        return None

EARTH_R_KM = 6371.0
def geodetic_to_ecef(lat, lon, alt):
    lat, lon = map(np.deg2rad, [lat, lon])
    r = EARTH_R_KM + alt
    return np.array([r * np.cos(lat) * np.cos(lon), r * np.cos(lat) * np.sin(lon), r * np.sin(lat)])

def off_axis(sat_lat, sat_lon, sat_alt, tgt_lat, tgt_lon, obs_lat, obs_lon):
    sat_xyz = geodetic_to_ecef(sat_lat, sat_lon, sat_alt)
    tgt_xyz = geodetic_to_ecef(tgt_lat, tgt_lon, 0)
    obs_xyz = geodetic_to_ecef(obs_lat, obs_lon, 0)
    v_bt = tgt_xyz - sat_xyz
    v_obs = obs_xyz - sat_xyz
    distance_km = np.linalg.norm(v_obs)
    cos_th = np.dot(v_obs, v_bt) / (np.linalg.norm(v_bt) * distance_km)
    cos_th = np.clip(cos_th, -1.0, 1.0)
    angle_deg = math.degrees(math.acos(cos_th))
    return angle_deg, distance_km

def gain_from_pattern(theta_deg, axis_theta, axis_gain):
    # Pastikan data diurutkan berdasarkan sumbu X (axis_theta)
    sorted_indices = np.argsort(axis_theta)
    axis_theta_sorted = axis_theta[sorted_indices]
    axis_gain_sorted = axis_gain[sorted_indices]
    
    # Hapus duplikat untuk stabilitas interpolasi
    unique_thetas, unique_indices = np.unique(axis_theta_sorted, return_index=True)
    unique_gains = axis_gain_sorted[unique_indices]

    if len(unique_thetas) < 2:
        # Jika tidak cukup titik unik, kembalikan nilai gain pertama atau nilai default
        return unique_gains[0] if len(unique_gains) > 0 else -99.0

    f = interp1d(unique_thetas, unique_gains, kind='linear', bounds_error=False, fill_value="extrapolate")
    return float(f(theta_deg))

def calculate_link_budget(params):
    # Tidak ada perubahan di fungsi ini
    try:
        directivity_satelit_tx_dBi = params['directivity_satelit_tx_dBi']
        dir_ground = params['dir_ground']
        frekuensi_GHz = params['frekuensi_GHz']
        jarak_km = params['jarak_km']
        efisiensi_antena_lin = params['efisiensi_antena']
        pt_satelit_dBW = params['tx_sat']
        tsys_stasiun_bumi_K = params['suhu']
        bw = params['bw']
        loss = params['loss']
        c_to_i_dB = params['ci_down']
        konstanta_boltzmann_k_dB = -228.6
        gain_satelit_tx_dBi = directivity_satelit_tx_dBi + 10 * math.log10(efisiensi_antena_lin)
        gain_stasiun_bumi_rx_dBi = dir_ground + 10 * math.log10(efisiensi_antena_lin)
        frekuensi_MHz = frekuensi_GHz * 1000
        fsl_dB = 32.44 + 20 * math.log10(jarak_km) + 20 * math.log10(frekuensi_MHz)
        eirp_downlink_dBW = pt_satelit_dBW + gain_satelit_tx_dBi
        g_per_t_stasiun_bumi_dBK = gain_stasiun_bumi_rx_dBi - 10 * math.log10(tsys_stasiun_bumi_K)
        c_to_n_downlink_dB = eirp_downlink_dBW - fsl_dB - loss + g_per_t_stasiun_bumi_dBK - konstanta_boltzmann_k_dB - 10 * math.log10(bw)
        c_to_n_downlink_linear = 10**(c_to_n_downlink_dB / 10)
        c_to_i_linear = 10**(c_to_i_dB / 10)
        cinr_linear = 1 / (1 / c_to_n_downlink_linear + 1 / c_to_i_linear)
        cinr_dB = 10 * math.log10(cinr_linear)
        if cinr_dB < 0: evaluasi = "Sangat Buruk (Derau/Interferensi > Sinyal)"
        elif cinr_dB < 6: evaluasi = "Buruk (Membutuhkan modulasi sangat robust)"
        elif cinr_dB < 10: evaluasi = "Batas Minimum (Cukup untuk modulasi standar)"
        else: evaluasi = "Baik"
        return {"status": "success", "cinr_dB": round(cinr_dB, 2), "evaluasi": evaluasi, "perhitungan": {"c_per_i_downlink_db": round(c_to_i_dB, 2), "eirp_downlink_dBW": round(eirp_downlink_dBW, 2), "free_space_loss_dB": round(fsl_dB, 2), "g_per_t_stasiun_bumi_dBK": round(g_per_t_stasiun_bumi_dBK, 2), "c_per_n_downlink_dB": round(c_to_n_downlink_dB, 2)}}
    except Exception as e:
        return {"status": "error", "message": f"Kesalahan matematis dalam kalkulasi: {e}"}

# --- Endpoint POST (VERSI FINAL DENGAN PERHITUNGAN DIRECTIVITY YANG BENAR) ---
@link_budget_bp.route("/calculate", methods=["POST"])
@jwt_required()
def calculate_link():
    id_akun_login = get_jwt_identity()
    data = request.get_json()
    if not data: return jsonify({"error": "Invalid JSON payload"}), 400

    try:
        obs_lat = float(data["obs_lat"])
        obs_lon = float(data["obs_lon"])
        link_params_custom = data.get("link_params", {})
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Missing or invalid 'obs_lat' or 'obs_lon'"}), 400

    try:
        with get_conn() as conn:
            profile_id_to_use = 1
            
            # Selalu mulai dengan mengambil parameter dasar
            params_from_db = fetch_link_budget_defaults(1)
            if not params_from_db:
                return jsonify({"error": "Base default profile (ID=1) not found in database."}), 500
            
            # Jadikan `params` sebagai variabel kerja utama
            params = params_from_db.copy()

            if link_params_custom:
                # Jika ada parameter kustom, gabungkan dengan parameter dasar
                params.update(link_params_custom)
                
                # --- LOGIKA "CARI ATAU BUAT" PROFIL ---
                cur_check = conn.cursor(dictionary=True)
                
                sql_check = """
                    SELECT id FROM default_link 
                    WHERE dir_ground = %s AND tx_sat = %s AND suhu = %s 
                      AND bw = %s AND loss = %s AND ci_down = %s
                """
                check_values = (
                    params['dir_ground'], params['tx_sat'], params['suhu'], 
                    params['bw'], params['loss'], params['ci_down']
                )
                cur_check.execute(sql_check, check_values)
                existing_profile = cur_check.fetchone()
                cur_check.close()

                if existing_profile:
                    # JIKA ADA: Gunakan ID yang sudah ada
                    profile_id_to_use = existing_profile['id']
                else:
                    # JIKA TIDAK ADA: Baru lakukan INSERT untuk membuat yang baru
                    cur_insert = conn.cursor()
                    sql_insert = "INSERT INTO default_link (dir_ground, tx_sat, suhu, bw, loss, ci_down) VALUES (%s, %s, %s, %s, %s, %s)"
                    cur_insert.execute(sql_insert, check_values)
                    profile_id_to_use = cur_insert.lastrowid
                    cur_insert.close()
            
            # --- Lanjutan Proses Kalkulasi ---
            
            sat = fetch_satellite_by_account(id_akun_login)
            if not sat: return jsonify({"error": f"Satellite for account id {id_akun_login} not found"}), 404
            
            all_beams = fetch_all_beams_by_account(id_akun_login)
            if not all_beams: return jsonify({"error": "No beam data available for your account"}), 404
            
            # Pilih beam terdekat berdasarkan jarak permukaan
            for b in all_beams:
                b["_surface_distance"] = haversine(obs_lat, obs_lon, b["clat"], b["clon"])
            best_beam_initial = min(all_beams, key=lambda x: x["_surface_distance"])
            
            id_antena_terbaik = best_beam_initial['id_antena']
            
            # --- PERHITUNGAN DIRECTIVITY YANG SUDAH DIPERBAIKI ---

            # 1. Ambil directivity puncak dari fetch_antenna_pattern
            peak_directivity_dBi, ant_eff, ant_freq_ghz, theta_axis, gain_axis = fetch_antenna_pattern(id_antena_terbaik)
            if theta_axis is None: return jsonify({"error": f"Pattern data for antenna id {id_antena_terbaik} not found"}), 404

            # 2. Hitung jarak 3D dan sudut off-axis
            theta_off_final, distance_final = off_axis(sat["lat"], sat["lon"], sat["alt"], best_beam_initial["clat"], best_beam_initial["clon"], obs_lat, obs_lon)
            
            # 3. Hitung penurunan gain dari pola radiasi (hasilnya negatif)
            gain_drop_off_dB = gain_from_pattern(theta_off_final, theta_axis, gain_axis)

            # 4. Hitung directivity absolut di lokasi observer
            directivity_final_abs = peak_directivity_dBi + gain_drop_off_dB
            
            # --- AKHIR BLOK PERBAIKAN ---

            # Buat dictionary untuk informasi beam terbaik
            best_beam = {
                "id": best_beam_initial["id"], 
                "lat": best_beam_initial["clat"], 
                "lon": best_beam_initial["clon"],
                "id_antena": id_antena_terbaik, 
                "distance_to_obs_km": round(distance_final, 2),
                "directivity_at_obs_dBi": round(directivity_final_abs, 2)
            }
            
            # Update dictionary params dengan nilai dinamis yang sudah dihitung
            params.update({
                'directivity_satelit_tx_dBi': directivity_final_abs,
                'jarak_km': distance_final,
                'efisiensi_antena': float(ant_eff),
                'frekuensi_GHz': float(ant_freq_ghz)
            })

            # Lakukan kalkulasi link budget
            link_budget_result = calculate_link_budget(params)
            if link_budget_result['status'] == 'error': return jsonify({"error": link_budget_result['message']}), 500

            # Simpan hasil akhir ke tabel 'link'
            cur_insert_link = conn.cursor()
            sql = "INSERT INTO link (id_beam, id_default, distance, lat, lon, directivity, cinr, evaluasi, ci, cn, gt, eirp, fsl) VALUES (%s,%s,%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            p = link_budget_result['perhitungan']
            values = (
                int(best_beam['id']), 
                int(profile_id_to_use), 
                float(best_beam['distance_to_obs_km']), 
                obs_lat, 
                obs_lon, 
                float(best_beam['directivity_at_obs_dBi']), # Menyimpan directivity absolut
                float(link_budget_result['cinr_dB']), 
                str(link_budget_result['evaluasi']), 
                float(p['c_per_i_downlink_db']),
                float(p['c_per_n_downlink_dB']), 
                float(p['g_per_t_stasiun_bumi_dBK']), 
                float(p['eirp_downlink_dBW']), 
                float(p['free_space_loss_dB'])
            )
            cur_insert_link.execute(sql, values)
            link_id_new = cur_insert_link.lastrowid
            conn.commit()
            
            final_response = {
                "message": "Calculation successful and data stored.", 
                "link_id": link_id_new, 
                "best_beam_found": best_beam, 
                "link_budget_result": link_budget_result, 
                "profile_id_used": profile_id_to_use
            }
            return jsonify(final_response)

    except (Error, ValueError) as e: 
        return jsonify({"error": f"Operation failed: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected server error occurred: {e}"}), 500
    
# --- Endpoint PUT untuk Update/Re-calculate (VERSI FINAL DENGAN PEMILIHAN BEAM ID) ---
@link_budget_bp.route("/link/<int:link_id>", methods=["PUT"])
@jwt_required()
def update_link(link_id):
    id_akun_login = get_jwt_identity()
    data = request.get_json()
    if not data or "link_params" not in data:
        return jsonify({"error": "Request body must contain 'link_params' with new parameters."}), 400
    
    link_params_custom = data["link_params"]
    
    # --- PERUBAHAN 1: Ambil input opsional untuk pemilihan beam manual berdasarkan ID ---
    new_obs_lat = data.get("obs_lat")
    new_obs_lon = data.get("obs_lon")
    ref_beam_id = data.get("ref_beam_id") # ID dari beam yang ingin dirujuk

    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            
            # 1. Validasi link lama (tidak ada perubahan)
            sql_validate = "SELECT l.id, l.lat, l.lon, l.id_default FROM link AS l JOIN beam AS b ON l.id_beam = b.id JOIN antena AS a ON b.id_antena = a.id JOIN satelite AS s ON a.id_satelite = s.id WHERE s.id_akun = %s AND l.id = %s"
            cur.execute(sql_validate, (id_akun_login, link_id))
            link_info = cur.fetchone()
            if not link_info:
                return jsonify({"error": "Link not found or you do not have permission to update it."}), 404

            # 2. Tentukan koordinat observasi (tidak ada perubahan)
            lat_for_recalc, lon_for_recalc = None, None
            if new_obs_lat is not None and new_obs_lon is not None:
                try:
                    lat_for_recalc, lon_for_recalc = float(new_obs_lat), float(new_obs_lon)
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid format for new 'obs_lat' or 'obs_lon'."}), 400
            else:
                lat_for_recalc, lon_for_recalc = link_info['lat'], link_info['lon']

            # 3. Ambil semua beam yang tersedia (tidak ada perubahan)
            all_beams = fetch_all_beams_by_account(id_akun_login)
            if not all_beams:
                return jsonify({"error": "No beam data available for your account to perform recalculation."}), 404

            # --- PERUBAHAN 2: Logika Pemilihan Beam (Manual by ID atau Otomatis) ---
            best_beam_for_update = None
            selection_method_info = ""

            # Jika pengguna memberikan ID beam referensi
            if ref_beam_id is not None:
                try:
                    ref_id = int(ref_beam_id)
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid format for 'ref_beam_id'. It must be an integer."}), 400

                # Cari beam yang cocok berdasarkan ID dari daftar beam milik user
                found_beam = next((beam for beam in all_beams if beam['id'] == ref_id), None)
                
                if not found_beam:
                    return jsonify({"error": f"The specified reference beam with ID {ref_id} was not found for your account."}), 404
                
                best_beam_for_update = found_beam
                selection_method_info = f"Recalculated using manually specified beam ID {best_beam_for_update['id']}."

            # Jika tidak ada input manual, gunakan logika otomatis (paling dekat)
            else:
                for b in all_beams:
                    b["_surface_distance"] = haversine(lat_for_recalc, lon_for_recalc, b["clat"], b["clon"])
                
                best_beam_for_update = min(all_beams, key=lambda x: x["_surface_distance"])
                selection_method_info = f"Recalculated using automatically selected nearest beam ID {best_beam_for_update['id']}."
            # --------------------------------------------------------------------

            id_antena_terbaik = best_beam_for_update['id_antena']

            # 5. Logika untuk mengelola profil link (tidak ada perubahan)
            current_default_id = link_info['id_default']
            base_params = fetch_link_budget_defaults(1)
            if not base_params: return jsonify({"error": "Base default profile (ID=1) not found."}), 500
            final_params = {**base_params, **link_params_custom}
            
            profile_id_to_use = None
            message = ""
            if current_default_id == 1:
                cur_manage_default = conn.cursor()
                sql_insert_default = "INSERT INTO default_link (dir_ground, tx_sat, suhu, bw, loss, ci_down) VALUES (%s, %s, %s, %s, %s, %s)"
                cur_manage_default.execute(sql_insert_default, (final_params['dir_ground'], final_params['tx_sat'], final_params['suhu'], final_params['bw'], final_params['loss'], final_params['ci_down']))
                profile_id_to_use = cur_manage_default.lastrowid
                message = f"Link ID {link_id} updated by creating a new custom profile ID {profile_id_to_use}."
                cur_manage_default.close()
            else:
                cur_manage_default = conn.cursor()
                sql_update_default = "UPDATE default_link SET dir_ground=%s, tx_sat=%s, suhu=%s, bw=%s, loss=%s, ci_down=%s WHERE id=%s"
                cur_manage_default.execute(sql_update_default, (final_params['dir_ground'], final_params['tx_sat'], final_params['suhu'], final_params['bw'], final_params['loss'], final_params['ci_down'], current_default_id))
                profile_id_to_use = current_default_id
                message = f"Link ID {link_id} updated by modifying its existing custom profile ID {profile_id_to_use}."
                cur_manage_default.close()

            params = final_params.copy()

            # 6. Lanjutkan proses re-kalkulasi (tidak ada perubahan)
            sat = fetch_satellite_by_account(id_akun_login)
            
            # 1. Ambil directivity puncak dari fetch_antenna_pattern
            peak_directivity_dBi, ant_eff, ant_freq_ghz, theta_axis, gain_axis = fetch_antenna_pattern(id_antena_terbaik)
            if theta_axis is None:
                return jsonify({"error": f"Pattern data not found for antenna ID: {id_antena_terbaik}"}), 404

            theta_off, distance = off_axis(sat["lat"], sat["lon"], sat["alt"], best_beam_for_update["clat"], best_beam_for_update["clon"], lat_for_recalc, lon_for_recalc)
            
            # 2. Hitung penurunan gain
            gain_drop_off_dB = gain_from_pattern(theta_off, theta_axis, gain_axis)
            
            # 3. Hitung directivity absolut
            directivity_abs = peak_directivity_dBi + gain_drop_off_dB
            
            # --- AKHIR PERUBAHAN ---
            
            params.update({
                # 4. Gunakan nilai directivity absolut yang baru
                'directivity_satelit_tx_dBi': directivity_abs, 
                'jarak_km': distance,
                'efisiensi_antena': float(ant_eff), 
                'frekuensi_GHz': float(ant_freq_ghz)
            })

            link_budget_result = calculate_link_budget(params)
            if link_budget_result['status'] == 'error': return jsonify({"error": link_budget_result['message']}), 500
            
            # 7. Update tabel link dengan nilai directivity yang sudah absolut
            cur_update = conn.cursor()
            sql_update_link = """
                UPDATE link SET id_beam=%s, id_default=%s, distance=%s, lat=%s, lon=%s, directivity=%s, 
                               cinr=%s, evaluasi=%s, ci=%s, cn=%s, gt=%s, eirp=%s, fsl=%s 
                WHERE id=%s
            """
            p = link_budget_result['perhitungan']
            values = (
                best_beam_for_update['id'], 
                profile_id_to_use, float(distance), lat_for_recalc, lon_for_recalc, 
                # 5. Simpan nilai directivity absolut
                float(directivity_abs), 
                float(link_budget_result['cinr_dB']),
                str(link_budget_result['evaluasi']), float(p['c_per_i_downlink_db']), float(p['c_per_n_downlink_dB']),
                float(p['g_per_t_stasiun_bumi_dBK']), float(p['eirp_downlink_dBW']), float(p['free_space_loss_dB']),
                link_id
            )
            cur_update.execute(sql_update_link, values)
            conn.commit()

            final_message = f"{message} {selection_method_info}"
            return jsonify({"message": final_message, "new_link_data": link_budget_result})

    except (Error, ValueError) as e:
        return jsonify({"error": f"Operation failed: {e}"}), 500

# --- Endpoint GET All ---
@link_budget_bp.route("/links", methods=["GET"])
@jwt_required()
def get_all_links():
    id_akun_login = get_jwt_identity() 
    try:
        with get_conn() as conn: 
            cur = conn.cursor(dictionary=True) 
            # SQL query dimodifikasi untuk JOIN dengan tabel default_link
            sql = """
                SELECT 
                    l.id, l.lat, l.lon, l.id_beam, b.clat, b.clon, l.distance, l.directivity, 
                    l.cinr, l.evaluasi, l.ci, l.cn, l.gt, l.eirp, l.fsl, 
                    l.id_default,
                    d.dir_ground, d.tx_sat, d.suhu, d.bw, d.loss, d.ci_down
                FROM link AS l 
                JOIN beam AS b ON l.id_beam = b.id 
                JOIN antena AS a ON b.id_antena = a.id 
                JOIN satelite AS s ON a.id_satelite = s.id 
                JOIN default_link AS d ON l.id_default = d.id -- Menambahkan JOIN ke tabel default_link
                WHERE s.id_akun = %s 
                ORDER BY l.id DESC
            """
            cur.execute(sql, (id_akun_login,)) 
            all_link_data = cur.fetchall()
            cur.close()
            return jsonify(all_link_data)
    except Error as e:
        return jsonify({"error": f"Database error: {e}"}), 500
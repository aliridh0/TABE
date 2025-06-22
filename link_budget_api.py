from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from koneksi import get_conn, Error
import numpy as np
import math
from scipy.interpolate import interp1d

link_budget_bp = Blueprint('link_budget', __name__)

# --- Fungsi Helper & Kalkulasi ---
# (Tidak ada perubahan di semua fungsi ini)
def fetch_satellite_by_account(id_akun):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT lat, lon, alt FROM satelite WHERE id_akun = %s LIMIT 1", (id_akun,))
            satellite = cur.fetchone()
            cur.close()
            return satellite
    except Error as e:
        print(f"Database error in fetch_satellite_by_account: {e}")
        return None
def fetch_antenna_pattern(ant_id):
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
            cur.close()
            if not theta_axis or not gain_axis: return None, None, None, None, None
            return (antenna_data['directivity'], antenna_data['eff'], antenna_data['frekuensi'], np.array(theta_axis), np.array(gain_axis))
    except Error as e:
        print(f"Database error in fetch_antenna_pattern: {e}")
        return None, None, None, None, None
def fetch_beam_by_id(beam_id):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, clat, clon, id_antena FROM beam WHERE id = %s", (beam_id,))
            beam = cur.fetchone()
            cur.close()
            return beam
    except Error as e:
        print(f"Database error in fetch_all_beams: {e}")
        return None
def fetch_all_beams():
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, clat, clon, id_antena FROM beam")
            beams = cur.fetchall()
            cur.close()
            return beams
    except Error as e:
        print(f"Database error in fetch_all_beams: {e}")
        return []
def fetch_link_budget_defaults(profile_id=1):
    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM default_link WHERE id = %s", (profile_id,))
            db_row = cur.fetchone()
            cur.close()
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
    theta_deg = np.clip(theta_deg, axis_theta.min(), axis_theta.max())
    f = interp1d(axis_theta, axis_gain, bounds_error=False, fill_value="extrapolate")
    return float(f(theta_deg))
def calculate_link_budget(params):
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
def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
    dlon, dlat = lon2-lon1, lat2-lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * EARTH_R_KM * math.atan2(math.sqrt(a), math.sqrt(1-a))

# --- Endpoint POST ---
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
            cur = conn.cursor(dictionary=True)
            
            profile_id_to_use = 1
            params_from_db = fetch_link_budget_defaults(1)
            if not params_from_db:
                return jsonify({"error": "Base default profile (ID=1) not found in database."}), 500

            if link_params_custom:
                final_params_for_insert = params_from_db.copy()
                final_params_for_insert.update(link_params_custom)
                
                cur_insert = conn.cursor()
                sql_insert = "INSERT INTO default_link (dir_ground, tx_sat, suhu, bw, loss, ci_down) VALUES (%s, %s, %s, %s, %s, %s)"
                cur_insert.execute(sql_insert, (
                    final_params_for_insert['dir_ground'],
                    final_params_for_insert['tx_sat'],
                    final_params_for_insert['suhu'],
                    final_params_for_insert['bw'],
                    final_params_for_insert['loss'],
                    final_params_for_insert['ci_down']
                ))
                profile_id_to_use = cur_insert.lastrowid
                cur_insert.close()

            params = fetch_link_budget_defaults(profile_id_to_use)
            if not params: return jsonify({"error": "Failed to fetch parameters for calculation."}), 500

            sat = fetch_satellite_by_account(id_akun_login)
            if not sat: return jsonify({"error": f"Satellite for account id {id_akun_login} not found"}), 404
            
            all_beams = fetch_all_beams()
            if not all_beams: return jsonify({"error": "No beam data available"}), 404
            
            for b in all_beams:
                b["_surface_distance"] = haversine(obs_lat, obs_lon, b["clat"], b["clon"])
            best_beam_initial = min(all_beams, key=lambda x: x["_surface_distance"])
            
            id_antena_terbaik = 1
            _, ant_eff, ant_freq_ghz, theta_axis, gain_axis = fetch_antenna_pattern(id_antena_terbaik)
            if theta_axis is None: return jsonify({"error": f"Pattern data for antenna id {id_antena_terbaik} not found"}), 404

            theta_off_final, distance_final = off_axis(sat["lat"], sat["lon"], sat["alt"], best_beam_initial["clat"], best_beam_initial["clon"], obs_lat, obs_lon)
            directivity_final = gain_from_pattern(theta_off_final, theta_axis, gain_axis)
            
            best_beam = {
                "id": best_beam_initial["id"], "lat": best_beam_initial["clat"], "lon": best_beam_initial["clon"],
                "id_antena": id_antena_terbaik, "distance_to_obs_km": round(distance_final, 2),
                "directivity_at_obs_dBi": round(directivity_final, 2)
            }
            
            params.update({
                'directivity_satelit_tx_dBi': best_beam['directivity_at_obs_dBi'],
                'jarak_km': best_beam['distance_to_obs_km'],
                'efisiensi_antena': float(ant_eff),
                'frekuensi_GHz': float(ant_freq_ghz)
            })

            link_budget_result = calculate_link_budget(params)
            if link_budget_result['status'] == 'error': return jsonify({"error": link_budget_result['message']}), 500

            cur_insert_link = conn.cursor()
            sql = "INSERT INTO link (id_beam, id_default, distance, lat, lon, directivity, cinr, evaluasi, ci, cn, gt, eirp, fsl) VALUES (%s,%s,%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            p = link_budget_result['perhitungan']
            values = (
                int(best_beam['id']), int(profile_id_to_use), float(best_beam['distance_to_obs_km']), obs_lat, obs_lon, float(best_beam['directivity_at_obs_dBi']),
                float(link_budget_result['cinr_dB']), str(link_budget_result['evaluasi']), float(p['c_per_i_downlink_db']),
                float(p['c_per_n_downlink_dB']), float(p['g_per_t_stasiun_bumi_dBK']), float(p['eirp_downlink_dBW']), float(p['free_space_loss_dB'])
            )
            cur_insert_link.execute(sql, values)
            link_id_new = cur_insert_link.lastrowid
            conn.commit()
            
            final_response = {"message": "Calculation successful and data stored.", "link_id": link_id_new, "best_beam_found": best_beam, "link_budget_result": link_budget_result, "profile_id_used": profile_id_to_use}
            return jsonify(final_response)

    except Error as e:
        return jsonify({"error": f"Database error: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected server error occurred: {e}"}), 500


# --- Endpoint PUT untuk Update/Re-calculate ---
@link_budget_bp.route("/link/<int:link_id>", methods=["PUT"])
@jwt_required()
def update_link(link_id):
    id_akun_login = get_jwt_identity()
    data = request.get_json()
    if not data or "link_params" not in data:
        return jsonify({"error": "Request body must contain 'link_params' with new parameters."}), 400
    
    link_params_custom = data["link_params"]

    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            
            # --- PERUBAHAN LOGIKA: Ambil juga 'id_default' dari link yang ada ---
            sql_validate = """
                SELECT l.id_beam, l.lat, l.lon, l.id_default 
                FROM link AS l 
                JOIN beam AS b ON l.id_beam = b.id 
                JOIN antena AS a ON b.id_antena = a.id 
                JOIN satelite AS s ON a.id_satelite = s.id 
                WHERE s.id_akun = %s AND l.id = %s
            """
            cur.execute(sql_validate, (id_akun_login, link_id))
            link_info = cur.fetchone()
            
            if not link_info:
                return jsonify({"error": "Link not found or you do not have permission to update it."}), 404
            
            current_default_id = link_info['id_default']
            profile_id_to_use = None
            message = ""

            # Gabungkan parameter dasar dengan parameter kustom dari request
            base_params = fetch_link_budget_defaults(1)
            if not base_params: return jsonify({"error": "Base default profile (ID=1) not found."}), 500
            
            final_params = {**base_params, **link_params_custom}
            
            # --- PERUBAHAN LOGIKA: Cek apakah akan INSERT baru atau UPDATE yang sudah ada ---
            if current_default_id == 1:
                # KASUS 1: Link masih menggunakan profil default, buat profil kustom baru.
                cur_manage_default = conn.cursor()
                sql_insert_default = """
                    INSERT INTO default_link (dir_ground, tx_sat, suhu, bw, loss, ci_down) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                cur_manage_default.execute(sql_insert_default, (
                    final_params['dir_ground'], final_params['tx_sat'],
                    final_params['suhu'], final_params['bw'],
                    final_params['loss'], final_params['ci_down']
                ))
                profile_id_to_use = cur_manage_default.lastrowid
                message = f"Link ID {link_id} updated by creating a new custom profile ID {profile_id_to_use}."
                cur_manage_default.close()
            else:
                # KASUS 2: Link sudah punya profil kustom, update profil tersebut.
                cur_manage_default = conn.cursor()
                sql_update_default = """
                    UPDATE default_link 
                    SET dir_ground=%s, tx_sat=%s, suhu=%s, bw=%s, loss=%s, ci_down=%s
                    WHERE id=%s
                """
                cur_manage_default.execute(sql_update_default, (
                    final_params['dir_ground'], final_params['tx_sat'],
                    final_params['suhu'], final_params['bw'],
                    final_params['loss'], final_params['ci_down'],
                    current_default_id
                ))
                profile_id_to_use = current_default_id
                message = f"Link ID {link_id} updated by modifying its existing custom profile ID {profile_id_to_use}."
                cur_manage_default.close()

            # --- Lanjutan proses re-kalkulasi (sebagian besar tetap sama) ---
            
            obs_lat_original = link_info['lat']
            obs_lon_original = link_info['lon']
            id_beam_to_recalc = link_info['id_beam']
            
            # Ambil parameter lengkap dari profil yang akan digunakan (baik yang baru maupun yang diupdate)
            params = fetch_link_budget_defaults(profile_id_to_use)
            sat = fetch_satellite_by_account(id_akun_login)
            beam_to_recalc = fetch_beam_by_id(id_beam_to_recalc)
            _, ant_eff, ant_freq_ghz, theta_axis, gain_axis = fetch_antenna_pattern(beam_to_recalc['id_antena'])
            
            theta_off, distance = off_axis(sat["lat"], sat["lon"], sat["alt"], beam_to_recalc["clat"], beam_to_recalc["clon"], obs_lat_original, obs_lon_original)
            directivity = gain_from_pattern(theta_off, theta_axis, gain_axis)
            
            params.update({
                'directivity_satelit_tx_dBi': directivity, 'jarak_km': distance,
                'efisiensi_antena': float(ant_eff), 'frekuensi_GHz': float(ant_freq_ghz)
            })

            link_budget_result = calculate_link_budget(params)
            if link_budget_result['status'] == 'error': return jsonify({"error": link_budget_result['message']}), 500
            
            # Update tabel link dengan hasil kalkulasi baru dan id_default yang sesuai
            cur_update = conn.cursor()
            sql_update_link = """
                UPDATE link SET id_default=%s, distance=%s, lat=%s, lon=%s, directivity=%s, cinr=%s, evaluasi=%s, 
                               ci=%s, cn=%s, gt=%s, eirp=%s, fsl=%s 
                WHERE id=%s
            """
            p = link_budget_result['perhitungan']
            values = (
                profile_id_to_use, float(distance), float(data['obs_lat']), float(data['obs_lon']), float(directivity), float(link_budget_result['cinr_dB']),
                str(link_budget_result['evaluasi']), float(p['c_per_i_downlink_db']), float(p['c_per_n_downlink_dB']),
                float(p['g_per_t_stasiun_bumi_dBK']), float(p['eirp_downlink_dBW']), float(p['free_space_loss_dB']),
                link_id
            )
            cur_update.execute(sql_update_link, values)
            conn.commit()

            return jsonify({"message": message, "new_link_data": link_budget_result})

    except Error as e:
        # Rollback otomatis terjadi jika 'with get_conn()' gagal atau ada exception
        return jsonify({"error": f"Database error: {e}"}), 500
    
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
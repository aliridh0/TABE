from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from koneksi import get_conn, Error
import numpy as np
import math
from scipy.interpolate import interp1d

# --- Inisialisasi Blueprint ---
link_budget_bp = Blueprint('link_budget', __name__)

# --- DICTIONARY UNTUK PARAMETER DEFAULT ---
LINK_BUDGET_DEFAULTS = {
    'frekuensi_GHz': 20.0,
    'directivity_stasiun_bumi_rx_dBi': 45.0,
    'efisiensi_antena': 0.65,
    'daya_pancar_satelit_dBW': 17.0,
    'suhu_derau_sistem_K': 100.0,
    'bandwidth_Hz': 36000000.0,
    'losses_downlink_dB': 3.0,
    'c_to_i_downlink_db': 20.0
}

# --- Fungsi Helper & Kalkulasi ---
# (Tidak ada perubahan di semua fungsi helper dan kalkulasi, semuanya sudah benar)
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
            cur.execute("SELECT directivity FROM antena WHERE id = %s", (ant_id,))
            antenna_data = cur.fetchone()
            if not antenna_data: return None, None, None
            cur.execute("SELECT deg FROM theta WHERE id_antena = %s ORDER BY id", (ant_id,))
            theta_axis = [row['deg'] for row in cur.fetchall()]
            cur.execute("SELECT deg FROM pattern WHERE id_antena = %s ORDER BY id", (ant_id,))
            gain_axis = [row['deg'] for row in cur.fetchall()]
            cur.close()
            if not theta_axis or not gain_axis: return None, None, None
            return antenna_data['directivity'], np.array(theta_axis), np.array(gain_axis)
    except Error as e:
        print(f"Database error in fetch_antenna_pattern: {e}")
        return None, None, None
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
        directivity_stasiun_bumi_rx_dBi = params['directivity_stasiun_bumi_rx_dBi']
        frekuensi_GHz = params['frekuensi_GHz']
        jarak_km = params['jarak_km']
        efisiensi_antena_lin = params['efisiensi_antena']
        pt_satelit_dBW = params['daya_pancar_satelit_dBW']
        tsys_stasiun_bumi_K = params['suhu_derau_sistem_K']
        bandwidth_Hz = params['bandwidth_Hz']
        losses_downlink_dB = params['losses_downlink_dB']
        c_to_i_dB = params['c_to_i_downlink_db']
        konstanta_boltzmann_k_dB = -228.6
        gain_satelit_tx_dBi = directivity_satelit_tx_dBi + 10 * math.log10(efisiensi_antena_lin)
        gain_stasiun_bumi_rx_dBi = directivity_stasiun_bumi_rx_dBi + 10 * math.log10(efisiensi_antena_lin)
        frekuensi_MHz = frekuensi_GHz * 1000
        fsl_dB = 32.44 + 20 * math.log10(jarak_km) + 20 * math.log10(frekuensi_MHz)
        eirp_downlink_dBW = pt_satelit_dBW + gain_satelit_tx_dBi
        g_per_t_stasiun_bumi_dBK = gain_stasiun_bumi_rx_dBi - 10 * math.log10(tsys_stasiun_bumi_K)
        c_to_n_downlink_dB = eirp_downlink_dBW - fsl_dB - losses_downlink_dB + g_per_t_stasiun_bumi_dBK - konstanta_boltzmann_k_dB - 10 * math.log10(bandwidth_Hz)
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

# --- Endpoint Utama (POST) ---
@link_budget_bp.route("/calculate_and_store", methods=["POST"])
@jwt_required()
def calculate_and_store_link():
    id_akun_login = get_jwt_identity()
    if not id_akun_login: return jsonify({"error": "Invalid token"}), 401
    data = request.get_json()
    if not data: return jsonify({"error": "Invalid JSON payload"}), 400
    try:
        obs_lat = float(data["obs_lat"])
        obs_lon = float(data["obs_lon"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Missing or invalid 'obs_lat' or 'obs_lon'"}), 400
    sat = fetch_satellite_by_account(id_akun_login)
    if not sat: return jsonify({"error": f"Satellite for account id {id_akun_login} not found"}), 404
    all_beams = fetch_all_beams()
    if not all_beams: return jsonify({"error": "No beam data available"}), 404
    for b in all_beams:
        b["_surface_distance"] = haversine(obs_lat, obs_lon, b["clat"], b["clon"])
    best_beam_initial = min(all_beams, key=lambda x: x["_surface_distance"])
    id_antena_terbaik = best_beam_initial["id_antena"]
    _, theta_axis, gain_axis = fetch_antenna_pattern(id_antena_terbaik)
    if theta_axis is None: return jsonify({"error": f"Pattern data for antenna id {id_antena_terbaik} not found"}), 404
    theta_off_final, distance_final = off_axis(sat["lat"], sat["lon"], sat["alt"], best_beam_initial["clat"], best_beam_initial["clon"], obs_lat, obs_lon)
    directivity_final = gain_from_pattern(theta_off_final, theta_axis, gain_axis)
    best_beam = {
        "id": best_beam_initial["id"], "lat": best_beam_initial["clat"], "lon": best_beam_initial["clon"],
        "id_antena": id_antena_terbaik, "distance_to_obs_km": round(distance_final, 2),
        "directivity_at_obs_dBi": round(directivity_final, 2)
    }
    params = LINK_BUDGET_DEFAULTS.copy()
    if "link_params" in data:
        params.update(data["link_params"])
    params['directivity_satelit_tx_dBi'] = best_beam['directivity_at_obs_dBi']
    params['jarak_km'] = best_beam['distance_to_obs_km']
    link_budget_result = calculate_link_budget(params)
    if link_budget_result['status'] == 'error':
        return jsonify({"error": link_budget_result['message']}), 500
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            sql = "INSERT INTO link (id_beam, distance, directivity, cinr, evaluasi, ci, cn, gt, eirp, fsl) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE distance=VALUES(distance), directivity=VALUES(directivity), cinr=VALUES(cinr), evaluasi=VALUES(evaluasi), ci=VALUES(ci), cn=VALUES(cn), gt=VALUES(gt), eirp=VALUES(eirp), fsl=VALUES(fsl);"
            p = link_budget_result['perhitungan']
            values = (
                int(best_beam['id']), float(best_beam['distance_to_obs_km']), float(best_beam['directivity_at_obs_dBi']),
                float(link_budget_result['cinr_dB']), str(link_budget_result['evaluasi']), float(p['c_per_i_downlink_db']),
                float(p['c_per_n_downlink_dB']), float(p['g_per_t_stasiun_bumi_dBK']), float(p['eirp_downlink_dBW']), float(p['free_space_loss_dB'])
            )
            cur.execute(sql, values)
            conn.commit()
    except Error as e:
        return jsonify({"warning": f"Calculation successful but failed to store in DB: {e}", "best_beam_found": best_beam, "link_budget_result": link_budget_result}), 500
    final_response = {"message": "Calculation successful and data stored.", "best_beam_found": best_beam, "link_budget_result": link_budget_result}
    return jsonify(final_response)

# --- Endpoint GET ---
@link_budget_bp.route("/link/<int:beam_id>", methods=["GET"])
@jwt_required()
def get_link_data(beam_id):
    """
    Mengambil data link budget untuk satu beam, TAPI HANYA JIKA
    beam tersebut milik akun yang sedang login.
    """
    id_akun_login = get_jwt_identity() # Dapatkan ID akun dari token

    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            # Query JOIN yang kompleks untuk verifikasi kepemilikan
            sql = """
                SELECT l.*, b.clat, b.clon
                FROM link AS l
                JOIN beam AS b ON l.id_beam = b.id
                JOIN antena AS a ON b.id_antena = a.id
                JOIN satelite AS s ON a.id_satelite = s.id
                WHERE s.id_akun = %s AND l.id_beam = %s
            """
            cur.execute(sql, (id_akun_login, beam_id)) # Kirim dua parameter ke query
            link_data = cur.fetchone()
            cur.close()

            if link_data:
                return jsonify(link_data)
            else:
                return jsonify({"error": "No stored link data found for this beam ID, or you do not have permission to view it."}), 404

    except Error as e:
        return jsonify({"error": f"Database error: {e}"}), 500
        
# --- Endpoint GET All (DENGAN FILTER OTOMATIS) ---
@link_budget_bp.route("/links", methods=["GET"])
@jwt_required()
def get_all_links():
    """
    Mengambil SEMUA data link budget yang relevan untuk akun yang login,
    dikembalikan dalam bentuk array.
    """
    id_akun_login = get_jwt_identity() # Dapatkan ID akun dari token

    try:
        with get_conn() as conn:
            cur = conn.cursor(dictionary=True)
            # Query JOIN yang sama untuk memfilter hasil berdasarkan kepemilikan
            sql = """
                SELECT 
                    l.id, l.id_beam, b.clat, b.clon, l.distance, l.directivity, 
                    l.cinr, l.evaluasi, l.ci, l.cn, l.gt, l.eirp, l.fsl
                FROM link AS l
                JOIN beam AS b ON l.id_beam = b.id
                JOIN antena AS a ON b.id_antena = a.id
                JOIN satelite AS s ON a.id_satelite = s.id
                WHERE s.id_akun = %s
                ORDER BY l.id_beam ASC
            """
            cur.execute(sql, (id_akun_login,)) # Kirim id_akun sebagai parameter
            all_link_data = cur.fetchall()
            cur.close()
            
            return jsonify(all_link_data)

    except Error as e:
        return jsonify({"error": f"Database error: {e}"}), 500

# Fungsi Haversine ditambahkan untuk seleksi awal
def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
    dlon, dlat = lon2-lon1, lat2-lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * EARTH_R_KM * math.atan2(math.sqrt(a), math.sqrt(1-a))
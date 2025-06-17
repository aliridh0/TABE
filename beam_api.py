"""
beam_api.py – Flask API spot-beam yang terkoneksi MySQL (pool db.py)
"""

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
    ang  = np.arccos(np.sin(np.radians(clat))*np.sin(np.radians(sat_lat)) +
                     np.cos(np.radians(clat))*np.cos(np.radians(sat_lat))*np.cos(dlon))
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
def fetch_satellite(sat_id=None):
    q = "SELECT id, name, latitude, longitude FROM satellite ORDER BY id LIMIT 1"
    p = ()
    if sat_id:
        q = "SELECT id, name, latitude, longitude FROM satellite WHERE id=%s"
        p = (sat_id,)
    with get_conn() as conn, conn.cursor(dictionary=True) as cur:
        cur.execute(q, p)
        return cur.fetchone()

def fetch_gain_theta(ant_id):
    """Ambil list[float] gain_dB & theta_deg untuk antena tertentu."""
    with get_conn() as conn, conn.cursor(dictionary=True) as cur:
        cur.execute("SELECT deg FROM theta WHERE id_antena=%s ORDER BY id", (ant_id,))
        theta = [row["deg"] for row in cur.fetchall()]

        cur.execute("SELECT deg FROM pattern WHERE id_antena=%s ORDER BY id", (ant_id,))
        gain  = [row["deg"] for row in cur.fetchall()]
    return gain, theta

# ────────────────────  Endpoint: store-beam  ─────────────────────
@beam_blueprint.route("/store-beam", methods=["POST"])
def store_beam():
    data = request.get_json(silent=True)
    try:
        clat = float(data["center_lat"])
        clon = float(data["center_lon"])
        ant_id = int(data["id_antena"])
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"Invalid field: {e}"}), 400

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO beam (clat, clon, id_antena) VALUES (%s,%s,%s)",
                        (clat, clon, ant_id))
            conn.commit()
            beam_id = cur.lastrowid
        return jsonify({"message": "Beam stored", "beam_id": beam_id}), 201
    except Error as err:
        return jsonify({"error": f"DB error: {err}"}), 500

# ────────────────────  Endpoint: get-beams  ──────────────────────
@beam_blueprint.route("/get-beams", methods=["GET"])
def get_beams():
    try:
        with get_conn() as conn, conn.cursor(dictionary=True) as cur:
            cur.execute("""
              SELECT b.id, b.clat AS center_lat, b.clon AS center_lon,
                     b.id_antena AS id_antena
                FROM beam AS b
            """)
            return jsonify(cur.fetchall())
    except Error as err:
        return jsonify({"error": f"DB error: {err}"}), 500

# ──────────────────  Endpoint: generate-ellipse  ─────────────────
@beam_blueprint.route("/generate-ellipse", methods=["POST"])
def generate_ellipse():
    data = request.get_json(silent=True) or {}
    sat_id = data.get("satellite_id")

    sat = fetch_satellite(sat_id)
    if not sat:
        return jsonify({"error": "Satellite not found"}), 400

    out = []
    try:
        with get_conn() as conn, conn.cursor(dictionary=True) as cur:
            cur.execute("SELECT * FROM beam")
            beams = cur.fetchall()

        for beam in beams:
            gain_dB, theta_deg = fetch_gain_theta(beam["id_antena"])
            maj, minr, rot, hbw = generate_spot_beam_properties(
                beam["clat"], beam["clon"],
                gain_dB, theta_deg,
                sat["longitude"], sat["latitude"]
            )

            levels = {}
            for lvl in (-1, -2, -3):
                levels[f"level{lvl}"] = {
                    "half_bw_deg": hbw[lvl],
                    "points": ellipse_points(beam["clat"], beam["clon"],
                                             maj, minr, rot)
                }

            out.append({
                "beam_id": beam["id"],
                "center": {"lat": beam["clat"], "lon": beam["clon"]},
                "id_antena": beam["id_antena"],
                "ellipse": levels
            })

        return jsonify(out)

    except Error as err:
        return jsonify({"error": f"DB error: {err}"}), 500
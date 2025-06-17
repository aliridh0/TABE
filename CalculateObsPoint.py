from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import numpy as np, math, json
from scipy.interpolate import interp1d

# ── App & DB
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"    # sama dg modul lain
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ── Models (harus sama dengan yang sudah ada)
class Satellite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64))
    latitude  = db.Column(db.Float, default=0.0)
    longitude = db.Column(db.Float, default=146.0)
    altitude  = db.Column(db.Float, default=35786.0)

class Antenna(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(64))
    directivity_dB = db.Column(db.Float)
    theta_json     = db.Column(db.Text)   # list theta_deg
    pattern_json   = db.Column(db.Text)   # list pattern_dB

class Beam(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(64))
    center_lat = db.Column(db.Float)
    center_lon = db.Column(db.Float)

# ── Bantu hitung
EARTH_R_KM = 6371.0

def geodetic_to_ecef(lat, lon, alt):
    lat, lon = map(np.deg2rad, [lat, lon])
    r = EARTH_R_KM + alt
    return np.array([r*np.cos(lat)*np.cos(lon),
                     r*np.cos(lat)*np.sin(lon),
                     r*np.sin(lat)])

def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon, dlat = lon2-lon1, lat2-lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*EARTH_R_KM*math.atan2(math.sqrt(a), math.sqrt(1-a))

def off_axis(sat_lat, sat_lon, sat_alt, tgt_lat, tgt_lon, obs_lat, obs_lon):
    sat_xyz  = geodetic_to_ecef(sat_lat, sat_lon, sat_alt)
    tgt_xyz  = geodetic_to_ecef(tgt_lat, tgt_lon, 0)
    obs_xyz  = geodetic_to_ecef(obs_lat, obs_lon, 0)
    v_bt     = tgt_xyz - sat_xyz
    v_obs    = obs_xyz - sat_xyz
    cos_th   = np.dot(v_obs, v_bt) / (np.linalg.norm(v_bt)*np.linalg.norm(v_obs))
    cos_th   = np.clip(cos_th, -1.0, 1.0)
    return math.degrees(math.acos(cos_th))

def gain_from_pattern(theta_deg, axis_theta, axis_gain):
    theta_deg = np.clip(theta_deg, axis_theta.min(), axis_theta.max())
    f = interp1d(axis_theta, axis_gain, bounds_error=False, fill_value="extrapolate")
    return float(f(theta_deg))

# ── Endpoint: best_beam
@app.route("/best_beam", methods=["POST"])
def best_beam():
    """
    Body:
    {
      "obs_lat": ..,
      "obs_lon": ..,
      "satellite_id": 1,   (optional)
      "antenna_id":  1     (optional)
    }
    """
    data = request.get_json()
    try:
        obs_lat = float(data["obs_lat"])
        obs_lon = float(data["obs_lon"])
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"Missing / invalid obs_lat|obs_lon: {e}"}), 400

    # ── Ambil satelit
    sat = (Satellite.query.get(data.get("satellite_id"))
           if data.get("satellite_id") else Satellite.query.first())
    if not sat:
        return jsonify({"error": "No satellite record found"}), 400

    # ── Ambil antena (pattern)
    ant = (Antenna.query.get(data.get("antenna_id"))
           if data.get("antenna_id") else Antenna.query.first())
    if not ant:
        return jsonify({"error": "No antenna record found"}), 400
    theta_axis = np.array(json.loads(ant.theta_json))
    gain_axis  = np.array(json.loads(ant.pattern_json))

    # ── Ambil daftar beam (boleh dibatasi via payload["beams"])
    if "beams" in data:
        # user mengirim subset beam {name, lat, lon}
        all_beams = data["beams"]
    else:
        # pakai semua beam di DB
        all_beams = [ {"name": b.name, "lat": b.center_lat, "lon": b.center_lon}
                      for b in Beam.query.all() ]
        if not all_beams:
            return jsonify({"error": "No beam data available"}), 400

    # ── Hitung jarak & gain utk tiap beam
    for b in all_beams:
        b["distance_to_obs_km"] = haversine(obs_lat, obs_lon, b["lat"], b["lon"])
        theta_off = off_axis(sat.latitude, sat.longitude, sat.altitude,
                             b["lat"], b["lon"], obs_lat, obs_lon)
        b["directivity_at_obs_dBi"] = gain_from_pattern(theta_off, theta_axis, gain_axis)

    best = min(all_beams, key=lambda x: x["distance_to_obs_km"])

    return jsonify({"best_beam": best})

# ── init & run
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)

import math
import numpy as np
import folium
from folium import plugins
import branca.colormap as cm
from scipy.interpolate import interp1d
from scipy import special # Untuk fungsi Bessel
import matplotlib.pyplot as plt # Diperlukan untuk plot pola radiasi 2D awal

# --- 1. Parameter Geometris Bumi dan Satelit ---
EARTH_RADIUS_KM = 6371.0 # Radius rata-rata bumi dalam km
GEO_ALTITUDE_KM = 35786.0 # Ketinggian satelit geostasioner dari permukaan bumi (35.785,7 km dari gambar)

# Asumsi Koordinat Titik Observasi (Bandung)
LAT_OBSERVASI = 3  # Lintang Bandung
LON_OBSERVASI = 98 # Bujur Bandung

# --- 2. Fungsi Konversi Koordinat (Geodetik ke ECEF) ---
def geodetic_to_ecef(lat_deg, lon_deg, alt_km=0):
    """
    Mengkonversi koordinat Geodetik (Lintang, Bujur, Ketinggian) ke koordinat ECEF (X, Y, Z).
    """
    lat_rad = np.deg2rad(lat_deg)
    lon_rad = np.deg2rad(lon_deg)
    R = EARTH_RADIUS_KM + alt_km
    X = R * np.cos(lat_rad) * np.cos(lon_rad)
    Y = R * np.cos(lat_rad) * np.sin(lon_rad)
    Z = R * np.sin(lat_rad)
    return X, Y, Z

# --- 3. Fungsi Menghitung Sudut Off-Axis ---
def calculate_off_axis_angle(sat_lon_deg, beam_target_lat_deg, beam_target_lon_deg, obs_lat_deg, obs_lon_deg):
    """
    Menghitung sudut off-axis (theta) antara arah beam target dan arah observasi
    dari perspektif satelit.
    """
    sat_x, sat_y, sat_z = geodetic_to_ecef(0, sat_lon_deg, GEO_ALTITUDE_KM)
    
    # Pastikan obs_lat_deg dan obs_lon_deg bisa berupa skalar atau array
    obs_lat_rad = np.deg2rad(np.array(obs_lat_deg))
    obs_lon_rad = np.deg2rad(np.array(obs_lon_deg))

    # Convert beam target to ECEF
    beam_target_pos = np.array(geodetic_to_ecef(beam_target_lat_deg, beam_target_lon_deg, 0))
    
    # Convert observation point(s) to ECEF
    if np.isscalar(obs_lat_deg): # If scalar, make it a 1D array for consistent operation
        obs_pos = np.array(geodetic_to_ecef(obs_lat_deg, obs_lon_deg, 0))[np.newaxis, :]
    else: # If array, apply to all points
        obs_pos = np.array([geodetic_to_ecef(lat, lon, 0) for lat, lon in zip(obs_lat_deg, obs_lon_deg)])

    V_sat_to_beam_target = beam_target_pos - np.array([sat_x, sat_y, sat_z])
    V_sat_to_obs = obs_pos - np.array([sat_x, sat_y, sat_z])

    dot_product = np.dot(V_sat_to_obs, V_sat_to_beam_target)
    
    mag_V_sat_to_beam_target = np.linalg.norm(V_sat_to_beam_target)
    mag_V_sat_to_obs = np.linalg.norm(V_sat_to_obs, axis=1) # Norm along the last axis for (N,3) array

    denominator = mag_V_sat_to_beam_target * mag_V_sat_to_obs
    denominator[denominator == 0] = np.finfo(float).eps # Handle division by zero
    
    cosine_theta = np.clip(dot_product / denominator, -1.0, 1.0)

    theta_off_axis_rad = np.arccos(cosine_theta)
    theta_off_axis_deg = np.degrees(theta_off_axis_rad)
    
    if np.isscalar(obs_lat_deg): 
        return theta_off_axis_deg[0]
    return theta_off_axis_deg

# --- 4. Fungsi Menghitung Pola Radiasi Antena (dari MATLAB) ---
def calculate_antenna_radiation_pattern(f, D, F_D, a_waveguide, theta_deg_range=(0, 12), num_points=1000):
    """
    Menghitung pola radiasi antena parabola dengan feed TE11 mode.
    Menggunakan rumus dari Physical Optics untuk reflektor dan fungsi Bessel untuk feed.
    """
    c = 3e8 # Kecepatan cahaya
    lambda_ = c / f # Panjang gelombang
    k = 2 * np.pi / lambda_ # Bilangan gelombang

    a = D / 2 # Radius parabola (D adalah diameter)
    
    theta_deg = np.linspace(theta_deg_range[0], theta_deg_range[1], num_points)
    theta_rad = np.deg2rad(theta_deg)
    
    # Parameter untuk pola radiasi feed (TE11 mode)
    lambda_c_constant = 1.706 * a_waveguide 
    k_c = 2 * np.pi / lambda_c_constant
    
    x_feed = k_c * a_waveguide * np.sin(theta_rad)
    
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

# --- 5. Fungsi untuk Mendapatkan Gain dari Pola Radiasi (Interpolasi) ---
def get_gain_from_pattern(theta_off_axis_deg, pattern_theta_deg, pattern_gain_dB, kind='linear'):
    """
    Melakukan interpolasi untuk mendapatkan nilai gain pada sudut off-axis tertentu
    dari pola radiasi yang sudah dihitung.
    """
    theta_off_axis_deg_clipped = np.clip(theta_off_axis_deg, pattern_theta_deg.min(), pattern_theta_deg.max())
    interp_func = interp1d(pattern_theta_deg, pattern_gain_dB, kind=kind, fill_value="extrapolate", bounds_error=False)
    gain_interpolated = interp_func(theta_off_axis_deg_clipped)
    return gain_interpolated

# --- 6. Fungsi untuk Menghitung Properti Elips (Major Axis, Minor Axis, Angle) ---
def generate_spot_beam_ellipse_properties(center_lat, center_lon, beam_radius_deg, satellite_lon=146.0):
    """
    Menghitung properti elips (sumbu mayor, sumbu minor, sudut rotasi)
    untuk proyeksi beam dari satelit GEO ke permukaan bumi.
    """
    ssp_lat = 0.0 # Subsatellite point latitude for GEO
    ssp_lon = satellite_lon
    
    # Menghitung jarak angular (dalam radian) dari SSP ke pusat beam di bumi
    delta_lon_for_dist = np.radians(center_lon - ssp_lon)
    angular_distance_from_ssp = np.arccos(
        np.sin(np.radians(center_lat)) * np.sin(np.radians(ssp_lat)) +
        np.cos(np.radians(center_lat)) * np.cos(np.radians(ssp_lat)) * np.cos(delta_lon_for_dist)
    )
    
    # Mengklip jarak angular untuk mencegah masalah numerik di cos(90 deg)
    clamped_angular_dist = np.clip(angular_distance_from_ssp, 0, np.radians(85)) # Batas GEO coverage ~81.3 deg
    
    minor_axis_deg = beam_radius_deg # Minor axis sama dengan beamwidth angular
    
    # Faktor distorsi yang menyebabkan lingkaran menjadi elips di bumi
    distortion_factor = 1.0 / np.cos(clamped_angular_dist)
    major_axis_deg = minor_axis_deg * distortion_factor
    
    # Menghitung azimuth dari SSP ke pusat beam untuk menentukan orientasi elips
    lat1_rad = np.radians(ssp_lat)
    lon1_rad = np.radians(ssp_lon)
    lat2_rad = np.radians(center_lat)
    lon2_rad = np.radians(center_lon)
    
    y_val = np.sin(lon2_rad - lon1_rad) * np.cos(lat2_rad)
    x_val = np.cos(lat1_rad) * np.sin(lat2_rad) - np.sin(lat1_rad) * np.cos(lat2_rad) * np.cos(lon2_rad - lon1_rad)
    azimuth_from_north_clockwise_deg = np.degrees(np.arctan2(y_val, x_val))
    
    # Sesuaikan sudut untuk rotasi elips di Folium (biasanya 90 derajat dari azimuth relatif ke arah satelit)
    angle_for_rotation_deg = 90 - azimuth_from_north_clockwise_deg
    angle_for_rotation_deg = (angle_for_rotation_deg + 360) % 360 

    return major_axis_deg, minor_axis_deg, angle_for_rotation_deg

# --- Fungsi Tambahan: Menggenerasi Titik-titik Elips ---
def generate_ellipse_points(center_lat, center_lon, major_axis_deg, minor_axis_deg, angle_deg, num_points=100):
    """
    Menghasilkan serangkaian titik (lat, lon) yang membentuk sebuah elips.
    """
    thetas = np.linspace(0, 2 * np.pi, num_points)
    angle_rad = np.deg2rad(angle_deg)

    a_semi = major_axis_deg / 2 # Semi-major axis
    b_semi = minor_axis_deg / 2 # Semi-minor axis

    # Titik elips standar (belum dirotasi atau ditranslasi)
    x_ellipse = a_semi * np.cos(thetas)
    y_ellipse = b_semi * np.sin(thetas)

    # Rotasi titik elips
    rotated_x = x_ellipse * np.cos(angle_rad) - y_ellipse * np.sin(angle_rad)
    rotated_y = x_ellipse * np.sin(angle_rad) + y_ellipse * np.cos(angle_rad)

    # Translasi titik elips ke pusat beam
    list_lats = center_lat + rotated_y
    list_lons = center_lon + rotated_x
    
    points = [[lat, lon] for lat, lon in zip(list_lats, list_lons)]
    return points

# --- FUNGSI BARU: Fungsi Haversine untuk Jarak Geografis ---
def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Menghitung jarak Haversine antara dua titik geografis dalam kilometer.
    """
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad

    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = EARTH_RADIUS_KM * c
    return distance

# --- Bagian Utama: Konfigurasi dan Plotting ---
if __name__ == "__main__":
    # --- Konfigurasi Satelit dan Antena (sesuai input dari gambar) ---
    satellite_longitude = 146.0 
    ssp_latitude = 0.0 

    # Parameter Antena dari screenshot "Physical Optics and Analytic Reflector Models"
    frequency_hz_ka_band = 18e9     # Frekuensi (Hz) untuk pola radiasi (Ka-band Tx)
    D_aperture_m = 2.0341           # Diameter Aperture (m)
    F_D_ratio = 1.27                # Rasio Focal Length terhadap Diameter (F/D)
    waveguide_radius_m = 0.002      # Asumsi nilai default

    # --- 1. Hitung Pola Radiasi Antena Satelit ---
    print("Menghitung pola radiasi antena satelit (untuk mendapatkan gain di off-axis)...")
    theta_pattern_deg, gain_pattern_dB = calculate_antenna_radiation_pattern(
        frequency_hz_ka_band, D_aperture_m, F_D_ratio, waveguide_radius_m,
        theta_deg_range=(0, 10), num_points=1000 
    )
    print("Pola radiasi antena berhasil dihitung.")

    # --- Tampilkan plot pola radiasi untuk verifikasi ---
    plt.figure(figsize=(9, 6))
    plt.plot(theta_pattern_deg, gain_pattern_dB, label='Calculated Antenna Pattern')
    plt.title('Antenna Radiation Pattern (Satellite Tx - Ka-band)')
    plt.xlabel('Off-Axis Angle (degrees)')
    plt.ylabel('Gain (dB)')
    plt.grid(True)
    plt.ylim([-40, 0]) 
    
    plt.axvline(x=0.25, color='r', linestyle='--', label=f'Expected 3dB Half-BW ({0.25} deg)')
    gain_at_0_25_deg = get_gain_from_pattern(0.25, theta_pattern_deg, gain_pattern_dB)
    plt.axhline(y=gain_at_0_25_deg, color='g', linestyle=':', label=f'Gain at 0.25 deg ({gain_at_0_25_deg:.2f} dB)')
    
    plt.legend()
    plt.show()

    # --- 2. Definisikan Pilihan Beam Satelit ---
    # Format: (Nama Beam, Lintang Pusat Beam, Bujur Pusat Beam)
    beams_info = [
        {"name": "Beam A (Aceh)", "lat": 4.99, "lon": 97.38},
        {"name": "Beam B (Medan)", "lat": 2.56, "lon": 99.98},
        {"name": "Beam C (Palembang)", "lat": -0.5, "lon": 105.0},
        {"name": "Beam D (Bandung Area)", "lat": -6.8, "lon": 107.6}, # Beam yang dekat Bandung
        {"name": "Beam E (Semarang)", "lat": -7.0, "lon": 109.0},
        {"name": "Beam F (Surabaya)", "lat": -7.0, "lon": 112.0},
        {"name": "Beam G (Pontianak)", "lat": 0.0, "lon": 109.0},
        {"name": "Beam H (Makassar)", "lat": -5.0, "lon": 120.0},
        {"name": "Beam I (Merauke)", "lat": -8.0, "lon": 135.0},
        {"name": "Beam J (SSP - Center)", "lat": 0.0, "lon": 146.0}, # Beam di SSP
    ]
    
    print(f"\nJumlah spot beam yang akan diproses: {len(beams_info)}")
    print(f"Titik Observasi (Bandung): Lintang {LAT_OBSERVASI}, Bujur {LON_OBSERVASI}")

    # --- 3. Hitung Jarak dan Directivity untuk Semua Beam ---
    # Tambahkan kolom untuk jarak dan directivity yang diterima di Bandung
    for beam in beams_info:
        # Hitung jarak Haversine dari pusat beam ke Bandung
        beam["distance_to_obs_km"] = haversine_distance(
            LAT_OBSERVASI, LON_OBSERVASI, beam["lat"], beam["lon"]
        )
        
        # Hitung sudut off-axis dari satelit ke Bandung, RELATIF TERHADAP PUSAT BEAM INI
        theta_off_axis_at_bandung = calculate_off_axis_angle(
            satellite_longitude, 
            beam["lat"], beam["lon"], 
            LAT_OBSERVASI, LON_OBSERVASI
        )
        
        # Dapatkan gain (directivity) di Bandung dari pola radiasi
        beam["directivity_at_obs_dBi"] = get_gain_from_pattern(
            theta_off_axis_at_bandung, 
            theta_pattern_deg, 
            gain_pattern_dB
        )

    # --- 4. Peringkat Awal Berdasarkan Jarak ---
    beams_info.sort(key=lambda x: x["distance_to_obs_km"])

    print("\n--- Peringkat Semua Beam Berdasarkan Jarak ke Bandung ---")
    for i, beam in enumerate(beams_info):
        print(f"  {i+1}. {beam['name']} (Lat: {beam['lat']:.2f}, Lon: {beam['lon']:.2f}) - "
              f"Jarak: {beam['distance_to_obs_km']:.2f} km, "
              f"Directivity: {beam['directivity_at_obs_dBi']:.2f} dBi")

    # --- 5. Ambil 3 Besar Berdasarkan Jarak ---
    top_n_by_distance = 3
    if len(beams_info) < top_n_by_distance:
        top_n_by_distance = len(beams_info) # Sesuaikan jika beam kurang dari 3
    
    top_3_beams_by_distance = beams_info[:top_n_by_distance]

    # --- 6. Peringkat Akhir (dari 3 Besar) Berdasarkan Directivity ---
    top_3_beams_by_distance.sort(key=lambda x: x["directivity_at_obs_dBi"], reverse=True)

    print(f"\n--- {top_n_by_distance} Besar Beam (Berdasarkan Jarak, Lalu Directivity di Bandung) ---")
    print("Beam yang direkomendasikan untuk perhitungan CINR adalah yang nomor 1.")
    best_beam = None
    for i, beam in enumerate(top_3_beams_by_distance):
        print(f"  {i+1}. {beam['name']} (Lat: {beam['lat']:.2f}, Lon: {beam['lon']:.2f}) - "
              f"Directivity di Bandung: {beam['directivity_at_obs_dBi']:.2f} dBi, "
              f"Jarak ke Bandung: {beam['distance_to_obs_km']:.2f} km")
        if i == 0:
            best_beam = beam # Simpan beam terbaik

    if not best_beam:
        print("\nTidak ada beam yang ditemukan atau dipilih sebagai yang terbaik.")
        exit() # Keluar jika tidak ada best beam

    print(f"\n--- Beam Terbaik yang Terpilih: {best_beam['name']} ---")
    print(f"  Pusat Beam: Lat {best_beam['lat']:.4f}, Lon {best_beam['lon']:.4f}")
    print(f"  Directivity Satelit (Gain) di Bandung: {best_beam['directivity_at_obs_dBi']:.2f} dBi")
    print(f"  Jarak ke Bandung: {best_beam['distance_to_obs_km']:.2f} km")


    # --- Bagian Visualisasi Peta ---
    print("\n--- Visualisasi Peta Beam dan Titik Observasi ---")
    
    # Hitung batas min/max lat/lon dari beam_centers untuk menentukan pusat dan zoom awal
    all_lats = [beam['lat'] for beam in beams_info] + [LAT_OBSERVASI]
    all_lons = [beam['lon'] for beam in beams_info] + [LON_OBSERVASI]

    min_lat_map = np.min(all_lats)
    max_lat_map = np.max(all_lats)
    min_lon_map = np.min(all_lons)
    max_lon_map = np.max(all_lons)

    center_map_lat = (min_lat_map + max_lat_map) / 2
    center_map_lon = (min_lon_map + max_lon_map) / 2
    
    m = folium.Map(
        location=[center_map_lat, center_map_lon],
        zoom_start=5, # Zoom awal, akan ditimpa oleh fit_bounds
        tiles='OpenStreetMap', 
        attr='OpenStreetMap contributors'
    )

    # Tambahkan bounds untuk peta (agar lebih fokus pada Indonesia dan titik observasi)
    padding_lat = 3 # Derajat
    padding_lon = 5 # Derajat
    m.fit_bounds([
        [min_lat_map - padding_lat, min_lon_map - padding_lon],
        [max_lat_map + padding_lat, max_lon_map + padding_lon]
    ])

    # Warna untuk setiap level kontur (dari terluar ke terdalam)
    ellipse_colors = {
        -3: '#FF0000', # Merah (paling luar)
        -2: '#FFFF00',    # Kuning
        -1: '#00FF00'     # Hijau (paling dalam)
    }

    # Hitung half-beamwidths untuk plotting visual
    inv_interp_func = interp1d(gain_pattern_dB, theta_pattern_deg, kind='linear', fill_value="extrapolate", bounds_error=False)
    theta_3dB_physical = inv_interp_func(-3) 
    if np.isnan(theta_3dB_physical) or theta_3dB_physical < 1e-9:
        theta_3dB_physical = 0.25 
    visual_3dB_half_beamwidth = 3.334 # <--- SESUAIKAN NILAI INI UNTUK UKURAN VISUAL BEAM!

    theta_minus_1dB_physical = inv_interp_func(-1.0) 
    theta_minus_2dB_physical = inv_interp_func(-2.0)
    
    ratio_1dB_to_3dB = theta_minus_1dB_physical / theta_3dB_physical if theta_3dB_physical > 1e-9 else 0.6 
    ratio_2dB_to_3dB = theta_minus_2dB_physical / theta_3dB_physical if theta_3dB_physical > 1e-9 else 0.8 

    gain_levels_to_plot = [-3, -2, -1] 
    sorted_gain_levels_for_plot = sorted(gain_levels_to_plot) 
    
    half_beamwidths_for_plotting = {}
    for level_dB in sorted_gain_levels_for_plot:
        if level_dB == -3:
            half_beamwidths_for_plotting[level_dB] = visual_3dB_half_beamwidth
        elif level_dB == -2:
            half_beamwidths_for_plotting[level_dB] = visual_3dB_half_beamwidth * ratio_2dB_to_3dB
        elif level_dB == -1:
            half_beamwidths_for_plotting[level_dB] = visual_3dB_half_beamwidth * ratio_1dB_to_3dB
        else:
            half_beamwidths_for_plotting[level_dB] = inv_interp_func(level_dB)

    # Plot titik pusat setiap beam sebagai Marker
    for beam in beams_info:
        folium.CircleMarker(
            location=[beam['lat'], beam['lon']], 
            radius=3,
            color='black',
            fill=True,
            fill_color='black',
            fill_opacity=1,
            popup=f"{beam['name']} Center<br>Lat: {beam['lat']:.4f}, Lon: {beam['lon']:.4f}<br>Directivity (Obs): {beam['directivity_at_obs_dBi']:.2f} dBi"
        ).add_to(m)

    # Plot elips untuk setiap level gain yang diinginkan
    for level_dB in sorted_gain_levels_for_plot: 
        beam_radius_for_ellipse = half_beamwidths_for_plotting[level_dB]
        
        for beam in beams_info:
            major_axis_deg, minor_axis_deg, angle_for_rotation_deg = generate_spot_beam_ellipse_properties(
                beam['lat'], beam['lon'], beam_radius_for_ellipse, satellite_lon=satellite_longitude
            )
            
            ellipse_points = generate_ellipse_points(
                beam['lat'], beam['lon'], major_axis_deg, minor_axis_deg, angle_for_rotation_deg
            )
            
            folium.PolyLine(
                locations=ellipse_points,
                color=ellipse_colors[level_dB],
                weight=1.5,
                opacity=0.7,
                popup=f"{beam['name']} - {level_dB}dB Contour<br>"
                      f"Lat: {beam['lat']:.4f}, Lon: {beam['lon']:.4f}<br>"
                      f"Major Axis: {major_axis_deg:.3f} deg, Minor Axis: {minor_axis_deg:.3f} deg"
            ).add_to(m)

    # Tambahkan marker untuk titik observasi (Bandung)
    folium.CircleMarker(
        location=[LAT_OBSERVASI, LON_OBSERVASI],
        radius=7,
        color='purple',
        fill=True,
        fill_color='purple',
        fill_opacity=1,
        popup=f"Titik Observasi (Bandung)<br>Lat: {LAT_OBSERVASI}, Lon: {LON_OBSERVASI}"
    ).add_to(m)

    # Tambahkan marker untuk 3 beam terbaik
    for i, beam in enumerate(top_3_beams_by_distance):
        icon_color = 'green' if i == 0 else 'orange'
        icon_name = 'star' if i == 0 else 'info-sign'
        popup_text = f"Rank {i+1}: {beam['name']}<br>Directivity di Bandung: {beam['directivity_at_obs_dBi']:.2f} dB"
        
        folium.Marker(
            location=[beam['lat'], beam['lon']],
            icon=folium.Icon(color=icon_color, icon=icon_name),
            popup=popup_text
        ).add_to(m)

    folium.LayerControl().add_to(m)

    output_file = 'best_beam_selection_map.html'
    m.save(output_file)
    print(f"Peta best beam selection berhasil dibuat dan disimpan di {output_file}")
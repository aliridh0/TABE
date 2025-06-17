import math

def get_user_input(prompt, default_value, unit=""):
    """
    Mendapatkan input dari pengguna dengan nilai default opsional.
    """
    while True:
        user_choice = input(f"Gunakan nilai default untuk {prompt}? (y/n) [Default: {default_value}{unit}]: ").lower()
        if user_choice == 'y' or user_choice == '':
            return default_value
        elif user_choice == 'n':
            try:
                manual_input = float(input(f"Masukkan nilai {prompt} secara manual{unit}: "))
                return manual_input
            except ValueError:
                print("Input tidak valid. Harap masukkan angka.")
        else:
            print("Pilihan tidak valid. Harap masukkan 'y' atau 'n'.")

def hitung_cinr_downlink_ka_band():
    """
    Menghitung CINR (Carrier-to-Interference-plus-Noise Ratio) hanya untuk jalur downlink (Ka-band).
    Membutuhkan input directivity antena, frekuensi, dan jarak.
    Parameter lain menggunakan nilai default yang bisa diubah secara manual.
    """

    print("--- Kalkulator CINR Downlink Satelit Ka-band ---")
    print("Silakan masukkan parameter dasar berikut:")

    try:
        # Input dasar dari pengguna
        directivity_satelit_tx_dBi = float(input("Directivity Antena Satelit (Pengirim Downlink) [dBi]: "))
        directivity_stasiun_bumi_rx_dBi = float(input("Directivity Antena Stasiun Bumi (Penerima Downlink) [dBi]: "))
        frekuensi_GHz = float(input("Frekuensi Downlink [GHz]: "))
        jarak_km = float(input("Jarak Antara Satelit dan Stasiun Bumi [km]: "))
    except ValueError:
        print("Input tidak valid. Pastikan Anda memasukkan angka.")
        return

    print("\n--- Konfigurasi Asumsi Parameter ---")
    print("Anda bisa menggunakan nilai default atau memasukkan nilai manual untuk setiap parameter.")

    # --- ASUMSI PARAMETER DENGAN OPSI MANUAL INPUT ---
    # Efisiensi Antena
    efisiensi_antena_lin = get_user_input("Efisiensi Antena (linear, 0-1)", 0.65) # Referensi: Stutzman & Thiele (2012)

    # Daya Pancar Satelit (Output Power dari Transponder)
    pt_satelit_dBW = get_user_input("Daya Pancar Satelit (Tx Power) [dBW]", 17.0, " dBW") # Referensi: Publikasi Industri / Datasheet Produsen

    # Suhu Derau Sistem Stasiun Bumi (sebagai penerima downlink)
    tsys_stasiun_bumi_K = get_user_input("Suhu Derau Sistem Stasiun Bumi (Rx) [K]", 100.0, " K") # Referensi: Datasheet Produsen LNA Ka-band

    # Bandwidth (lebar pita)
    bandwidth_Hz = get_user_input("Bandwidth Sinyal [Hz]", 36e6, " Hz") # Referensi: Situs Web Operator Satelit / Datasheet Transponder

    # Kerugian Lain-lain (Miscellaneous Losses) di Jalur Downlink
    # Termasuk feeder loss, pointing loss, redaman hujan (di Bandung), atmosfer, dll.
    losses_downlink_dB = get_user_input("Total Kerugian Lain-lain (Downlink) [dB]", 3.0, " dB") # Referensi: ITU-R P.618, Jurnal terkait redaman hujan Ka-band di tropis

    # C/I (Carrier-to-Interference Ratio) untuk Downlink
    c_to_i_dB = get_user_input("Carrier-to-Interference Ratio (C/I) [dB]", 20.0, " dB") # Referensi: Buku Teks / Rekomendasi ITU-R S.740 / Jurnal Interferensi Ka-band

    # --- KONSTANTA FISIKA ---
    konstanta_boltzmann_k_dB = -228.6  # dBW/Hz/K

    print("\n--- Melakukan Perhitungan ---")

    # --- PERHITUNGAN ---

    # 1. Hitung Gain Antena (dari Directivity dan Efisiensi)
    # Gain Tx adalah gain antena satelit (pengirim downlink)
    gain_satelit_tx_dBi = directivity_satelit_tx_dBi + 10 * math.log10(efisiensi_antena_lin)
    # Gain Rx adalah gain antena stasiun bumi (penerima downlink)
    gain_stasiun_bumi_rx_dBi = directivity_stasiun_bumi_rx_dBi + 10 * math.log10(efisiensi_antena_lin)

    print(f"  Gain Antena Satelit (Tx Downlink): {gain_satelit_tx_dBi:.2f} dBi")
    print(f"  Gain Antena Stasiun Bumi (Rx Downlink): {gain_stasiun_bumi_rx_dBi:.2f} dBi")

    # 2. Hitung Free Space Loss (FSL) untuk Downlink
    frekuensi_MHz = frekuensi_GHz * 1000
    fsl_dB = 32.44 + 20 * math.log10(jarak_km) + 20 * math.log10(frekuensi_MHz)
    print(f"  Free Space Loss (FSL) Downlink: {fsl_dB:.2f} dB")

    # 3. Perhitungan Downlink (Satelit ke Stasiun Bumi)
    eirp_downlink_dBW = pt_satelit_dBW + gain_satelit_tx_dBi
    print(f"  EIRP Satelit (Pengirim Downlink): {eirp_downlink_dBW:.2f} dBW")

    # G/T Stasiun Bumi (Stasiun Bumi sebagai penerima)
    g_per_t_stasiun_bumi_dBK = gain_stasiun_bumi_rx_dBi - 10 * math.log10(tsys_stasiun_bumi_K)
    print(f"  G/T Stasiun Bumi (Penerima Downlink): {g_per_t_stasiun_bumi_dBK:.2f} dB/K")

    # C/N Downlink (Carrier-to-Noise Ratio)
    c_to_n_downlink_dB = eirp_downlink_dBW - fsl_dB - losses_downlink_dB + g_per_t_stasiun_bumi_dBK - konstanta_boltzmann_k_dB - 10 * math.log10(bandwidth_Hz)
    print(f"  C/N Downlink: {c_to_n_downlink_dB:.2f} dB")

    # 4. Hitung CINR (dengan mempertimbangkan C/I)
    # Konversi C/N Downlink dan C/I ke bentuk linear untuk penjumlahan noise dan interference
    c_to_n_downlink_linear = 10**(c_to_n_downlink_dB / 10)
    c_to_i_linear = 10**(c_to_i_dB / 10)

    if c_to_n_downlink_linear <= 0 or c_to_i_linear <= 0:
        print("\nTidak dapat menghitung CINR. Pastikan C/N Downlink dan C/I lebih besar dari nol (dalam linear).")
        print("Ini mungkin menunjukkan sinyal terlalu lemah atau interferensi/noise terlalu dominan.")
        return

    # Rumus gabungan untuk CINR = 1 / (1/C/N + 1/C/I)
    cinr_linear = 1 / (1 / c_to_n_downlink_linear + 1 / c_to_i_linear)
    cinr_dB = 10 * math.log10(cinr_linear)

    print("\n--- HASIL AKHIR DOWNLINK ---")
    print(f"CINR (Carrier-to-Interference-plus-Noise Ratio) Downlink: {cinr_dB:.2f} dB")

    # Evaluasi kualitas CINR
    if cinr_dB < 0:
        print("\nPERINGATAN: Nilai CINR negatif mengindikasikan bahwa derau dan/atau interferensi lebih besar dari sinyal. Kualitas link sangat buruk.")
    elif cinr_dB < 6:
        print("\nPERINGATAN: Nilai CINR rendah mengindikasikan kualitas link yang buruk atau hanya cocok untuk modulasi yang sangat robust.")
    elif cinr_dB < 10:
        print("\nCATATAN: CINR pada batas minimum untuk sebagian besar modulasi digital standar.")
    else:
        print("\nKualitas link downlink terlihat baik untuk sebagian besar modulasi digital.")

# Panggil fungsi untuk menjalankan program
hitung_cinr_downlink_ka_band()
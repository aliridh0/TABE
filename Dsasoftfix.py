import math 

# Masukan
eff = 0.4364
c = 3e8 
freq = float(input("Frequency (GHz): "))
bw3dB = float(input("BW 3 dB (deg): "))

"============================================================================================"
# Function to calculate wavelength
def calculate_wavelength(frequency):
    return c / (frequency*10**9)

# Function to calculate diameter aperture
def calculate_diameter_aperture(wavelength, bw3dB_rad):
    return (1.06505 * wavelength) / bw3dB_rad

# Function to calculate antenna aperture
def calculate_aperture_antenna(Diameter_ap):
    return (math.pi * Diameter_ap**2) / 4  # Corrected formula for aperture area

# Function to calculate Directivity antenna
def calculate_directivity_antenna(ap_antenna, wavelength):
    return (eff * 4 * math.pi * ap_antenna) / (wavelength ** 2)

# Function to convert directivity to dB
def directivity_to_dB(directivity):
    return 10 * math.log10(directivity)

"==========================================================================================================="
# Convert beamwidth from degrees to radians
bw3dB_rad = bw3dB * (math.pi / 180)

# Calculate wavelength
wavelength = calculate_wavelength(freq)

# Calculate diameter aperture
Diameter_ap = calculate_diameter_aperture(wavelength, bw3dB_rad)

# Calculate aperture antenna
ap_antenna = calculate_aperture_antenna(Diameter_ap)

# Calculate directivity
directivity = calculate_directivity_antenna(ap_antenna, wavelength)

#convert to dB
dir_dB = directivity_to_dB(directivity)

"==========================================================================================================="
# Output
print(f"wavelength = {wavelength} m")
print(f"Diameter aperture = {Diameter_ap} m")
print(f"Aperture antenna = {ap_antenna} m^2")
print(f"Directivity = {directivity}")
print(f"Directivity_dB = {dir_dB} dB")

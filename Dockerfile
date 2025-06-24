# Langkah 1: Gunakan base image resmi Python
FROM python:3.10-slim

# Langkah 2: Tetapkan direktori kerja di dalam container
WORKDIR /app

# Langkah 3: Salin file requirements terlebih dahulu untuk optimasi cache
COPY requirements.txt .

# Langkah 4: Install semua dependensi yang diperlukan
RUN pip install --no-cache-dir -r requirements.txt

# Langkah 5: Salin semua sisa file proyek ke dalam direktori kerja
# Ini termasuk semua file .py dan file .pem Anda
COPY . .

# Langkah 6: Perintah untuk menjalankan aplikasi menggunakan Gunicorn
# Pastikan 'main:app' sesuai dengan nama file utama dan variabel Flask Anda
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "main:app"]
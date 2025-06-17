import mysql.connector
from mysql.connector import pooling, Error
from contextlib import contextmanager

# ------- parameter koneksi ------- #
HOST     = '127.0.0.1'  
PORT     = 3306
DATABASE = 'satelite_monitoring'
USER     = 'root'
PASSWORD = ''
POOL_SIZE = 5                                  # koneksi di-reuse

# ------- buat pool koneksi ------- #
connection_pool = pooling.MySQLConnectionPool(
    pool_name="mypool",
    pool_size=POOL_SIZE,
    host=HOST,
    port=PORT,
    database=DATABASE,
    user=USER,
    password=PASSWORD,
    charset="utf8",
    autocommit=True,
)

@contextmanager
def get_conn():
    """A context manager to handle MySQL connection."""
    
    # Inisialisasi conn ke None untuk keamanan jika get_connection() gagal
    conn = None
    try:
        conn = connection_pool.get_connection()
        yield conn
    finally:
        # Kode Anda di sini sudah benar. 
        # Untuk koneksi dari pool, .close() akan mengembalikannya ke pool.
        if conn:
            conn.close()
import mysql.connector
from mysql.connector import pooling, Error
from contextlib import contextmanager
import os
from dotenv import load_dotenv

load_dotenv()

HOST        = os.getenv('DB_HOST')
PORT        = int(os.getenv('DB_PORT', 3306))
DATABASE    = os.getenv('DB_DATABASE')
USER        = os.getenv('DB_USER')
PASSWORD    = os.getenv('DB_PASSWORD')
POOL_SIZE   = int(os.getenv('DB_POOL_SIZE', 5))
SSL_FILENAME = os.getenv('SSL_CERT_FILENAME')

if not all([HOST, DATABASE, USER, PASSWORD, SSL_FILENAME]):
    raise ValueError("Satu atau lebih environment variables database (HOST, DATABASE, USER, PASSWORD, SSL_FILENAME) tidak diatur.")

try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    SSL_CERT_PATH = os.path.join(current_dir, SSL_FILENAME)
except NameError:
    SSL_CERT_PATH = SSL_FILENAME # Fallback

if not os.path.exists(SSL_CERT_PATH):
    raise FileNotFoundError(f"File sertifikat SSL tidak ditemukan di path: {SSL_CERT_PATH}")

try:
    connection_pool = pooling.MySQLConnectionPool(
        pool_name="mypool",
        pool_size=POOL_SIZE,
        host=HOST,
        port=PORT,
        database=DATABASE,
        user=USER,
        password=PASSWORD,
        charset="utf8",
        ssl_ca=SSL_CERT_PATH,
        ssl_verify_cert=False,
        tls_versions=['TLSv1.2']
    )
    print("Secure connection pool created successfully from .env configuration.")

except Error as err:
    print(f"Error creating connection pool: {err}")
    exit(1)


@contextmanager
def get_conn():
    """A context manager to handle MySQL connection from the pool."""
    conn = None
    try:
        conn = connection_pool.get_connection()
        conn.autocommit = False 
        yield conn
    except Error as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()
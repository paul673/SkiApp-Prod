import sqlite3
from config import DB_PATH
import pandas as pd

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def query_dataframe(query,conn):
    df = pd.read_sql_query(query, conn)
    return df



import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON;")

conn.execute("""
    DELETE FROM estimated_forecast;
""")

conn.execute("""
    DELETE FROM measured_forecast;
""")

conn.execute("""
    DELETE FROM temp_measured_forecast;
""")

conn.commit()
conn.close()
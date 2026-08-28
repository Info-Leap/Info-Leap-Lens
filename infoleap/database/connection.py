import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def get_engine():
    url = os.getenv("NEON_DATABASE_URL")
    if not url:
        raise ValueError("NEON_DATABASE_URL not set in .env")
    return create_engine(url, pool_pre_ping=True)

def run_schema():
    engine = get_engine()
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    with engine.begin() as conn:
        for statement in sql.split(';'):
            stmt = statement.strip()
            if stmt:
                conn.execute(text(stmt))
    print("[DB] Schema applied successfully.")

def test_connection():
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            print(f"[DB] Connection OK: {result.fetchone()}")
        return True
    except Exception as e:
        print(f"[DB] Connection FAILED: {e}")
        return False


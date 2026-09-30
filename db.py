import psycopg2
from psycopg2.errors import UndefinedTable
import os

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "resume_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "1234")

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

def create_table():
    """Create the resumes table if it doesn't exist (no drop)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS resumes (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255),
            phone VARCHAR(50),
            degree VARCHAR(255),
            email VARCHAR(255),
            linkedin VARCHAR(255),
            github VARCHAR(255),
            address TEXT,
            skills TEXT,
            experience TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            raw_text TEXT,
            file_name VARCHAR(255),
            file_path VARCHAR(512)
        );
        ALTER TABLE resumes ADD COLUMN IF NOT EXISTS file_path VARCHAR(512);
    """)
    conn.commit()
    cur.close()
    conn.close()

GENERIC_EMAILS = {'email@email.com', 'hello@reallygreatsite.com', 'example@example.com'}
GENERIC_PHONES = {'1234567890', '3868683442', '5417543010'}

def is_duplicate(phone, email, skills, experience, file_name=None, candidate_name=None):
    cur_fname = (file_name or '').strip().lower()
    cur_name = (candidate_name or '').strip().lower()
    cur_phone_clean = ''.join(filter(str.isdigit, phone or ''))
    cur_email_clean = (email or '').strip().lower()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, phone, email, file_name FROM resumes;")
        rows = cur.fetchall()
    except Exception:
        rows = []
    finally:
        cur.close()
        conn.close()

    for row in rows:
        ex_id, ex_name, ex_phone, ex_email, ex_fname = row
        ex_fname = (ex_fname or '').strip().lower()
        ex_name = (ex_name or '').strip().lower()
        ex_phone_clean = ''.join(filter(str.isdigit, ex_phone or ''))
        ex_email_clean = (ex_email or '').strip().lower()

        # 1. Exact file_name match
        if cur_fname and cur_fname == ex_fname:
            return True, ex_id

        # 2. Same candidate name + matching phone or email
        if cur_name and cur_name == ex_name:
            if cur_phone_clean and cur_phone_clean == ex_phone_clean:
                return True, ex_id
            if cur_email_clean and cur_email_clean == ex_email_clean:
                return True, ex_id

        # 3. Matching non-generic phone AND non-generic email
        if (cur_phone_clean and ex_phone_clean and cur_phone_clean not in GENERIC_PHONES and cur_phone_clean == ex_phone_clean) and \
           (cur_email_clean and ex_email_clean and cur_email_clean not in GENERIC_EMAILS and cur_email_clean == ex_email_clean):
            return True, ex_id

    return False, None

def insert_resume(data):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO resumes
        (name, phone, degree, email, linkedin, github, address, skills, experience, raw_text, file_name, file_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
    """, (
        data.get('name'),
        data.get('phone'),
        data.get('degree'),
        data.get('email'),
        data.get('linkedin'),
        data.get('github'),
        data.get('address'),
        data.get('skills'),
        data.get('experience'),
        data.get('raw_text'),
        data.get('file_name'),
        data.get('file_path')
    ))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def fetch_all_resumes():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM resumes ORDER BY id ASC;")
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        result = [dict(zip(columns, row)) for row in rows]
    except UndefinedTable:
        result = []
    finally:
        cur.close()
        conn.close()
    return result

def search_resumes(query):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT * FROM resumes 
            WHERE 
                name ILIKE %s OR
                skills ILIKE %s OR
                experience ILIKE %s OR
                degree ILIKE %s OR
                raw_text ILIKE %s
            ORDER BY id ASC
            LIMIT 10;
        """, (f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%'))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        result = [dict(zip(columns, row)) for row in rows]
    except Exception:
        result = []
    finally:
        cur.close()
        conn.close()
    return result

def delete_all_resumes():
    """Delete all rows from the resumes table and reset ID sequence to 1."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE resumes RESTART IDENTITY;")
    conn.commit()
    cur.close()
    conn.close()

def delete_resume_by_id(resume_id):
    """Delete a single resume by its ID."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM resumes WHERE id = %s;", (resume_id,))
    conn.commit()
    cur.close()
    conn.close()
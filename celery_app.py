import os
import re
import zipfile
import pdfplumber
import docx
from io import BytesIO
from celery import Celery
from dotenv import load_dotenv

from db import is_duplicate, insert_resume

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "1234")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "resume_db")

db_broker = f"sqla+postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
db_backend = f"db+postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

celery_app = Celery(
    "resume_tasks",
    broker=db_broker,
    backend=db_backend
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True
)

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

def extract_text_from_pdf(file_bytes):
    text = ""
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

def extract_text_from_docx(file_bytes):
    doc = docx.Document(BytesIO(file_bytes))
    return "\n".join([para.text for para in doc.paragraphs if para.text])

def extract_text_from_zip(file_bytes):
    combined_text = ""
    with zipfile.ZipFile(BytesIO(file_bytes)) as z:
        for filename in z.namelist():
            ext = os.path.splitext(filename)[1].lower()
            if filename.startswith('__MACOSX') or filename.startswith('.'):
                continue
            with z.open(filename) as f:
                content = f.read()
            if ext == '.pdf':
                combined_text += extract_text_from_pdf(content) + "\n"
            elif ext == '.docx':
                combined_text += extract_text_from_docx(content) + "\n"
    return combined_text

@celery_app.task(name="process_folder_async_task")
def process_folder_async_task(folder_path):
    """
    Celery task that processes a folder of resumes asynchronously in the background.
    """
    from app import parse_resume, save_file_locally
    
    if not os.path.exists(folder_path):
        return {"error": f"Folder '{folder_path}' does not exist."}

    valid_exts = ('.pdf', '.docx', '.zip')
    file_list = [os.path.join(folder_path, f) for f in os.listdir(folder_path) 
                 if os.path.isfile(os.path.join(folder_path, f)) and f.lower().endswith(valid_exts)]

    if not file_list:
        return {"error": f"No supported resume files (.pdf, .docx, .zip) found in '{folder_path}'."}

    inserted = 0
    duplicates = 0
    failed = 0

    for fpath in file_list:
        fname = os.path.basename(fpath)
        ext = os.path.splitext(fname)[1].lower()
        try:
            with open(fpath, "rb") as f:
                file_bytes = f.read()

            if ext == '.pdf':
                text = extract_text_from_pdf(file_bytes)
            elif ext == '.docx':
                text = extract_text_from_docx(file_bytes)
            elif ext == '.zip':
                text = extract_text_from_zip(file_bytes)
            else:
                continue

            if not text.strip():
                failed += 1
                continue

            parsed = parse_resume(text)
            parsed['file_name'] = fname
            local_path = save_file_locally(file_bytes, fname)
            parsed['file_path'] = local_path

            phone = parsed.get('phone')
            email = parsed.get('email')
            cname = parsed.get('name')
            is_dup, _ = is_duplicate(phone, email, parsed.get('skills'), parsed.get('experience'), file_name=fname, candidate_name=cname)

            if is_dup:
                duplicates += 1
            else:
                insert_resume(parsed)
                inserted += 1
        except Exception:
            failed += 1

    return {
        "total_files": len(file_list),
        "inserted": inserted,
        "duplicates": duplicates,
        "failed": failed
    }

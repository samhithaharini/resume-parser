import streamlit as st
import streamlit.components.v1 as components
import os
import re
import zipfile
import base64
from io import BytesIO
import pdfplumber
import docx
import pandas as pd
from datetime import datetime
from collections import Counter
import threading
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from db import (
    create_table, is_duplicate, insert_resume, fetch_all_resumes,
    search_resumes, delete_all_resumes, delete_resume_by_id
)
from chatbot import search_and_answer
from celery_app import process_folder_async_task

load_dotenv()

# ---------- Directory & Environment Setup ----------
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
RESUMES_FOLDER_PATH = os.getenv("RESUMES_FOLDER_PATH", r"C:\Users\user\DATA SCIENCE\data-science-projects\Resumes")

# ---------- Top-Level Helper Functions ----------
def save_file_locally(file_bytes, file_name):
    target_path = os.path.join(UPLOADS_DIR, file_name)
    with open(target_path, "wb") as f:
        f.write(file_bytes)
    return target_path

def render_open_in_new_tab_button(file_path, file_name, key):
    if not file_path or not os.path.exists(file_path):
        target = os.path.join(UPLOADS_DIR, file_name)
        if os.path.exists(target):
            file_path = target
        else:
            st.caption("*(File unavailable locally)*")
            return

    try:
        ext = os.path.splitext(file_name)[1].lower()
        with open(file_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")
        
        mime_type = "application/pdf" if ext == ".pdf" else "application/octet-stream"
        
        html_code = f"""
            <button id="btn_{key}" style="
                background-color: #0284c7;
                color: white;
                border: none;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
                border-radius: 6px;
                cursor: pointer;
                width: 100%;
                font-family: system-ui, -apple-system, sans-serif;
            ">👁️ View {file_name} in New Tab</button>

            <script>
            document.getElementById("btn_{key}").onclick = function() {{
                var base64Data = "{b64_data}";
                var byteCharacters = atob(base64Data);
                var byteNumbers = new Array(byteCharacters.length);
                for (var i = 0; i < byteCharacters.length; i++) {{
                    byteNumbers[i] = byteCharacters.charCodeAt(i);
                }}
                var byteArray = new Uint8Array(byteNumbers);
                var blob = new Blob([byteArray], {{type: '{mime_type}'}});
                var fileURL = URL.createObjectURL(blob);
                window.open(fileURL, '_blank');
            }};
            </script>
        """
        components.html(html_code, height=45)
    except Exception as e:
        st.caption(f"*(Error reading file: {e})*")

def extract_text_from_pdf(file_bytes):
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        text = "\n".join([page.extract_text() or "" for page in pdf.pages])
    return text

def extract_text_from_docx(file_bytes):
    doc = docx.Document(BytesIO(file_bytes))
    text = "\n".join([para.text for para in doc.paragraphs])
    return text

def extract_text_from_zip(file_bytes):
    texts = []
    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith('/'):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in ['.pdf', '.docx']:
                data = zf.read(name)
                if ext == '.pdf':
                    texts.append(extract_text_from_pdf(data))
                elif ext == '.docx':
                    texts.append(extract_text_from_docx(data))
    return "\n\n".join(texts)

def extract_name(text, lines):
    invalid_name_phrases = {
        'deep learning', 'machine learning', 'artificial intelligence', 'data science',
        'data scientist', 'software engineer', 'full stack', 'web developer', 'python developer',
        'frontend', 'backend', 'devops', 'curriculum vitae', 'resume', 'contact', 'summary',
        'profile', 'objective', 'skills', 'experience', 'projects', 'education', 'certifications',
        'declaration', 'personal details', 'b.e', 'b.tech', 'm.tech', 'b.sc', 'm.sc', 'bca', 'mca',
        'computer science', 'engineering', 'deep learning engineer', 'ai engineer', 'email', 'employment history'
    }

    name_match = re.search(r'Name\s*[:\-]\s*([^\n]+)', text, re.I)
    if name_match:
        raw = name_match.group(1).strip()
        candidate = re.split(r'[,|–—]', raw)[0].strip()
        if candidate.lower() not in invalid_name_phrases:
            return candidate.title()

    email_match = re.search(r'([a-zA-Z0-9._%+-]+)@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    email_prefix = ""
    if email_match:
        email_prefix = re.sub(r'[^a-zA-Z]', '', email_match.group(1).split('.')[0]).lower()

    if len(email_prefix) >= 3:
        for line in lines[:15]:
            clean_line = re.sub(r'[^\w\s\.-]', '', line).strip()
            if not clean_line or any(p in clean_line.lower() for p in invalid_name_phrases):
                continue
            if '@' in line or 'http' in line or '+' in line or 'Phone' in line or 'Email' in line:
                continue
            words = clean_line.split()
            for w in words:
                w_clean = re.sub(r'[^a-zA-Z]', '', w).lower()
                if w_clean and (w_clean in email_prefix or email_prefix in w_clean):
                    if len(words) <= 4:
                        return clean_line.title()

    for line in lines[:15]:
        clean_line = re.sub(r'[^\w\s\.-]', '', line).strip()
        line_lower = clean_line.lower()
        if not clean_line or any(p in line_lower for p in invalid_name_phrases):
            continue
        if '@' in line or 'http' in line or 'github' in line or 'linkedin' in line or '+' in line or re.search(r'\d', line):
            continue
        words = clean_line.split()
        if 1 <= len(words) <= 4:
            if all(re.match(r'^[A-Za-z\.]+$', w) for w in words):
                return clean_line.title()

    return "Unknown Candidate"

def extract_phone(text):
    patterns = [
        re.compile(r'(?:(?:\+|00)\d{1,3}[\s.-]?)?\d{10}'),
        re.compile(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}'),
        re.compile(r'\d{3}\s\d{3}\s\d{4}')
    ]
    for pattern in patterns:
        matches = pattern.findall(text)
        for match in matches:
            clean = re.sub(r'[\s\-\.\(\)]', '', match)
            if len(clean) >= 10:
                return match.strip()
    ten_digit = re.search(r'\b\d{10}\b', text)
    if ten_digit:
        return ten_digit.group(0)
    return None

def extract_degree(text, lines):
    degree_acronyms = [
        r'B\.?Tech\.?', r'B\.?E\.?', r'B\.?Sc\.?', r'B\.?A\.?', r'B\.?Com\.?',
        r'M\.?Tech\.?', r'M\.?E\.?', r'M\.?Sc\.?', r'M\.?A\.?', r'M\.?Com\.?',
        r'MBA', r'MCA', r'Ph\.?D', r'MS', r'Master', r'Masters', r'Master\'s',
        r'Bachelor', r'Bachelors', r'Bachelor\'s', r'Diploma',
        r'Associate', r'BBA', r'BCA', r'PGDM'
    ]
    pattern = re.compile(r'\b(?:' + '|'.join(degree_acronyms) + r')\b', re.I)
    match = pattern.search(text)
    if match:
        return match.group(0).strip()
    for line in lines:
        m = pattern.search(line)
        if m:
            return m.group(0).strip()
    return None

def extract_experience(text, lines):
    exp_label = re.search(r'(?:Total\s+)?Experience\s*[:\-]\s*([^\n]+)', text, re.I)
    if exp_label:
        label_val = exp_label.group(1).strip()
        if re.search(r'\d+\s*(?:month|year|yr|mo)', label_val, re.I):
            return label_val

    exp_section_text = ""
    exp_headers = [
        r'\bwork\s+experience\b', r'\bprofessional\s+experience\b', r'\bexperience\b',
        r'\binternship[s]?\b', r'\binternship\s+experience\b', r'\bemployment\s+history\b'
    ]
    exp_header_pattern = re.compile(r'(' + '|'.join(exp_headers) + r')', re.I)

    education_headers = [r'\beducation\b', r'\bacademic\s+details\b', r'\bqualifications\b', r'\bacademics\b']
    edu_header_pattern = re.compile(r'(' + '|'.join(education_headers) + r')', re.I)

    sections = re.split(r'\n(?=[A-Z\s]{3,25}\n|\n)', text)
    for sec in sections:
        sec_lower = sec.lower()
        if exp_header_pattern.search(sec_lower) and not edu_header_pattern.search(sec_lower):
            exp_section_text += sec + "\n"

    if not exp_section_text.strip():
        filtered_lines = []
        in_edu_section = False
        for line in lines:
            if edu_header_pattern.search(line.lower()):
                in_edu_section = True
                continue
            if exp_header_pattern.search(line.lower()):
                in_edu_section = False
            if not in_edu_section:
                filtered_lines.append(line)
        exp_section_text = "\n".join(filtered_lines)

    total_months = 0
    norm_text = ' '.join(exp_section_text.splitlines())
    months_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

    def parse_month(mon):
        if mon:
            mon = mon.strip().lower()[:3]
            return months_map.get(mon, 1)
        return 1

    pattern1 = re.compile(r'([A-Za-z]{3,9})\s*(\d{4})\s*[-–—]\s*([A-Za-z]{3,9})\s*(\d{4})', re.I)
    for match in pattern1.finditer(norm_text):
        start_mon = match.group(1).strip()
        start_yr = int(match.group(2))
        end_mon = match.group(3).strip()
        end_yr = int(match.group(4))
        sm = parse_month(start_mon)
        em = parse_month(end_mon)
        months = (end_yr - start_yr) * 12 + (em - sm)
        if months > 0:
            total_months += months

    pattern2 = re.compile(r'([A-Za-z]{3,9})\s*(\d{4})\s*[-–—]\s*(?:Present|Current)', re.I)
    for match in pattern2.finditer(norm_text):
        start_mon = match.group(1).strip()
        start_yr = int(match.group(2))
        sm = parse_month(start_mon)
        now = datetime.now()
        months = (now.year - start_yr) * 12 + (now.month - sm)
        if months > 0:
            total_months += months

    pattern5 = re.compile(r'(\d{1,2})/(\d{4})\s*[-–—]\s*(\d{1,2})/(\d{4})')
    for match in pattern5.finditer(norm_text):
        start_mon = int(match.group(1))
        start_yr = int(match.group(2))
        end_mon = int(match.group(3))
        end_yr = int(match.group(4))
        months = (end_yr - start_yr) * 12 + (end_mon - start_mon)
        if months > 0:
            total_months += months

    if total_months == 0:
        years_match = re.search(r'(\d+)\s*(?:years|yrs)', norm_text, re.I)
        if years_match:
            total_months = int(years_match.group(1)) * 12
        months_match = re.search(r'(\d+)\s*(?:months|mos)', norm_text, re.I)
        if months_match:
            total_months += int(months_match.group(1))

    years = total_months // 12
    months = total_months % 12
    if years > 0 and months > 0:
        return f"{years} years {months} months"
    elif years > 0:
        return f"{years} years"
    elif months > 0:
        return f"{months} months"
    else:
        return "0 months"

def extract_skills(text):
    skill_list = [
        'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'ruby', 'php', 'swift', 'kotlin',
        'go', 'rust', 'scala', 'perl', 'r', 'matlab', 'sql', 'mongodb', 'postgresql', 'mysql', 'oracle',
        'redis', 'elasticsearch', 'docker', 'kubernetes', 'jenkins', 'git', 'svn', 'jira', 'confluence',
        'aws', 'azure', 'gcp', 'linux', 'unix', 'windows', 'macos', 'react', 'angular', 'vue', 'node',
        'django', 'flask', 'spring', 'hibernate', 'asp.net', 'rails', 'laravel', 'express', 'bootstrap',
        'tailwind', 'sass', 'webpack', 'babel', 'npm', 'yarn', 'docker-compose', 'ansible', 'terraform',
        'circleci', 'github actions', 'gitlab ci', 'prometheus', 'grafana', 'elk', 'splunk',
        'machine learning', 'deep learning', 'artificial intelligence', 'nlp', 'computer vision',
        'data science', 'data analysis', 'data visualization', 'tableau', 'power bi', 'excel',
        'agile', 'scrum', 'kanban', 'leadership', 'communication', 'problem solving', 'teamwork',
        'project management', 'product management', 'ux', 'ui', 'user experience', 'user interface',
        'figma', 'sketch', 'adobe xd', 'photoshop', 'illustrator', 'indesign', 'after effects',
        'premiere pro', 'final cut', 'autocad', 'solidworks', 'spss', 'stata',
        'hadoop', 'spark', 'kafka', 'flink', 'airflow', 'mlflow', 'tensorflow', 'pytorch', 'keras',
        'scikit-learn', 'pandas', 'numpy', 'scipy', 'seaborn', 'plotly', 'opencv', 'nltk', 'spacy',
        'transformers', 'huggingface', 'llm', 'generative ai', 'chatgpt', 'stable diffusion',
        'auditing', 'financial reporting', 'accounting'
    ]
    skill_list_sorted = sorted(set(skill_list), key=len, reverse=True)
    pattern_str = r'\b(?:' + '|'.join(re.escape(skill).replace(r'\ ', r'\s+') for skill in skill_list_sorted) + r')\b'
    skill_regex = re.compile(pattern_str, re.I)
    matches = skill_regex.findall(text)
    seen = set()
    unique = []
    for skill in matches:
        lower = skill.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(skill)
    
    if unique:
        formatted = []
        for skill in unique:
            if skill.isupper():
                formatted.append(skill)
            else:
                formatted.append(skill.title())
        return ', '.join(formatted)
    else:
        skills_section = re.search(r'(?:skills|technical skills|core competencies)[:\s]*(.*?)(?:\n\n|\n[A-Z]|$)', text, re.I | re.DOTALL)
        if skills_section:
            skills_text = skills_section.group(1).strip()
            raw_skills = re.split(r'[,;•\n]', skills_text)
            clean_list = []
            for s in raw_skills:
                s_clean = s.strip()
                if s_clean and not re.search(r'\d', s_clean) and len(s_clean) > 1 and s_clean != '.':
                    clean_list.append(s_clean)
            return ', '.join(clean_list)
        return None

def parse_resume(text):
    lines = text.splitlines()
    data = {
        'name': extract_name(text, lines),
        'phone': extract_phone(text),
        'degree': extract_degree(text, lines),
        'email': None,
        'linkedin': None,
        'github': None,
        'address': None,
        'skills': extract_skills(text),
        'experience': extract_experience(text, lines),
        'raw_text': text
    }

    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    emails = email_pattern.findall(text)
    if emails:
        data['email'] = emails[0].strip()

    linkedin_pattern = re.compile(r'(?:https?://)?(?:www\.)?linkedin\.com/in/([^/\s]+)')
    linkedin_match = linkedin_pattern.search(text)
    if linkedin_match:
        data['linkedin'] = linkedin_match.group(0)

    github_pattern = re.compile(r'(?:https?://)?(?:www\.)?github\.com/([^/\s]+)')
    github_match = github_pattern.search(text)
    if github_match:
        data['github'] = github_match.group(0)

    for line in lines:
        if re.search(r'address', line, re.I):
            data['address'] = line.strip()
            break
    if not data['address']:
        for line in lines:
            if re.search(r'\d+\s+[A-Za-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct)', line, re.I):
                data['address'] = line.strip()
                break
    return data

def process_files(files):
    results = []
    for file in files:
        file_bytes = file.read()
        file_name = file.name
        ext = os.path.splitext(file_name)[1].lower()
        try:
            if ext == '.pdf':
                text = extract_text_from_pdf(file_bytes)
            elif ext == '.docx':
                text = extract_text_from_docx(file_bytes)
            elif ext == '.zip':
                text = extract_text_from_zip(file_bytes)
            else:
                continue
            if not text.strip():
                st.warning(f"⚠️ Could not extract text from {file_name}")
                continue
            parsed = parse_resume(text)
            parsed['file_name'] = file_name
            local_path = save_file_locally(file_bytes, file_name)
            parsed['file_path'] = local_path
            results.append(parsed)
        except Exception as e:
            st.error(f"❌ Error processing {file_name}: {str(e)}")
    return results

# ---------- Background Asynchronous Sync Worker ----------
BG_SYNC_STATE = {
    "is_running": False,
    "processed": 0,
    "total": 0,
    "status": None
}

def _process_single_file(fpath):
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
            return "skip"

        if not text.strip():
            return "failed"

        parsed = parse_resume(text)
        parsed['file_name'] = fname
        local_path = save_file_locally(file_bytes, fname)
        parsed['file_path'] = local_path

        phone = parsed.get('phone')
        email = parsed.get('email')
        cname = parsed.get('name')
        is_dup, _ = is_duplicate(phone, email, parsed.get('skills'), parsed.get('experience'), file_name=fname, candidate_name=cname)

        if is_dup:
            return "duplicate"
        else:
            insert_resume(parsed)
            return "inserted"
    except Exception:
        return "failed"

def _run_background_sync(folder_path):
    global BG_SYNC_STATE
    BG_SYNC_STATE["is_running"] = True
    BG_SYNC_STATE["processed"] = 0
    BG_SYNC_STATE["total"] = 0
    BG_SYNC_STATE["status"] = None

    if not os.path.exists(folder_path):
        BG_SYNC_STATE["status"] = {"error": f"Folder '{folder_path}' does not exist."}
        BG_SYNC_STATE["is_running"] = False
        return

    valid_exts = ('.pdf', '.docx', '.zip')
    file_list = [os.path.join(folder_path, f) for f in os.listdir(folder_path) 
                 if os.path.isfile(os.path.join(folder_path, f)) and f.lower().endswith(valid_exts)]

    if not file_list:
        BG_SYNC_STATE["status"] = {"error": f"No supported resume files (.pdf, .docx, .zip) found in '{folder_path}'."}
        BG_SYNC_STATE["is_running"] = False
        return

    BG_SYNC_STATE["total"] = len(file_list)
    inserted = 0
    duplicates = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_process_single_file, fpath) for fpath in file_list]
        for fut in futures:
            res = fut.result()
            BG_SYNC_STATE["processed"] += 1
            if res == "inserted":
                inserted += 1
            elif res == "duplicate":
                duplicates += 1
            elif res == "failed":
                failed += 1

    BG_SYNC_STATE["status"] = {
        "total_files": len(file_list),
        "inserted": inserted,
        "duplicates": duplicates,
        "failed": failed
    }
    BG_SYNC_STATE["is_running"] = False

def start_background_sync(folder_path):
    if BG_SYNC_STATE["is_running"]:
        return False
    t = threading.Thread(target=_run_background_sync, args=(folder_path,), daemon=True)
    t.start()
    return True

# ---------- Page Config & Session State ----------
st.set_page_config(
    page_title="Resume Parser & AI Chatbot",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'resumes_data' not in st.session_state:
    st.session_state.resumes_data = []
if 'upload_key' not in st.session_state:
    st.session_state.upload_key = 0
if 'show_confirm_delete' not in st.session_state:
    st.session_state.show_confirm_delete = False

# ---------- Ensure DB Table Exists ----------
try:
    create_table()
    st.session_state.resumes_data = fetch_all_resumes()
except Exception as e:
    st.error(f"❌ Database connection failed: {e}")
    st.stop()

# ---------- Custom CSS ----------
st.markdown("""
    <style>
        .main { background-color: #f5f7fa; }
        .stApp { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .chat-message {
            padding: 1rem;
            border-radius: 0.5rem;
            margin-bottom: 1rem;
            display: flex;
            flex-direction: column;
            color: #000000 !important;
        }
        .chat-message * {
            color: #000000 !important;
        }
        .user-message {
            background-color: #e3f2fd;
            align-self: flex-end;
            border-bottom-right-radius: 0;
            color: #000000 !important;
        }
        .user-message * {
            color: #000000 !important;
        }
        .bot-message {
            background-color: #f5f5f5;
            align-self: flex-start;
            border-bottom-left-radius: 0;
            color: #000000 !important;
        }
        .bot-message * {
            color: #000000 !important;
        }
        .stButton button { width: 100%; }
    </style>
""", unsafe_allow_html=True)

# ---------- Sidebar Dashboard ----------
with st.sidebar:
    st.header("📊 Dashboard")
    total = len(st.session_state.resumes_data)
    st.metric("Total Resumes", total)

    st.divider()
    st.subheader("📂 Local Resumes Folder")

    if st.button("⚡ Upload / Sync from Folder", use_container_width=True, type="primary"):
        try:
            process_folder_async_task.delay(RESUMES_FOLDER_PATH)
        except Exception:
            pass
        start_background_sync(RESUMES_FOLDER_PATH)
        st.toast("🚀 Uploading folder resumes in background...")

    if not BG_SYNC_STATE["is_running"] and BG_SYNC_STATE["status"]:
        status = BG_SYNC_STATE["status"]
        st.session_state.resumes_data = fetch_all_resumes()
        if "error" in status:
            st.error(f"❌ {status['error']}")
        else:
            st.markdown("#### 📋 Upload Status")
            st.success("✅ **Status:** Successful")
            st.write(f"📁 **Total Files Processed:** {status['total_files']}")
            st.write(f"📥 **Uploaded/Inserted:** {status['inserted']}")
            if status['duplicates'] > 0:
                st.warning(f"⚠️ **Duplicate Files:** {status['duplicates']}")
            else:
                st.write(f"⚠️ **Duplicate Files:** 0")
            if status['failed'] > 0:
                st.error(f"❌ **Failed Files:** {status['failed']}")

    st.divider()

    if total > 0:
        valid_skills = []
        for r in st.session_state.resumes_data:
            if r.get('skills'):
                raw_s = r['skills'].split(',')
                for s in raw_s:
                    s_clean = s.strip()
                    if s_clean and not re.search(r'\d', s_clean) and len(s_clean) > 1 and s_clean != '.':
                        valid_skills.append(s_clean.title())
        if valid_skills:
            skill_counts = Counter(valid_skills).most_common(5)
            top_skills = [skill for skill, _ in skill_counts]
            st.write("**Top Skills:**")
            st.write(", ".join(top_skills))
        else:
            st.write("**Top Skills:** None")
    else:
        st.write("No resumes uploaded yet.")

    st.divider()
    if st.button("🗑️ Delete All Database Data", use_container_width=True):
        st.session_state.show_confirm_delete_sidebar = True

    if st.session_state.get('show_confirm_delete_sidebar', False):
        st.warning("⚠️ Delete ALL database resumes? ID sequence will restart from 1.")
        col_sb1, col_sb2 = st.columns(2)
        with col_sb1:
            if st.button("✅ Yes, Delete", key="sb_del_yes", use_container_width=True):
                delete_all_resumes()
                st.session_state.resumes_data = fetch_all_resumes()
                st.session_state.show_confirm_delete_sidebar = False
                BG_SYNC_STATE["status"] = None
                st.session_state.pop('last_sync_status', None)
                st.success("All data deleted! Next ID starts from 1.")
                st.rerun()
        with col_sb2:
            if st.button("❌ Cancel", key="sb_del_no", use_container_width=True):
                st.session_state.show_confirm_delete_sidebar = False
                st.rerun()

# ---------- Header ----------
col1, col2 = st.columns([3, 1])
with col1:
    st.title("📄 Resume Parser & AI Chatbot")
    st.markdown("Upload, parse, and query resumes using AI")
with col2:
    if st.button("🔄 Refresh Data"):
        st.session_state.resumes_data = fetch_all_resumes()
        st.rerun()

# ---------- Tabs ----------
tab1, tab2, tab3 = st.tabs(["📤 Upload & Parse", "💬 AI Chatbot", "📋 All Resumes"])

# ============================================================
# TAB 1: UPLOAD & PARSE
# ============================================================
with tab1:
    st.subheader("📤 Upload Resumes")
    st.markdown("Upload PDF, DOCX, or ZIP files containing resumes")

    uploaded_files = st.file_uploader(
        "Choose files or drag & drop",
        type=["pdf", "docx", "zip"],
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.upload_key}",
        help="Supported formats: .pdf, .docx, .zip"
    )


    def extract_text_from_docx(file_bytes):
        doc = docx.Document(BytesIO(file_bytes))
        text = "\n".join([para.text for para in doc.paragraphs])
        return text

    def extract_text_from_zip(file_bytes):
        texts = []
        with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith('/'):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in ['.pdf', '.docx']:
                    data = zf.read(name)
                    if ext == '.pdf':
                        texts.append(extract_text_from_pdf(data))
                    elif ext == '.docx':
                        texts.append(extract_text_from_docx(data))
        return "\n\n".join(texts)

    def extract_name(text, lines):
        skip_headers = re.compile(r'^(resume|curriculum vitae|cv|contact|education|experience|skills|projects|objective|summary|profile|about me)$', re.I)
        role_keywords = re.compile(r'\b(developer|engineer|designer|analyst|manager|consultant|architect|lead|specialist|associate|intern|trainee|executive|director|head|product|project|program|software|data|ux|ui|full[- ]stack|front[- ]end|back[- ]end|devops|machine learning|ai|cloud|security|network|system|database|administrator)\b', re.I)
        name_match = re.search(r'Name\s*[:]\s*([^\n]+)', text, re.I)
    def extract_name(text, lines):
        invalid_name_phrases = {
            'deep learning', 'machine learning', 'artificial intelligence', 'data science',
            'data scientist', 'software engineer', 'full stack', 'web developer', 'python developer',
            'frontend', 'backend', 'devops', 'curriculum vitae', 'resume', 'contact', 'summary',
            'profile', 'objective', 'skills', 'experience', 'projects', 'education', 'certifications',
            'declaration', 'personal details', 'b.e', 'b.tech', 'm.tech', 'b.sc', 'm.sc', 'bca', 'mca',
            'computer science', 'engineering', 'deep learning engineer', 'ai engineer'
        }

        # 1. Check explicit "Name:"
        name_match = re.search(r'Name\s*[:\-]\s*([^\n]+)', text, re.I)
        if name_match:
            raw = name_match.group(1).strip()
            candidate = re.split(r'[,|–—]', raw)[0].strip()
            if candidate.lower() not in invalid_name_phrases:
                return candidate.title()

        # 2. Extract email prefix to match candidate name
        email_match = re.search(r'([a-zA-Z0-9._%+-]+)@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        email_prefix = ""
        if email_match:
            email_prefix = re.sub(r'[^a-zA-Z]', '', email_match.group(1).split('.')[0]).lower()

        # 3. Match email prefix with top 15 lines
        if len(email_prefix) >= 3:
            for line in lines[:15]:
                clean_line = re.sub(r'[^\w\s\.-]', '', line).strip()
                if not clean_line or any(p in clean_line.lower() for p in invalid_name_phrases):
                    continue
                if '@' in line or 'http' in line or '+' in line or 'Phone' in line or 'Email' in line:
                    continue
                words = clean_line.split()
                for w in words:
                    w_clean = re.sub(r'[^a-zA-Z]', '', w).lower()
                    if w_clean and (w_clean in email_prefix or email_prefix in w_clean):
                        if len(words) <= 4:
                            return clean_line.title()

        # 4. Fallback: Scan top 15 lines for person name pattern
        for line in lines[:15]:
            clean_line = re.sub(r'[^\w\s\.-]', '', line).strip()
            line_lower = clean_line.lower()
            if not clean_line or any(p in line_lower for p in invalid_name_phrases):
                continue
            if '@' in line or 'http' in line or 'github' in line or 'linkedin' in line or '+' in line or re.search(r'\d', line):
                continue
            words = clean_line.split()
            if 1 <= len(words) <= 4:
                if all(re.match(r'^[A-Za-z\.]+$', w) for w in words):
                    return clean_line.title()

        return "Unknown Candidate"

    def extract_phone(text):
        patterns = [
            re.compile(r'(?:(?:\+|00)\d{1,3}[\s.-]?)?\d{10}'),
            re.compile(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}'),
            re.compile(r'\d{3}\s\d{3}\s\d{4}')
        ]
        for pattern in patterns:
            matches = pattern.findall(text)
            for match in matches:
                clean = re.sub(r'[\s\-\.\(\)]', '', match)
                if len(clean) >= 10:
                    return match.strip()
        ten_digit = re.search(r'\b\d{10}\b', text)
        if ten_digit:
            return ten_digit.group(0)
        return None

    def extract_degree(text, lines):
        degree_acronyms = [
            r'B\.?Tech\.?', r'B\.?E\.?', r'B\.?Sc\.?', r'B\.?A\.?', r'B\.?Com\.?',
            r'M\.?Tech\.?', r'M\.?E\.?', r'M\.?Sc\.?', r'M\.?A\.?', r'M\.?Com\.?',
            r'MBA', r'MCA', r'Ph\.?D', r'MS', r'Master', r'Masters', r'Master\'s',
            r'Bachelor', r'Bachelors', r'Bachelor\'s', r'Diploma',
            r'Associate', r'BBA', r'BCA', r'PGDM'
        ]
        pattern = re.compile(r'\b(?:' + '|'.join(degree_acronyms) + r')\b', re.I)
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
        for line in lines:
            m = pattern.search(line)
            if m:
                return m.group(0).strip()
        return None

    def extract_experience(text, lines):
        # 1. Look for explicit Experience label (e.g. "Experience: 2 months")
        exp_label = re.search(r'(?:Total\s+)?Experience\s*[:\-]\s*([^\n]+)', text, re.I)
        if exp_label:
            label_val = exp_label.group(1).strip()
            if re.search(r'\d+\s*(?:month|year|yr|mo)', label_val, re.I):
                return label_val

        # 2. Extract Experience Section text only
        exp_section_text = ""
        exp_headers = [
            r'\bwork\s+experience\b', r'\bprofessional\s+experience\b', r'\bexperience\b',
            r'\binternship[s]?\b', r'\binternship\s+experience\b', r'\bemployment\s+history\b'
        ]
        exp_header_pattern = re.compile(r'(' + '|'.join(exp_headers) + r')', re.I)

        education_headers = [r'\beducation\b', r'\bacademic\s+details\b', r'\bqualifications\b', r'\bacademics\b']
        edu_header_pattern = re.compile(r'(' + '|'.join(education_headers) + r')', re.I)

        sections = re.split(r'\n(?=[A-Z\s]{3,25}\n|\n)', text)
        for sec in sections:
            sec_lower = sec.lower()
            if exp_header_pattern.search(sec_lower) and not edu_header_pattern.search(sec_lower):
                exp_section_text += sec + "\n"

        if not exp_section_text.strip():
            filtered_lines = []
            in_edu_section = False
            for line in lines:
                if edu_header_pattern.search(line.lower()):
                    in_edu_section = True
                    continue
                if exp_header_pattern.search(line.lower()):
                    in_edu_section = False
                if not in_edu_section:
                    filtered_lines.append(line)
            exp_section_text = "\n".join(filtered_lines)

        total_months = 0
        norm_text = ' '.join(exp_section_text.splitlines())
        months_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

        def parse_month(mon):
            if mon:
                mon = mon.strip().lower()[:3]
                return months_map.get(mon, 1)
            return 1

        pattern1 = re.compile(r'([A-Za-z]{3,9})\s*(\d{4})\s*[-–—]\s*([A-Za-z]{3,9})\s*(\d{4})', re.I)
        for match in pattern1.finditer(norm_text):
            start_mon = match.group(1).strip()
            start_yr = int(match.group(2))
            end_mon = match.group(3).strip()
            end_yr = int(match.group(4))
            sm = parse_month(start_mon)
            em = parse_month(end_mon)
            months = (end_yr - start_yr) * 12 + (em - sm)
            if months > 0:
                total_months += months

        pattern2 = re.compile(r'([A-Za-z]{3,9})\s*(\d{4})\s*[-–—]\s*(?:Present|Current)', re.I)
        for match in pattern2.finditer(norm_text):
            start_mon = match.group(1).strip()
            start_yr = int(match.group(2))
            sm = parse_month(start_mon)
            now = datetime.now()
            months = (now.year - start_yr) * 12 + (now.month - sm)
            if months > 0:
                total_months += months

        pattern5 = re.compile(r'(\d{1,2})/(\d{4})\s*[-–—]\s*(\d{1,2})/(\d{4})')
        for match in pattern5.finditer(norm_text):
            start_mon = int(match.group(1))
            start_yr = int(match.group(2))
            end_mon = int(match.group(3))
            end_yr = int(match.group(4))
            months = (end_yr - start_yr) * 12 + (end_mon - start_mon)
            if months > 0:
                total_months += months

        if total_months == 0:
            years_match = re.search(r'(\d+)\s*(?:years|yrs)', norm_text, re.I)
            if years_match:
                total_months = int(years_match.group(1)) * 12
            months_match = re.search(r'(\d+)\s*(?:months|mos)', norm_text, re.I)
            if months_match:
                total_months += int(months_match.group(1))

        years = total_months // 12
        months = total_months % 12
        if years > 0 and months > 0:
            return f"{years} years {months} months"
        elif years > 0:
            return f"{years} years"
        elif months > 0:
            return f"{months} months"
        else:
            return "0 months"

    def extract_skills(text):
        skill_list = [
            'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'ruby', 'php', 'swift', 'kotlin',
            'go', 'rust', 'scala', 'perl', 'r', 'matlab', 'sql', 'mongodb', 'postgresql', 'mysql', 'oracle',
            'redis', 'elasticsearch', 'docker', 'kubernetes', 'jenkins', 'git', 'svn', 'jira', 'confluence',
            'aws', 'azure', 'gcp', 'linux', 'unix', 'windows', 'macos', 'react', 'angular', 'vue', 'node',
            'django', 'flask', 'spring', 'hibernate', 'asp.net', 'rails', 'laravel', 'express', 'bootstrap',
            'tailwind', 'sass', 'webpack', 'babel', 'npm', 'yarn', 'docker-compose', 'ansible', 'terraform',
            'circleci', 'github actions', 'gitlab ci', 'prometheus', 'grafana', 'elk', 'splunk',
            'machine learning', 'deep learning', 'artificial intelligence', 'nlp', 'computer vision',
            'data science', 'data analysis', 'data visualization', 'tableau', 'power bi', 'excel',
            'agile', 'scrum', 'kanban', 'leadership', 'communication', 'problem solving', 'teamwork',
            'project management', 'product management', 'ux', 'ui', 'user experience', 'user interface',
            'figma', 'sketch', 'adobe xd', 'photoshop', 'illustrator', 'indesign', 'after effects',
            'premiere pro', 'final cut', 'autocad', 'solidworks', 'spss', 'stata',
            'hadoop', 'spark', 'kafka', 'flink', 'airflow', 'mlflow', 'tensorflow', 'pytorch', 'keras',
            'scikit-learn', 'pandas', 'numpy', 'scipy', 'seaborn', 'plotly', 'opencv', 'nltk', 'spacy',
            'transformers', 'huggingface', 'llm', 'generative ai', 'chatgpt', 'stable diffusion',
            'adobe', 'ai', 'ml'
        ]
        skill_list_sorted = sorted(set(skill_list), key=len, reverse=True)
        pattern_str = r'\b(?:' + '|'.join(re.escape(skill).replace(r'\ ', r'\s+') for skill in skill_list_sorted) + r')\b'
        skill_regex = re.compile(pattern_str, re.I)
        matches = skill_regex.findall(text)
        seen = set()
        unique = []
        for skill in matches:
            lower = skill.lower()
            if lower not in seen:
                seen.add(lower)
                unique.append(skill)
        if unique:
            formatted = []
            for skill in unique:
                if skill.isupper():
                    formatted.append(skill)
                else:
                    formatted.append(skill.title())
            return ', '.join(formatted)
        else:
            skills_section = re.search(r'(?:skills|technical skills|core competencies)[:\s]*(.*?)(?:\n\n|\n[A-Z]|$)', text, re.I | re.DOTALL)
            if skills_section:
                skills_text = skills_section.group(1).strip()
                raw_skills = re.split(r'[,;•\n]', skills_text)
                return ', '.join([s.strip() for s in raw_skills if s.strip()])
            return None

    def parse_resume(text):
        lines = text.splitlines()
        data = {
            'name': extract_name(text, lines),
            'phone': extract_phone(text),
            'degree': extract_degree(text, lines),
            'email': None,
            'linkedin': None,
            'github': None,
            'address': None,
            'skills': extract_skills(text),
            'experience': extract_experience(text, lines),
            'raw_text': text
        }

        email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        emails = email_pattern.findall(text)
        if emails:
            data['email'] = emails[0].strip()

        linkedin_pattern = re.compile(r'(?:https?://)?(?:www\.)?linkedin\.com/in/([^/\s]+)')
        linkedin_match = linkedin_pattern.search(text)
        if linkedin_match:
            data['linkedin'] = linkedin_match.group(0)

        github_pattern = re.compile(r'(?:https?://)?(?:www\.)?github\.com/([^/\s]+)')
        github_match = github_pattern.search(text)
        if github_match:
            data['github'] = github_match.group(0)

        for line in lines:
            if re.search(r'address', line, re.I):
                data['address'] = line.strip()
                break
        if not data['address']:
            for line in lines:
                if re.search(r'\d+\s+[A-Za-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct)', line, re.I):
                    data['address'] = line.strip()
                    break
        return data

    def process_files(files):
        results = []
        for file in files:
            file_bytes = file.read()
            file_name = file.name
            ext = os.path.splitext(file_name)[1].lower()
            try:
                if ext == '.pdf':
                    text = extract_text_from_pdf(file_bytes)
                elif ext == '.docx':
                    text = extract_text_from_docx(file_bytes)
                elif ext == '.zip':
                    text = extract_text_from_zip(file_bytes)
                else:
                    continue
                if not text.strip():
                    st.warning(f"⚠️ Could not extract text from {file_name}")
                    continue
                parsed = parse_resume(text)
                parsed['file_name'] = file_name
                local_path = save_file_locally(file_bytes, file_name)
                parsed['file_path'] = local_path
                results.append(parsed)
            except Exception as e:
                st.error(f"❌ Error processing {file_name}: {str(e)}")
        return results

    # ---------- Process UI ----------
    if uploaded_files:
        st.info(f"📎 {len(uploaded_files)} file(s) uploaded. Click below to extract and store them.")
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            if st.button("⚡ Extract & Store Resumes", use_container_width=True, type="primary"):
                with st.spinner("Processing files..."):
                    parsed_data = process_files(uploaded_files)
                    if parsed_data:
                        inserted = 0
                        duplicates = 0
                        for data in parsed_data:
                            phone = data.get('phone')
                            email = data.get('email')
                            if not phone and not email:
                                st.warning(f"⚠️ No phone or email in {data.get('file_name')} – skipping.")
                                continue
                            is_dup, _ = is_duplicate(phone, email, data.get('skills'), data.get('experience'))
                            if is_dup:
                                duplicates += 1
                            else:
                                insert_resume(data)
                                inserted += 1
                        st.success(f"✅ Inserted {inserted} resumes, skipped {duplicates} duplicates")
                        #st.success(f"Hi Anish!")
                        #print("Hi Anish!")
                        #print("Got it!")
                        st.session_state.resumes_data = fetch_all_resumes()
                        st.rerun()
                    else:
                        st.error("❌ No valid resumes were extracted.")
        with col2:
            # Clear All Data with confirmation
            if st.button("🗑️ Clear All Data", use_container_width=True):
                st.session_state.show_confirm_delete = True
        with col3:
            if st.button("↩️ Reset Uploads", use_container_width=True):
                st.session_state.upload_key += 1
                st.rerun()

        # Show confirmation dialog if delete was requested
        if st.session_state.show_confirm_delete:
            st.warning("⚠️ Are you sure you want to delete ALL resumes from the database? This cannot be undone.")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Yes, Delete All", use_container_width=True):
                    delete_all_resumes()
                    st.session_state.resumes_data = fetch_all_resumes()
                    st.session_state.show_confirm_delete = False
                    st.success("All data deleted successfully.")
                    st.rerun()
            with col2:
                if st.button("❌ Cancel", use_container_width=True):
                    st.session_state.show_confirm_delete = False
                    st.rerun()
    else:
        st.info("📤 Upload files above to begin. After uploading, click 'Extract & Store Resumes'.")

# ============================================================
# TAB 2: AI CHATBOT
# ============================================================
with tab2:
    st.subheader("💬 Resume Query Assistant")
    st.markdown("Ask questions about the resumes stored in the database.")

    resumes = fetch_all_resumes()
    if not resumes:
        st.warning("⚠️ No resumes in the database. Please upload and process resumes first.")
    else:
        st.info(f"📊 {len(resumes)} resumes available for querying")
        chat_container = st.container()
        with chat_container:
            for message in st.session_state.chat_history:
                if message["role"] == "user":
                    st.markdown(f'<div class="chat-message user-message">👤 <b>You:</b><br>{message["content"]}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div class="chat-message bot-message">🤖 <b>Assistant:</b><br>{message["content"]}</div>', unsafe_allow_html=True)

        col1, col2 = st.columns([5, 1])
        with col1:
            user_query = st.text_input("Ask a question:", placeholder="e.g., Who has experience with Python?", key="chat_input")
        with col2:
            send_button = st.button("Send", use_container_width=True)

        st.caption("💡 Suggested questions:")
        suggestion_cols = st.columns(3)
        suggestions = [
            "Show all resumes",
            "Who knows Python?",
            "What are the skills?",
            "Who has the most experience?",
            "Show candidates with degrees",
            "What projects are mentioned?"
        ]
        for idx, suggestion in enumerate(suggestions):
            with suggestion_cols[idx % 3]:
                if st.button(suggestion, use_container_width=True, key=f"sug_{idx}"):
                    st.session_state.chat_history.append({"role": "user", "content": suggestion})
                    response = search_and_answer(suggestion)
                    st.session_state.chat_history.append({"role": "assistant", "content": response})
                    st.rerun()

        if send_button and user_query:
            st.session_state.chat_history.append({"role": "user", "content": user_query})
            with st.spinner("Thinking..."):
                response = search_and_answer(user_query)
                st.session_state.chat_history.append({"role": "assistant", "content": response})
                st.rerun()

        if st.button("🗑️ Clear Chat History"):
            st.session_state.chat_history = []
            st.rerun()

# ============================================================
# TAB 3: ALL RESUMES
# ============================================================
with tab3:
    st.subheader("📋 All Stored Resumes")
    search_query = st.text_input("🔍 Search resumes:", placeholder="Search by name, skills, experience...")
    if search_query:
        results = search_resumes(search_query)
    else:
        results = st.session_state.resumes_data

    if results:
        df = pd.DataFrame(results)
        display_cols = ['id', 'name', 'phone', 'email', 'degree', 'skills', 'experience', 'uploaded_at']
        df_display = df[[c for c in display_cols if c in df.columns]].copy()
        for col in ['skills', 'experience']:
            if col in df_display:
                df_display[col] = df_display[col].apply(lambda x: x[:150] + '...' if isinstance(x, str) and len(x) > 150 else x)
        st.dataframe(df_display, use_container_width=True, height=400)

        col_exp1, col_exp2 = st.columns([3, 1])
        with col_exp1:
            if st.button("📥 Export to CSV"):
                csv = df_display.to_csv(index=False)
                st.download_button(
                    label="Download CSV",
                    data=csv,
                    file_name=f"resumes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )

        st.divider()
        st.subheader("📄 View Stored Resumes in New Tab")
        for r in results:
            fname = r.get('file_name', 'resume.pdf')
            fpath = r.get('file_path') or os.path.join(UPLOADS_DIR, fname)
            col_a, col_b = st.columns([3, 2])
            with col_a:
                st.markdown(f"**👤 Candidate:** {r.get('name', 'Unknown Candidate')} — `{fname}`")
                st.caption(f"Email: {r.get('email', 'N/A')} | Phone: {r.get('phone', 'N/A')} | Degree: {r.get('degree', 'N/A')}")
            with col_b:
                render_open_in_new_tab_button(fpath, fname, key=f"view_{r['id']}")

        st.divider()
        st.subheader("🗑️ Delete Resumes from Database")
        del_col1, del_col2 = st.columns([3, 1])
        with del_col1:
            resume_options = {f"ID #{r['id']}: {r.get('name', 'Unknown')} ({r.get('email', 'No email')})": r['id'] for r in results}
            selected_resume_str = st.selectbox("Select a resume to delete:", options=list(resume_options.keys()), key="select_del_resume")
        with del_col2:
            st.write("") # Spacing
            st.write("")
            if st.button("🗑️ Delete Selected", use_container_width=True, type="primary"):
                target_id = resume_options[selected_resume_str]
                delete_resume_by_id(target_id)
                st.success(f"✅ Deleted resume ID #{target_id}")
                st.session_state.resumes_data = fetch_all_resumes()
                st.rerun()
    else:
        st.info("No resumes found in the database.")
import os
import streamlit as st
from groq import Groq
from dotenv import load_dotenv
from db import fetch_all_resumes

load_dotenv()

def get_chatbot_response(user_query, resumes_data):
    if not resumes_data:
        return "No resume data found in the database. Please upload resumes first."

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return simple_search_response(user_query, resumes_data)

    # Format context
    context = "Here are the resumes stored in the database:\n\n"
    for i, resume in enumerate(resumes_data, 1):
        context += f"Resume #{i}:\n"
        context += f"Name: {resume.get('name', 'N/A')}\n"
        context += f"Phone: {resume.get('phone', 'N/A')}\n"
        context += f"Email: {resume.get('email', 'N/A')}\n"
        context += f"Degree: {resume.get('degree', 'N/A')}\n"
        context += f"Skills: {resume.get('skills', 'N/A')}\n"
        context += f"Experience: {resume.get('experience', 'N/A')}\n"
        context += f"LinkedIn: {resume.get('linkedin', 'N/A')}\n"
        context += f"GitHub: {resume.get('github', 'N/A')}\n"
        context += f"Address: {resume.get('address', 'N/A')}\n"
        context += "-" * 50 + "\n"

    system_prompt = f"""
    You are a resume query assistant. Answer questions based ONLY on the resume data provided.

    RULES:
    1. ONLY use information from the resumes in the context.
    2. If not found, respond: "I don't have information about that in the resumes."
    3. Do NOT make up or infer information.
    4. Be concise and helpful.

    CONTEXT:
    {context}
    """

    try:
        client = Groq(api_key=api_key)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ]
        response = client.chat.completions.create(
            model="groq/compound-mini",  # ✅ Active model available on Groq
            messages=messages,
            temperature=0.1,
            max_tokens=1024
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error generating response: {str(e)}"

def simple_search_response(query, resumes):
    query_lower = query.lower()
    matches = []
    for r in resumes:
        if (query_lower in str(r.get('name', '')).lower() or
            query_lower in str(r.get('skills', '')).lower() or
            query_lower in str(r.get('experience', '')).lower() or
            query_lower in str(r.get('degree', '')).lower()):
            matches.append(r)

    if not matches:
        return "No resumes match your query. Try asking about skills, experience, or names."

    response = f"Found {len(matches)} resume(s) matching your query:\n"
    for r in matches:
        response += f"\n- {r.get('name', 'Unknown')}: {r.get('skills', 'No skills listed')}"
    return response

def search_and_answer(query):
    # Always fetch the latest data from DB
    resumes = fetch_all_resumes()
    if not resumes:
        return "No resumes available in the database. Please upload and process resumes first."

    # Simple retrieval: filter by keyword
    query_lower = query.lower()
    filtered = []
    for resume in resumes:
        if (query_lower in str(resume.get('name', '')).lower() or
            query_lower in str(resume.get('skills', '')).lower() or
            query_lower in str(resume.get('experience', '')).lower() or
            query_lower in str(resume.get('degree', '')).lower()):
            filtered.append(resume)

    if not filtered:
        filtered = resumes  # Use all if no specific match

    return get_chatbot_response(query, filtered)
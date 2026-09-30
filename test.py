import psycopg2
conn = psycopg2.connect(
    host="localhost",
    port=5432,
    dbname="resume_db",
    user="postgres",
    password="your_new_password"
)
print("Connected!")
conn.close()
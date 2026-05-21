import sqlite3
def list_cases():
    conn = sqlite3.connect("medcare.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, patient_id FROM patient_cases;")
    rows = cursor.fetchall()
    print("Cases (id, patient_id):")
    for r in rows:
        print(r)
    conn.close()
list_cases()

import sqlite3, os
path = 'medcare.db'
if not os.path.exists(path):
    print('medcare.db not found; nothing to clear.')
else:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute('PRAGMA foreign_keys=OFF')
    cur.execute('BEGIN TRANSACTION')
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cur.fetchall()]
    for t in tables:
        if t != 'sqlite_sequence':
            cur.execute(f'DROP TABLE IF EXISTS {t}')
    conn.commit()
    conn.close()
    print('Dropped all tables in medcare.db')

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS patients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, age INTEGER, gender TEXT, phone TEXT, email TEXT UNIQUE, password TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS doctors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, doctor_id TEXT UNIQUE, password TEXT, phone TEXT, email TEXT, qualification TEXT, hospital TEXT, experience TEXT, is_available INTEGER DEFAULT 1)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS patient_cases (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER, patient_name TEXT, age INTEGER, gender TEXT, diagnosis TEXT, surgery TEXT, hospital TEXT, doctor TEXT, doctor_id INTEGER, simplified_report TEXT, medications TEXT, physiotherapy TEXT, diet TEXT, recovery_progress TEXT, followup TEXT, file_path TEXT, upload_date TIMESTAMP, FOREIGN KEY(patient_id) REFERENCES patients(id), FOREIGN KEY(doctor_id) REFERENCES doctors(id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, patient_id INTEGER, doctor_id INTEGER, sender_type TEXT, message TEXT, timestamp TIMESTAMP, FOREIGN KEY(case_id) REFERENCES patient_cases(id), FOREIGN KEY(patient_id) REFERENCES patients(id), FOREIGN KEY(doctor_id) REFERENCES doctors(id))''')
    conn.commit()
    conn.close()
    print('Recreated empty schema in medcare.db')

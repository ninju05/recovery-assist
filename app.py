import logging
import traceback
from flask import Flask, render_template, request, redirect, url_for, send_file, session
from werkzeug.utils import secure_filename
import os
import re
import json
import PyPDF2
import sqlite3
import pandas as pd
import pypdfium2
import pytesseract
from datetime import datetime, timezone

logging.basicConfig(filename='app.log', level=logging.DEBUG)

app = Flask(__name__)

# Tesseract OCR
tesseract_cmd = os.environ.get("TESSERACT_CMD", "tesseract")
pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

# Create uploads folder if not exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# -------------------------
# DATABASE INIT
# -------------------------
def init_db():
    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()

        # Patient Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            age INTEGER,
            gender TEXT,
            phone TEXT,
            email TEXT UNIQUE,
            password TEXT
        )
        """)

        # Doctor Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            doctor_id TEXT UNIQUE,
            password TEXT,
            phone TEXT,
            email TEXT,
            qualification TEXT,
            hospital TEXT,
            experience TEXT,
            is_available INTEGER DEFAULT 1
        )
        """)

        # Patient Cases Table (for storing uploaded reports)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS patient_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            patient_name TEXT,
            age INTEGER,
            gender TEXT,
            diagnosis TEXT,
            surgery TEXT,
            hospital TEXT,
            doctor TEXT,
            doctor_id INTEGER,
            simplified_report TEXT,
            medications TEXT,
            physiotherapy TEXT,
            diet TEXT,
            recovery_progress TEXT,
            followup TEXT,
            file_path TEXT,
            upload_date TIMESTAMP,
            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(doctor_id) REFERENCES doctors(id)
        )
        """)

        # Messages Table (for chat between patient and doctor)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER,
            patient_id INTEGER,
            doctor_id INTEGER,
            sender_type TEXT,
            message TEXT,
            timestamp TIMESTAMP,
            FOREIGN KEY(case_id) REFERENCES patient_cases(id),
            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(doctor_id) REFERENCES doctors(id)
        )
        """)

        # Add doctor_recommendations column if not exists
        try:
            cursor.execute("ALTER TABLE patient_cases ADD COLUMN doctor_recommendations TEXT")
        except sqlite3.OperationalError:
            pass

        # Add recovery_tips column if not exists
        try:
            cursor.execute("ALTER TABLE patient_cases ADD COLUMN recovery_tips TEXT")
        except sqlite3.OperationalError:
            pass

        conn.commit()
    finally:
        conn.close()


# Initialize DB on startup
init_db()


# -------------------------
# LOAD NEW DATASETS
# -------------------------
try:
    terms_df = pd.read_excel("datasets/Orthopedic_Terms.xlsx")
    diet_df = pd.read_excel("datasets/Orthopedic_Diet.xlsx")
    recovery_df = pd.read_excel("datasets/Orthopedic_Recovery.xlsx")
    terms_df = terms_df.fillna("")
    diet_df = diet_df.fillna("")
    recovery_df = recovery_df.fillna("")
    print("All 3 Datasets loaded successfully!")
except Exception as e:
    print(f"Error loading datasets: {e}")
    terms_df, diet_df, recovery_df = None, None, None


# --------------------------------
# Extract Patient Info from PDF Text
# --------------------------------
def extract_patient_info(text):
    info = {"patient_name": "Unknown", "age": "-", "gender": "-", "hospital": "-", "doctor": "-"}

    # --- Name ---
    # Format 1 (Tata): "Patient Name Ms. Anjali Singh Age/Gender"
    m = re.search(r'Patient\s+Name\s+(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?)?\s*([A-Za-z][A-Za-z .]+?)\s+Age/Gender', text)
    if m:
        info["patient_name"] = m.group(1).strip().title()
    else:
        # Format 2 (standard): "Patient: IVANA SALTELLI" or "Patient: SALTELLI, IVANA"
        m = re.search(r'Patient:\s+([A-Z][A-Z ,\.]+?)(?:\s+DOB:|\s+MRN:|\n)', text)
        if m:
            raw = m.group(1).strip()
            # Handle "LAST, FIRST" → "First Last"
            if ',' in raw:
                parts = [p.strip().title() for p in raw.split(',', 1)]
                info["patient_name"] = f"{parts[1]} {parts[0]}"
            else:
                info["patient_name"] = raw.title()

    # --- Age ---
    # Format 1 (Tata): "Age/Gender 25 Y"
    m = re.search(r'Age/Gender\s+(\d+)\s*Y', text)
    if m:
        info["age"] = m.group(1)
    else:
        # Format 2: "Age: 36 years" or "36 years"
        m = re.search(r'Age:\s*(\d+)\s*years?', text, re.IGNORECASE)
        if not m:
            m = re.search(r'(\d{1,3})\s*(?:year|yr)s?\s*(?:old)?', text, re.IGNORECASE)
        if m:
            info["age"] = m.group(1)

    # --- Gender ---
    # Format 1 (Tata): "/Female" or "/Male"
    m = re.search(r'Age/Gender[^\n]+?\n+[^/\n]*/\s*(Female|Male)', text, re.IGNORECASE)
    if m:
        info["gender"] = m.group(1).title()
    else:
        # Format 2: "Sex: F" or "Sex: M" or "Sex: Female"
        m = re.search(r'Sex:\s*(Female|Male|F\b|M\b)', text, re.IGNORECASE)
        if m:
            g = m.group(1).strip().upper()
            info["gender"] = "Female" if g.startswith('F') else "Male"
        else:
            m = re.search(r'/(Female|Male)', text, re.IGNORECASE)
            if m:
                info["gender"] = m.group(1).title()

    # --- Hospital ---
    # Format 2: look for known hospital keywords anywhere in first 20 lines
    skip_words = ('documentinfo', 'result type', 'result date', 'result status',
                  'performed by', 'verified by', 'modified by', 'discharge summary')
    hospital_found = False
    for line in text.split('\n')[:20]:
        line = line.strip()
        if line and any(k in line.lower() for k in ('hospital', 'medical center', 'clinic', 'health', 'care')):
            if not any(line.lower().startswith(s) for s in skip_words):
                info["hospital"] = line
                hospital_found = True
                break
    if not hospital_found:
        # Try Location field: "Location: SFMH TEN: ..."
        m = re.search(r'Location:\s*([A-Z]{2,}[A-Z0-9 ]*)', text)
        if m:
            info["hospital"] = m.group(1).strip()
        else:
            # Format 1 (Tata): first non-metadata non-empty line
            for line in text.split('\n'):
                line = line.strip()
                if line and not any(line.lower().startswith(s) for s in skip_words):
                    info["hospital"] = line
                    break

    # --- Doctor ---
    # Format 1 (Tata): "Treating Doctor Dr Jayanta Kumar Laik Treating Doctor Speciality"
    m = re.search(r'Treating\s+Doctor\s+(?:Dr\.?\s+)?([A-Za-z][A-Za-z .]+?)\s+Treating\s+Doctor\s+Speciality', text)
    if m:
        info["doctor"] = m.group(1).strip()
    else:
        # Format 2: "Admitting MD: Patel, Ashish MD" or "Author: Matz, Robert MD" or "SURGEON: Gaurav Abbi"
        for pattern in [
            r'(?:Admitting MD|Attending|Author|Surgeon|Physician):\s*([A-Za-z][A-Za-z ,\.]+?(?:MD|DO|Dr\.?))',
            r'SURGEON:\s*([A-Za-z][A-Za-z ,\.]+?)(?:\n|,)',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                raw = m.group(1).strip().rstrip(',')
                # Normalize "Last, First MD" → "First Last"
                raw = re.sub(r'\s*(MD|DO|M\.D\.|D\.O\.)\s*$', '', raw, flags=re.IGNORECASE).strip()
                if ',' in raw:
                    parts = [p.strip().title() for p in raw.split(',', 1)]
                    info["doctor"] = f"{parts[1]} {parts[0]}"
                else:
                    info["doctor"] = raw.title()
                break

    return info


# --------------------------------
# Group & Deduplicate Recovery Tips
# --------------------------------
EMERGENCY_SYMPTOMS = {
    'fever', 'chest pain', 'shortness of breath', 'loss of bladder control', 'emergency'
}
WARNING_SYMPTOMS = {
    'calf swelling', 'increasing pain', 'numbness', 'wound discharge',
    'redness', 'leg weakness', 'severe swelling', 'bleeding',
    'vomiting', 'dizziness', 'constipation', 'muscle spasm', 'infection'
}
ACTIVITY_RESTRICTIONS = {
    'do not', 'avoid', 'restrict', 'limit', 'lifting', 'driving', 'strenuous', 'weight bearing', 'bending', 'twisting', 'no heavy'
}

def group_recovery_tips(items):
    """Deduplicate and group raw recovery tip strings into structured categories."""
    groups = {
        'emergency': [],
        'warning': [],
        'activity': [],
        'general': []
    }
    seen_tips = set()

    for tip in items:
        tip_clean = tip.strip()
        if not tip_clean: 
            continue
            
        tip_lower = tip_clean.lower()
        # Deduplicate by first 60 chars to remove very similar points
        key = tip_lower[:60]
        
        if key in seen_tips:
            continue
        seen_tips.add(key)

        if any(s in tip_lower for s in EMERGENCY_SYMPTOMS):
            groups['emergency'].append(tip_clean)
        elif any(s in tip_lower for s in WARNING_SYMPTOMS):
            groups['warning'].append(tip_clean)
        elif any(s in tip_lower for s in ACTIVITY_RESTRICTIONS):
            groups['activity'].append(tip_clean)
        else:
            groups['general'].append(tip_clean)

    return groups


# --------------------------------
# Dynamic Detection from CSVs
# --------------------------------
def detect_case(text):
    text_lower = text.lower()

    case_data = {
        "patient_name": "Unknown",
        "age": "-",
        "gender": "-",
        "hospital": "-",
        "doctor": "-",
        "diagnosis": "Not detected",
        "surgery": "-",
        "simplified_report": "Unable to detect specific condition from the report.",
        "medications": [],
        "physiotherapy": [],
        "diet": [],
        "recovery_progress": [],
        "followup": "-"
    }

    if recovery_df is None:
        return case_data

    # Extract personal/hospital details from PDF text
    extracted = extract_patient_info(text)
    case_data.update(extracted)

    # Extract surgery from PDF text
    m = re.search(r'PROCEDURE PERFORMED:\s*([^\n]+)', text, re.IGNORECASE)
    if not m:
        m = re.search(r'(?:surgery|procedure|operation)\s*(?:performed|done|completed)?:\s*([^\n]{10,})', text, re.IGNORECASE)
    if m:
        case_data["surgery"] = m.group(1).strip()

    # Extract follow-up from PDF text
    text_clean = text.replace('\xa0', ' ')
    m = re.search(r'follow[- ]?up[\s\S]{0,200}\.', text_clean, re.IGNORECASE)
    if m:
        followup = re.sub(r'\s+', ' ', m.group(0)).strip()
        # Stop at first sentence end
        case_data["followup"] = followup

    # Extract real discharge medications from PDF text
    med_section = re.search(
        r'Discharge Medications?[\s\S]{0,50}\n([\s\S]+?)(?:Discontinued Meds|Discharge Disposition|Discharge Diet|Discharge Activity|$)',
        text_clean, re.IGNORECASE
    )
    if med_section:
        raw_meds = med_section.group(1)
        # Split on numbered entries like "1. " or "\n2. "
        entries = re.split(r'(?:^|\n)\s*\d+\.\s+', raw_meds)
        parsed_meds = []
        for entry in entries:
            entry = entry.strip()
            if not entry or len(entry) < 10:
                continue
            # Drug name: prefer brand in parentheses, else generic before first space+digit
            name_m = re.match(r'^([A-Za-z][A-Za-z0-9 \-]+?)(?:\(([^)]+)\))?(?:\s+\d|\s+Tab|\s+Cap|\s+By|$)', entry)
            drug_name = ''
            if name_m:
                generic = name_m.group(1).strip()
                brand = name_m.group(2)
                if brand:
                    # Use brand name, strip dosage from it
                    drug_name = re.split(r'\s+\d', brand)[0].strip()
                else:
                    drug_name = generic

            dose_m = re.search(r'(\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|units?|i\.u\.))', entry, re.IGNORECASE)
            dose = dose_m.group(1) if dose_m else '-'

            route_m = re.search(r'\b(by mouth|oral|intravenous|topical|sublingual|IV|IM|SC)\b', entry, re.IGNORECASE)
            route = route_m.group(1).title() if route_m else 'By Mouth'

            freq_m = re.search(
                r'(once daily|twice daily|three times daily|every\s+\d+\s+hours?|as needed|daily)',
                entry, re.IGNORECASE
            )
            frequency = freq_m.group(0).strip().title() if freq_m else 'As Directed'

            dur_m = re.search(r'(\d+)\s*(Day|Week|Month)', entry, re.IGNORECASE)
            duration = f"{dur_m.group(1)} {dur_m.group(2)}(s)" if dur_m else '-'

            purpose_m = re.search(r'for\s+(Pain[^\n,]*)', entry, re.IGNORECASE)
            purpose = purpose_m.group(1).strip() if purpose_m else ''

            instr_m = re.search(r'Special Instructions?:\s*([^\n]+)', entry, re.IGNORECASE)
            instruction = instr_m.group(1).strip() if instr_m else ''

            if drug_name:
                parsed_meds.append({
                    'name': drug_name.title(),
                    'dose': dose,
                    'route': route,
                    'frequency': frequency,
                    'duration': duration,
                    'purpose': purpose,
                    'instruction': instruction,
                    'done': False
                })
        if parsed_meds:
            case_data['medications'] = parsed_meds

    # 1. Match Recovery dataset — collect ALL matching guidelines
    matched_guidelines = []
    for _, row in recovery_df.iterrows():
        keyword = str(row.get('Trigger_Keyword', '')).lower().strip()
        if keyword and keyword in text_lower:
            guideline = str(row.get('Output_Guideline', '')).strip()
            if guideline:
                matched_guidelines.append(guideline)
            # Use first Diagnosis-type match as the diagnosis
            if case_data["diagnosis"] == "Not detected" and str(row.get('Entity_Type', '')) == 'Diagnosis':
                case_data["diagnosis"] = str(row.get('Trigger_Keyword', 'Detected Condition'))

    # Split guidelines into recovery_progress and physiotherapy
    if matched_guidelines:
        recovery_items = []
        physio_items = []
        med_items = []
        physio_keywords = ('exercise', 'therapy', 'physiotherapy', 'weight bearing', 'mobiliz', 'gait', 'range of motion', 'drill', 'movement')
        med_keywords = ('antibiotic', 'nsaid', 'opioid', 'steroid', 'supplement', 'vitamin', 'calcium', 'medication', 'analgesic', 'pump inhibitor', 'muscle relaxant')
        for g in matched_guidelines:
            g_lower = g.lower()
            if any(k in g_lower for k in physio_keywords):
                physio_items.append(g)
            elif any(k in g_lower for k in med_keywords):
                med_items.append(g)
            else:
                recovery_items.append(g)
        case_data["recovery_progress"] = group_recovery_tips(recovery_items)
        case_data["physiotherapy"] = physio_items
        # Only use dataset medications as fallback if PDF extraction found none
        if not case_data["medications"]:
            case_data["medications"] = med_items

    # 2. Match Diet dataset — aggregate all matching rows' Recommended Foods
    if diet_df is not None:
        diet_keywords = ['bone fracture recovery', 'post fracture rehabilitation', 'post orthopedic surgery',
                         'knee surgery recovery', 'joint replacement rehabilitation', 'orthopedic trauma recovery']
        diet_foods = set()
        for _, row in diet_df.iterrows():
            condition = str(row.get('Condition', '')).lower().strip()
            if condition in text_lower or condition in diet_keywords:
                foods = str(row.get('Recommended Foods', ''))
                for f in foods.split(','):
                    f = f.strip()
                    if f:
                        diet_foods.add(f)
        if diet_foods:
            case_data["diet"] = sorted(diet_foods)

    # 3. Match Terms dataset for simplified report
    if terms_df is not None:
        for _, row in terms_df.iterrows():
            keyword = str(row.get('Keyword for PDF Detection', '')).lower().strip()
            if keyword and keyword in text_lower:
                case_data["simplified_report"] = str(row.get('Patient Friendly Meaning', case_data["simplified_report"]))
                advice = str(row.get('System Action / Recovery Advice', ''))
                if advice:
                    existing = case_data["recovery_progress"]
                    if isinstance(existing, dict):
                        key = advice.lower()[:60]
                        if key not in [g.lower()[:60] for g in existing.get('general', [])]:
                            existing.setdefault('general', []).append(advice)
                    else:
                        existing.append(advice)
                if case_data["diagnosis"] == "Not detected":
                    case_data["diagnosis"] = str(row.get('Medical Term', 'Detected Condition'))
                break

    # 4. Fallback: extract diagnosis directly from PDF text
    if case_data["diagnosis"] == "Not detected":
        m = re.search(r'(?:ty|ity)\s+([A-Z][a-zA-Z ,]+(?:fracture|injury|replacement|surgery|repair|disorder|syndrome|disease)[a-zA-Z ,]*)', text, re.IGNORECASE)
        if m:
            case_data["diagnosis"] = m.group(1).strip()

    # 5. Extract discharge advice from PDF as physiotherapy if still empty
    if not case_data["physiotherapy"]:
        m = re.search(r'Advice at Discharge[:\s]*\n([\s\S]+?)(?:\n\n|Discharge Medication|$)', text, re.IGNORECASE)
        if m:
            items = [i.strip() for i in re.split(r'[,\n]', m.group(1)) if i.strip() and len(i.strip()) > 3]
            case_data["physiotherapy"] = items

    return case_data


def safe_json_parse(value, default=None):
    try:
        if value and isinstance(value, str) and value.strip():
            return json.loads(value)
        return default if default is not None else []
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def parse_recovery(value):
    """Parse recovery_progress — handles both old list format and new grouped dict."""
    data = safe_json_parse(value, {})
    if isinstance(data, list):
        # Old flat list — group it on the fly
        return group_recovery_tips(data)
    return data


# --------------------------------
# Content Generation / Formatting
# --------------------------------
def generate_medication_schedule(med_list):
    """Parses extracted medication strings or dicts into a structured schedule with timings."""
    schedule = []
    for med in med_list:
        if not med: continue
        
        # If it's already a dict (e.g. from PDF extraction), we still want to enrich it with timing/hours
        if isinstance(med, dict):
            name = med.get('name', 'Prescribed Medication')
            dosage = med.get('dose', '-')
            frequency = med.get('frequency', '-')
            instruction = med.get('instruction', 'Monitor side effects, adjust activity.')
            original = f"{name} {dosage}"
            # Extract relation/timing from original or instruction if possible
            relation = "After food"
            if 'before food' in instruction.lower() or 'empty stomach' in instruction.lower():
                relation = "Before food"
        else:
            if not med.strip(): continue
            name = med
            dosage = "-"
            frequency = "-"
            relation = "After food"
            instruction = "Monitor side effects, adjust activity."
            original = med
        
        timing = []
        hours_list = []
        
        # Remove repetitive boilerplate from name
        name = re.sub(r'Patients with .*? receiving\s+', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+should adhere strictly.*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'(prevent infection|to reduce pain|to prevent bone loss).*?', '', name, flags=re.IGNORECASE)
        
        m_rel = re.search(r'(before food|after food|empty stomach|post meal|pre meal|a/f|b/f)', name, re.IGNORECASE)
        if m_rel:
            rel_str = m_rel.group(1).lower()
            if rel_str in ['before food', 'empty stomach', 'pre meal', 'b/f']:
                relation = "Before food"
            else:
                relation = "After food"
            name = name.replace(m_rel.group(0), '')
        
        # Parse frequency/timing
        m_freq = re.search(r'\b(\d+[-/]\d+[-/]\d+(?:[-/]\d+)?)\b', name if not isinstance(med, dict) else frequency)
        if m_freq:
            f_str = m_freq.group(1)
            parts = f_str.replace('/', '-').split('-')
            times = []
            if len(parts) >= 3:
                if parts[0] != '0': 
                    times.append("Morning (8:00 AM)")
                    hours_list.append(8)
                if parts[1] != '0': 
                    times.append("Afternoon (2:00 PM)")
                    hours_list.append(14)
                if parts[2] != '0': 
                    times.append("Night (8:00 PM)")
                    hours_list.append(20)
            if len(parts) == 4 and parts[3] != '0':
                times.append("Late Night (10:00 PM)")
                hours_list.append(22)
            timing = times
            
            if not frequency or frequency == '-':
                if len(times) == 1: frequency = "Once daily"
                elif len(times) == 2: frequency = "Twice daily"
                elif len(times) == 3: frequency = "Three times daily"
                elif len(times) == 4: frequency = "Four times daily"
            if not isinstance(med, dict):
                name = name.replace(m_freq.group(0), '')
        else:
            search_str = (name if not isinstance(med, dict) else frequency).lower()
            if re.search(r'\b(bd|bid|twice daily)\b', search_str):
                frequency = "Twice daily"
                timing = ["Morning (8:00 AM)", "Night (8:00 PM)"]
                hours_list = [8, 20]
            elif re.search(r'\b(tds|tid|three times daily)\b', search_str):
                frequency = "Three times daily"
                timing = ["Morning (8:00 AM)", "Afternoon (2:00 PM)", "Night (8:00 PM)"]
                hours_list = [8, 14, 20]
            elif re.search(r'\b(od|once daily|daily)\b', search_str):
                frequency = "Once daily"
                timing = ["Morning (8:00 AM)"]
                hours_list = [8]
            elif re.search(r'\b(sos|as needed)\b', search_str):
                frequency = "As needed (SOS)"
                timing = ["Only when required"]
                hours_list = []
                
        if not isinstance(med, dict):
            m_dos = re.search(r'\b(\d+(?:\.\d+)?\s*(mg|ml|g|mcg|i\.u\.|units))\b', name, re.IGNORECASE)
            if m_dos:
                dosage = m_dos.group(1)
            
            name = re.sub(r'(?i)\b(tab\.?|cap\.?|inj\.?|syr\.?|drop\.?|cream|ointment|gel)\b', '', name).strip()
            name = re.sub(r'^[0-9\.\- ]+', '', name).strip()
            name = name.split(',')[0].strip() # Cleanup long trailing sentences

        if not timing:
            timing = ["Morning (8:00 AM)"]
            if not frequency or frequency == '-': 
                frequency = "Once daily"
            hours_list = [8]

        schedule.append({
            "name": name.title() if name else "Prescribed Medication",
            "dosage": dosage,
            "frequency": frequency,
            "timing": ", ".join(timing),
            "relation": relation,
            "hours": hours_list,
            "instruction": instruction,
            "original": original
        })
    return schedule


def generate_diet_chart(foods_list):
    """Distributes an aggregated generic diet list into structured daily Indian meals."""
    meals = {
        'Breakfast': [],
        'Mid-Morning': [],
        'Lunch': [],
        'Evening Snack': [],
        'Dinner': []
    }
    avoid_foods = ["Junk food", "Excess sugar", "Deep-fried items", "Carbonated drinks", "Excessive salt"]
    hydration = "Drink at least 8-10 glasses of water daily. Coconut water and fresh juices are recommended."
    
    for f in foods_list:
        if not f.strip(): continue
        f_clean = f.strip().lower()
        if any(w in f_clean for w in ['egg', 'milk', 'oat', 'bread', 'poha', 'upma', 'idli', 'dosa']):
            meals['Breakfast'].append(f.title())
        elif any(w in f_clean for w in ['fruit', 'juice', 'smoothie', 'almond', 'nut', 'seed', 'walnut', 'coconut', 'yoghurt', 'curd']):
            meals['Mid-Morning'].append(f.title())
        elif any(w in f_clean for w in ['rice', 'dal', 'chapati', 'roti', 'chicken', 'fish', 'curry', 'vegetable', 'sabzi', 'lentil', 'rajma', 'chole']):
            meals['Lunch'].append(f.title())
        elif any(w in f_clean for w in ['soup', 'salad', 'paneer', 'khichdi', 'dalia', 'light']):
            meals['Dinner'].append(f.title())
        else:
            meals['Evening Snack'].append(f.title())
                
    # Fill empty meals with good generic bone healing Indian foods
    if not meals['Breakfast']: meals['Breakfast'] = ['Oatmeal with Milk / Poha', 'Boiled Eggs / Paneer Bhurji']
    if not meals['Mid-Morning']: meals['Mid-Morning'] = ['Mixed nuts (Almonds, Walnuts)', 'Fresh seasonal fruit']
    if not meals['Lunch']: meals['Lunch'] = ['Dal / Lentils', 'Green leafy vegetables (Spinach/Methi)', 'Chapati / Rice']
    if not meals['Evening Snack']: meals['Evening Snack'] = ['Roasted Makhana / Chana', 'Green Tea / Milk']
    if not meals['Dinner']: meals['Dinner'] = ['Light vegetable soup', 'Khichdi / Dalia']

    return {
        "meals": meals,
        "avoid": avoid_foods,
        "hydration": hydration
    }

def generate_progress_plan(diagnosis, surgery):
    """Generates a phased progress plan based on patient condition and diagnosis type."""
    if not diagnosis:
        diagnosis = ""

    diagnosis_lower = diagnosis.lower()

    # Category 1: Joint Replacement Surgery (Major Surgical Recovery)
    if any(keyword in diagnosis_lower for keyword in ['knee replacement', 'hip replacement', 'joint replacement']):
        return [
            {
                "timeline": "Surgical Recovery Timeline",
                "category": "Major Joint Surgery",
                "weeks": [
                    {"week": "Week 1-2 (Immediate Post-Op)", "desc": "Hospital recovery and initial healing. Focus on pain management and preventing complications.", "activity": "Bed rest, gentle movements, physical therapy begins.", "warning": "Monitor for infection, blood clots, or excessive swelling."},
                    {"week": "Week 3-6 (Early Recovery)", "desc": "Gradual increase in mobility. Building strength around the joint.", "activity": "Walking with assistance, range-of-motion exercises, home exercises.", "warning": "Avoid putting full weight on operated joint without clearance."},
                    {"week": "Week 7-12 (Strength Building)", "desc": "Significant improvement in function. Focus on muscle strengthening.", "activity": "Increased walking, resistance exercises, return to light activities.", "warning": "Do not rush recovery - follow physical therapy guidance."},
                    {"week": "Month 4-6 (Functional Recovery)", "desc": "Near normal function achieved. Return to most daily activities.", "activity": "Normal walking, light sports, full independence.", "warning": "Continue avoiding high-impact activities for 6-12 months."},
                    {"week": "Month 7+ (Long-term)", "desc": "Full recovery and adaptation to new joint.", "activity": "Return to most activities, regular exercise routine.", "warning": "Annual follow-ups recommended for implant monitoring."}
                ]
            }
        ]

    # Category 2: Fracture Recovery
    elif any(keyword in diagnosis_lower for keyword in ['fracture', 'broken', 'break']):
        return [
            {
                "timeline": "Fracture Healing Timeline",
                "category": "Bone Fracture",
                "weeks": [
                    {"week": "Week 1-3 (Immobilization)", "desc": "Bone healing begins. Cast/splint immobilization required.", "activity": "Rest, elevation, ice application, gentle finger/toe movements if applicable.", "warning": "Keep weight off injured area, watch for circulation issues."},
                    {"week": "Week 4-6 (Early Healing)", "desc": "Callus formation around fracture site. Reduced swelling.", "activity": "Protected weight-bearing if allowed, begin gentle range-of-motion.", "warning": "Avoid forceful movements that could displace healing bone."},
                    {"week": "Week 7-12 (Bone Consolidation)", "desc": "Bone becomes stronger. Cast removal and rehabilitation begins.", "activity": "Physical therapy, gradual strength building, return to light activities.", "warning": "Do not return to full activity until cleared by doctor."},
                    {"week": "Month 4-6 (Functional Recovery)", "desc": "Bone fully healed. Return to normal activities.", "activity": "Full weight-bearing, sports activities, normal daily routine.", "warning": "Some fractures may take longer - follow X-ray guidance."}
                ]
            }
        ]

    # Category 3: Ligament/Tendon Surgery (ACL, etc.)
    elif any(keyword in diagnosis_lower for keyword in ['acl', 'ligament', 'tendon', 'reconstruction']):
        return [
            {
                "timeline": "Ligament Recovery Timeline",
                "category": "Ligament/Tendon Surgery",
                "weeks": [
                    {"week": "Week 1-2 (Protection Phase)", "desc": "Protect healing ligament with brace/crutches. Control swelling.", "activity": "Toe-touch weight-bearing, ice, compression, elevation.", "warning": "Avoid any stress on repaired ligament."},
                    {"week": "Week 3-6 (Early Rehab)", "desc": "Begin controlled range-of-motion. Build quad strength.", "activity": "Physical therapy, stationary bike, gentle strengthening.", "warning": "No running, jumping, or pivoting movements."},
                    {"week": "Week 7-12 (Strength Phase)", "desc": "Focus on strength and proprioception. Sport-specific drills.", "activity": "Agility training, plyometrics, return to controlled sports.", "warning": "Progress slowly to avoid re-injury."},
                    {"week": "Month 4-6 (Return to Sport)", "desc": "Full return to activities. Maintenance of strength.", "activity": "Full sports participation, ongoing conditioning.", "warning": "Some may need 6-9 months for full recovery."}
                ]
            }
        ]

    # Category 4: Spine Conditions (Disc Herniation, Fusion)
    elif any(keyword in diagnosis_lower for keyword in ['spine', 'disc', 'fusion', 'lumbar', 'spinal']):
        return [
            {
                "timeline": "Spine Recovery Timeline",
                "category": "Spinal Condition",
                "weeks": [
                    {"week": "Week 1-4 (Acute Phase)", "desc": "Pain control and inflammation reduction. Activity modification.", "activity": "Rest, gentle walking, core stability exercises.", "warning": "Avoid bending, lifting, or twisting spine."},
                    {"week": "Week 5-8 (Subacute Phase)", "desc": "Gradual return to activities. Build core strength.", "activity": "Physical therapy, posture training, light strengthening.", "warning": "Listen to body - stop if pain increases."},
                    {"week": "Week 9-12 (Recovery Phase)", "desc": "Functional improvement. Return to work/activities.", "activity": "Full exercise program, ergonomic modifications.", "warning": "Maintain proper body mechanics long-term."},
                    {"week": "Month 4+ (Maintenance)", "desc": "Prevent recurrence. Ongoing conditioning.", "activity": "Regular exercise, posture awareness, healthy lifestyle.", "warning": "Spinal conditions may require lifelong management."}
                ]
            }
        ]

    # Category 5: Degenerative Conditions (Arthritis, Osteoporosis)
    elif any(keyword in diagnosis_lower for keyword in ['arthritis', 'osteoporosis', 'osteoarthritis', 'degenerative']):
        return [
            {
                "timeline": "Conservative Management Timeline",
                "category": "Degenerative Condition",
                "weeks": [
                    {"week": "Month 1-3 (Assessment & Education)", "desc": "Understanding condition, lifestyle modifications, pain management.", "activity": "Low-impact exercises, weight management, joint protection.", "warning": "Avoid high-impact activities that stress joints."},
                    {"week": "Month 4-6 (Strength Building)", "desc": "Build muscle strength around affected areas. Improve function.", "activity": "Physical therapy, swimming, cycling, resistance training.", "warning": "Progress gradually to avoid flare-ups."},
                    {"week": "Month 7-12 (Maintenance)", "desc": "Maintain function and prevent progression.", "activity": "Regular exercise routine, healthy diet, assistive devices if needed.", "warning": "Long-term condition management required."}
                ]
            },
            {
                "timeline": "Pain Management Timeline",
                "category": "Symptom Control",
                "weeks": [
                    {"week": "Week 1-4 (Acute Pain Control)", "desc": "Identify pain triggers, medication management, activity pacing.", "activity": "Rest periods, heat/cold therapy, gentle stretching.", "warning": "Balance activity with rest to avoid worsening symptoms."},
                    {"week": "Week 5-12 (Long-term Management)", "desc": "Develop coping strategies, maintain function despite pain.", "activity": "Cognitive behavioral techniques, regular exercise, stress management.", "warning": "Chronic pain may persist - focus on quality of life."}
                ]
            }
        ]

    # Default: General Recovery Timeline
    else:
        return [
            {
                "timeline": "General Recovery Timeline",
                "category": "Standard Recovery",
                "weeks": [
                    {"week": "Week 1 (Immediate Recovery)", "desc": "Focus on rest, managing pain, and reducing swelling. Stick strictly to prescribed medications.", "activity": "Bed rest with mild movement based on doctor's advice.", "warning": "Watch for fever, severe swelling, or numbness."},
                    {"week": "Week 2-3 (Early Mobility)", "desc": "Gradual reduction in pain. Healing begins taking solid shape.", "activity": "Start prescribed gentle physiotherapy. Limit strenuous activities.", "warning": "Avoid bearing full weight unless cleared by the doctor."},
                    {"week": "Week 4-6 (Strengthening)", "desc": "Significant improvement in mobility and decrease in stiffness.", "activity": "Increase weight bearing and range-of-motion exercises as guided.", "warning": "Do not rush or push through sharp pain."},
                    {"week": "Week 7+ (Normalization)", "desc": "Bones and tissues are largely stable. Returning to independence.", "activity": "Gradual return to light daily tasks and normal routines.", "warning": "Continue avoiding high-impact sports until cleared."}
                ]
            }
        ]

# -------------------------
# Home Page
# -------------------------
@app.route("/")
def home():
    return render_template("home.html")


# -------------------------
# Patient Login
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM patients WHERE email=? AND password=?
            """, (email, password))
            user = cursor.fetchone()
        finally:
            conn.close()

        if user:
            session['patient_id'] = user[0]
            session['patient_name'] = user[1]
            session['patient_email'] = user[5]
            conn2 = sqlite3.connect("medcare.db")
            try:
                c2 = conn2.cursor()
                c2.execute("SELECT COUNT(*) FROM patient_cases WHERE patient_id = ?", (user[0],))
                count = c2.fetchone()[0]
            finally:
                conn2.close()
            if count > 0:
                return redirect(url_for("patient_dashboard"))
            return redirect(url_for("upload"))
        else:
            from markupsafe import escape
            return str(escape("Invalid Email or Password"))

    return render_template("login.html")


# -------------------------
# Patient Register
# -------------------------
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form["name"]
        age = request.form["age"]
        gender = request.form["gender"]
        phone = request.form["phone"]
        email = request.form["email"]
        password = request.form["password"]

        try:
            conn = sqlite3.connect("medcare.db")
            try:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO patients (name, age, gender, phone, email, password)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (name, age, gender, phone, email, password))
                conn.commit()
            finally:
                if cursor:
                    cursor.close()
                conn.close()
        except sqlite3.IntegrityError:
            return "Email already registered!"

        return redirect(url_for("login"))

    return render_template("register.html")


# -------------------------
# Upload Page
# -------------------------
@app.route("/upload", methods=["GET", "POST"])
def upload():

    if request.method == "POST":

        file = request.files["report"]

        if file.filename == "":
            return "No file selected"

        filename = secure_filename(file.filename)
        if not filename:
            return "Invalid filename", 400
        upload_folder = os.path.realpath(app.config["UPLOAD_FOLDER"])
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{ext}"
        filepath = os.path.realpath(os.path.join(upload_folder, filename))
        if not filepath.startswith(upload_folder + os.sep):
            return "Invalid file path", 400
        file.save(filepath)

        text = ""

        with open(os.path.abspath(filepath), "rb") as pdf:
            reader = PyPDF2.PdfReader(pdf)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text

        # Fallback to OCR if PyPDF2 extracted nothing (scanned/image PDF)
        if not text.strip():
            doc = pypdfium2.PdfDocument(filepath)
            for i in range(len(doc)):
                bitmap = doc[i].render(scale=3)
                img = bitmap.to_pil()
                text += pytesseract.image_to_string(img)
            doc.close()

        case = detect_case(text)

        # Store case data in database
        if 'patient_id' in session:
            with sqlite3.connect("medcare.db") as conn:
                cursor = conn.cursor()

                cursor.execute("""
                INSERT INTO patient_cases 
                (patient_id, patient_name, age, gender, diagnosis, surgery, hospital, doctor, 
                 simplified_report, medications, physiotherapy, diet, recovery_progress, followup, file_path, upload_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session['patient_id'],
                    case.get('patient_name', ''),
                    case.get('age', ''),
                    case.get('gender', ''),
                    case.get('diagnosis', ''),
                    case.get('surgery', ''),
                    case.get('hospital', ''),
                    case.get('doctor', ''),
                    case.get('simplified_report', ''),
                    json.dumps(case.get('medications', [])),
                    json.dumps(case.get('physiotherapy', [])),
                    json.dumps(case.get('diet', [])),
                    json.dumps(case.get('recovery_progress', [])),
                    case.get('followup', ''),
                    filepath,
                    datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                ))

                case_id = cursor.lastrowid
                conn.commit()
                session['case_id'] = case_id

            return redirect(url_for('patient_dashboard'))

    return render_template("upload.html")


# -------------------------
# Patient View Case
# -------------------------
@app.route("/view_case/<int:case_id>")
def view_case(case_id):
    if 'patient_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM patient_cases WHERE id = ? AND patient_id = ?", (case_id, session['patient_id']))
        row = cursor.fetchone()

        if not row:
            return "Case not found", 404

        patient = {
            'id': row[0],
            'patient_name': row[2],
            'age': row[3],
            'gender': row[4],
            'diagnosis': row[5],
            'surgery': row[6],
            'hospital': row[7],
            'doctor': row[8],
            'doctor_id': row[9],
            'simplified_report': row[10],
            'medications': safe_json_parse(row[11]),
            'physiotherapy': safe_json_parse(row[12]),
            'diet': safe_json_parse(row[13]),
            'recovery_progress': parse_recovery(row[14]),
            'followup': row[15],
            'file_path': row[16],
            'upload_date': row[17],
            'doctor_recommendations': row[18] if len(row) > 18 else ''
        }

        patient['med_schedule'] = generate_medication_schedule(patient['medications'])
        patient['diet_chart'] = generate_diet_chart(patient['diet'])
        patient['progress_plan'] = generate_progress_plan(patient['diagnosis'], patient['surgery'])

        cursor.execute("""
        SELECT sender_type, message, timestamp FROM messages WHERE case_id = ? ORDER BY timestamp ASC
        """, (case_id,))
        messages = [{'sender_type': r[0], 'message': r[1], 'timestamp': r[2]} for r in cursor.fetchall()]
    finally:
        conn.close()

    return render_template("patient_detail.html", patient=patient, case_id=case_id, messages=messages, is_doctor=False)


# -------------------------
# Patient Dashboard
# -------------------------
@app.route("/patient_dashboard")
def patient_dashboard():
    if 'patient_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, patient_name, diagnosis, surgery, upload_date, doctor
        FROM patient_cases WHERE patient_id = ? ORDER BY upload_date DESC
        """, (session['patient_id'],))
        cases = [{'case_id': r[0], 'patient_name': r[1], 'diagnosis': r[2], 'surgery': r[3], 'upload_date': r[4], 'doctor': r[5]} for r in cursor.fetchall()]
    finally:
        conn.close()
    return render_template("dashboard.html", name=session['patient_name'], cases=cases)


# -------------------------
# Logout
# -------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))


# -------------------------
# Results Page (kept for backward compatibility)
# -------------------------
@app.route("/results")
def results():
    return render_template("results.html", data={})


# -------------------------
# Simplified Report Page
# -------------------------
@app.route("/simplified_report/<int:case_id>")
def simplified_report(case_id):
    if 'patient_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM patient_cases WHERE id = ? AND patient_id = ?", (case_id, session['patient_id']))
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        return "Case not found", 404

    patient = {
        'id': row[0],
        'patient_name': row[2],
        'age': row[3],
        'gender': row[4],
        'diagnosis': row[5],
        'surgery': row[6],
        'hospital': row[7],
        'doctor': row[8],
        'simplified_report': row[10],
        'medications': safe_json_parse(row[11]),
        'physiotherapy': safe_json_parse(row[12]),
        'diet': safe_json_parse(row[13]),
        'recovery_progress': parse_recovery(row[14]),
        'followup': row[15],
        'upload_date': row[17],
    }
    
    patient['med_schedule'] = generate_medication_schedule(patient['medications'])
    patient['diet_chart'] = generate_diet_chart(patient['diet'])
    patient['progress_plan'] = generate_progress_plan(patient['diagnosis'], patient['surgery'])

    return render_template("simplified_report.html", patient=patient, case_id=case_id)


# -------------------------
# Doctor Register
# -------------------------
@app.route("/doctor_register", methods=["GET", "POST"])
def doctor_register():

    if request.method == "POST":

        name = request.form["name"]
        doctor_id = request.form["doctor_id"]
        password = request.form["password"]
        phone = request.form["phone"]
        email = request.form["email"]
        qualification = request.form["qualification"]
        hospital = request.form["hospital"]
        experience = request.form["experience"]

        try:
            with sqlite3.connect("medcare.db") as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO doctors (name, doctor_id, password, phone, email, qualification, hospital, experience)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, doctor_id, password, phone, email, qualification, hospital, experience))
                conn.commit()
        except sqlite3.IntegrityError as e:
            if "doctor_id" in str(e).lower():
                return "Doctor ID already exists! Please use a different Doctor ID."
            return "Registration failed due to a conflict."
        except Exception:
            return "An error occurred during registration."

        return redirect(url_for("doctor_login"))

    return render_template("doctor_register.html")


# -------------------------
# Doctor Login
# -------------------------
@app.route("/doctor_login", methods=["GET", "POST"])
def doctor_login():

    if request.method == "POST":

        doctor_id = request.form["doctor_id"]
        password = request.form["password"]

        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM doctors WHERE doctor_id=? AND password=?
            """, (doctor_id, password))
            doctor = cursor.fetchone()
        finally:
            conn.close()

        if doctor:
            # Store doctor info in session
            session['doctor_id'] = doctor[0]
            session['doctor_name'] = doctor[1]
            session['doctor_idnum'] = doctor[2]
            return redirect(url_for("doctor_dashboard"))
        else:
            from markupsafe import escape
            return str(escape("Invalid Doctor ID or Password"))

    return render_template("doctor_login.html")


# -------------------------
# Doctor Dashboard
# -------------------------
@app.route("/doctor_dashboard")
def doctor_dashboard():
    
    if 'doctor_id' not in session:
        return redirect(url_for('doctor_login'))
    
    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT DISTINCT id, patient_name, age, gender, diagnosis, upload_date
        FROM patient_cases
        WHERE doctor_id = ?
        ORDER BY upload_date DESC
        """, (session['doctor_id'],))
        patients = [{'case_id': r[0], 'patient_name': r[1], 'age': r[2], 'gender': r[3], 'diagnosis': r[4], 'upload_date': r[5]} for r in cursor.fetchall()]
    finally:
        conn.close()
    return render_template("doctor_dashboard.html", patients=patients)


# -------------------------
# Patient Case Detail Page
# -------------------------
@app.route("/patient_detail/<int:case_id>")
def patient_detail(case_id):
    try:
        print(f"Accessing case_id: {case_id}")
        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM patient_cases WHERE id = ?", (case_id,))
            row = cursor.fetchone()
            print(f"Row fetched: {row is not None}")

            if not row:
                return "Patient case not found", 404

            print("Parsing patient data")
            patient = {
                'id': row[0],
                'patient_name': row[2],
                'age': row[3],
                'gender': row[4],
                'diagnosis': row[5],
                'surgery': row[6],
                'hospital': row[7],
                'doctor': row[8],
                'doctor_id': row[9],
                'simplified_report': row[10],
                'medications': safe_json_parse(row[11]),
                'physiotherapy': safe_json_parse(row[12]),
                'diet': safe_json_parse(row[13]),
                'recovery_progress': parse_recovery(row[14]),
                'followup': row[15],
                'file_path': row[16],
                'upload_date': row[17],
                'doctor_recommendations': row[18] if len(row) > 18 else ''
            }
            
            print("Generating med_schedule")
            patient['med_schedule'] = generate_medication_schedule(patient['medications'])
            print("Generating diet_chart")
            patient['diet_chart'] = generate_diet_chart(patient['diet'])
            print("Generating progress_plan")
            patient['progress_plan'] = generate_progress_plan(patient['diagnosis'], patient['surgery'])

            print("Fetching messages")
            cursor.execute("""
            SELECT sender_type, message, timestamp FROM messages WHERE case_id = ? ORDER BY timestamp ASC
            """, (case_id,))
            messages = [{'sender_type': r[0], 'message': r[1], 'timestamp': r[2]} for r in cursor.fetchall()]
        finally:
            conn.close()

        is_doctor = 'doctor_id' in session
        print("Rendering template")
        return render_template("patient_detail.html", patient=patient, case_id=case_id, messages=messages, is_doctor=is_doctor)
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Internal Server Error: {str(e)}", 500


# -------------------------
# Download Patient PDF
# -------------------------
@app.route("/download_pdf/<int:case_id>")
def download_pdf(case_id):
    
    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT file_path, patient_name FROM patient_cases WHERE id = ?
        """, (case_id,))
        row = cursor.fetchone()
    finally:
        conn.close()
    
    if not row:
        return "File not found", 404
    
    file_path, patient_name = row

    upload_folder = os.path.realpath(app.config["UPLOAD_FOLDER"])
    safe_path = os.path.realpath(file_path)
    if not safe_path.startswith(upload_folder + os.sep):
        return "Access denied", 403

    if not os.path.exists(safe_path):
        return "File not found on server", 404

    return send_file(safe_path, as_attachment=True, download_name=f"{patient_name}_discharge_summary.pdf")


# -------------------------
# Get All Doctors (for patient to see - standalone)
# -------------------------
@app.route("/doctors_list")
def doctors_list():
    is_patient = 'patient_id' in session
    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, name, qualification, experience, hospital, phone, is_available FROM doctors ORDER BY name
        """)
        doctors = [{'id': r[0], 'name': r[1], 'qualification': r[2], 'experience': r[3], 'hospital': r[4], 'phone': r[5], 'is_available': r[6]} for r in cursor.fetchall()]
    finally:
        conn.close()
    return render_template("doctors_list.html", doctors=doctors, case_id=None, is_patient=is_patient)


# -------------------------
# Get All Doctors for Patient with Case (with chat feature)
# -------------------------
@app.route("/patient_doctors/<int:case_id>")
def patient_doctors_list(case_id):
    
    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, name, qualification, experience, hospital, phone, is_available FROM doctors ORDER BY name
        """)
        doctors = [{'id': r[0], 'name': r[1], 'qualification': r[2], 'experience': r[3], 'hospital': r[4], 'phone': r[5], 'is_available': r[6]} for r in cursor.fetchall()]
    finally:
        conn.close()
    return render_template("doctors_list.html", doctors=doctors, case_id=case_id)


# -------------------------
# Get All Doctors as JSON (for AJAX calls)
# -------------------------
@app.route("/api/doctors")
def get_doctors_json():
    
    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, name, qualification, experience, hospital, phone, is_available FROM doctors ORDER BY name
        """)
        doctors = [{'id': r[0], 'name': r[1], 'qualification': r[2], 'experience': r[3], 'hospital': r[4], 'phone': r[5], 'is_available': r[6]} for r in cursor.fetchall()]
    finally:
        conn.close()
    return {"doctors": doctors}


# -------------------------
# Toggle Doctor Availability
# -------------------------
@app.route("/toggle_availability", methods=["POST"])
def toggle_availability():
    
    if 'doctor_id' not in session:
        return {"status": "error", "message": "Not logged in"}, 401
    
    try:
        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT is_available FROM doctors WHERE id = ?
            """, (session['doctor_id'],))
            result = cursor.fetchone()
            current_status = result[0] if result else 1
            new_status = 1 - current_status
            cursor.execute("""
            UPDATE doctors SET is_available = ? WHERE id = ?
            """, (new_status, session['doctor_id']))
            conn.commit()
        finally:
            conn.close()
        
        return {
            "status": "success",
            "message": "Availability updated",
            "is_available": new_status
        }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# -------------------------
# Assign Doctor to Patient Case (Chat Page)
# -------------------------
@app.route("/assign_doctor/<int:case_id>/<int:doctor_id>", methods=["GET", "POST"])
def assign_doctor(case_id, doctor_id):
    
    try:
        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT name, id FROM doctors WHERE id = ?
            """, (doctor_id,))
            doctor = cursor.fetchone()
            if not doctor:
                return "Doctor not found", 404
            cursor.execute("""
            UPDATE patient_cases
            SET doctor_id = ?, doctor = ?
            WHERE id = ?
            """, (doctor_id, doctor[0], case_id))
            conn.commit()
        finally:
            conn.close()
        
        # Redirect to chat page
        return redirect(url_for('chat_page', case_id=case_id, doctor_id=doctor_id))
    
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# -------------------------
# Request Consultation with Doctor (for patients without specific case)
# -------------------------
@app.route("/request_consultation/<int:doctor_id>")
def request_consultation(doctor_id):
    
    if 'patient_id' not in session:
        return redirect(url_for('login'))
    
    patient_id = session['patient_id']
    
    try:
        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            
            # Get doctor info
            cursor.execute("""
            SELECT name FROM doctors WHERE id = ?
            """, (doctor_id,))
            doctor = cursor.fetchone()
            if not doctor:
                return "Doctor not found", 404
            
            doctor_name = doctor[0]
            
            # Get patient's cases
            cursor.execute("""
            SELECT id FROM patient_cases WHERE patient_id = ? ORDER BY upload_date DESC
            """, (patient_id,))
            cases = cursor.fetchall()
            
            if not cases:
                # No cases yet, redirect to upload
                return redirect(url_for('upload'))
            
            # Assign doctor to all patient's cases
            for case in cases:
                cursor.execute("""
                UPDATE patient_cases
                SET doctor_id = ?, doctor = ?
                WHERE id = ?
                """, (doctor_id, doctor_name, case[0]))
            
            conn.commit()
            
        finally:
            conn.close()
        
        # Redirect to patient dashboard
        return redirect(url_for('patient_dashboard'))
    
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# -------------------------
# Patient-Doctor Chat Page
# -------------------------
@app.route("/chat/<int:case_id>/<int:doctor_id>", methods=["GET"])
def chat_page(case_id, doctor_id):

    conn = sqlite3.connect("medcare.db")
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT patient_name, age, gender, diagnosis, surgery, hospital
        FROM patient_cases WHERE id = ?
        """, (case_id,))
        case_info = cursor.fetchone()

        cursor.execute("""
        SELECT name, phone, email FROM doctors WHERE id = ?
        """, (doctor_id,))
        doctor_info = cursor.fetchone()

        cursor.execute("""
        SELECT sender_type, message, timestamp FROM messages
        WHERE case_id = ? ORDER BY timestamp ASC
        """, (case_id,))
        messages = [{'sender_type': r[0], 'message': r[1], 'timestamp': r[2]} for r in cursor.fetchall()]
    finally:
        conn.close()

    if not case_info or not doctor_info:
        return "Case or Doctor not found", 404

    is_doctor = 'doctor_id' in session
    back_url = url_for('doctor_dashboard') if is_doctor else url_for('patient_dashboard')

    # noqa: CWE-20,79,80 - Flask auto-escapes all template variables
    return render_template("chat.html",
        case_id=case_id,
        doctor_id=doctor_id,
        patient_name=case_info[0],
        patient_age=case_info[1],
        patient_gender=case_info[2],
        diagnosis=case_info[3],
        surgery=case_info[4],
        hospital=case_info[5],
        doctor_name=doctor_info[0],
        doctor_phone=doctor_info[1],
        doctor_email=doctor_info[2],
        messages=messages,
        is_doctor=is_doctor,
        back_url=back_url
    )


# -------------------------
# Send Chat Message
# -------------------------
@app.route("/send_message/<int:case_id>/<int:doctor_id>", methods=["POST"])
def send_message(case_id, doctor_id):
    
    try:
        data = request.get_json()
        message = data.get('message', '')
        sender_type = data.get('sender_type', 'patient')
        if sender_type not in ('patient', 'doctor'):
            return {"status": "error", "message": "Invalid sender type"}, 400

        if not message:
            return {"status": "error", "message": "Message is empty"}, 400
        
        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT patient_id FROM patient_cases WHERE id = ?
            """, (case_id,))
            result = cursor.fetchone()
            if not result:
                return {"status": "error", "message": "Case not found"}, 404
            patient_id = result[0]
            cursor.execute("""
            INSERT INTO messages (case_id, patient_id, doctor_id, sender_type, message, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (case_id, patient_id, doctor_id if doctor_id != 0 else None, sender_type, message, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
        finally:
            conn.close()
        
        return {"status": "success", "message": "Message sent"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# -------------------------
# Get Chat Messages (for AJAX)
# -------------------------
@app.route("/api/messages/<int:case_id>", methods=["GET"])
def get_messages(case_id):
    
    try:
        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT sender_type, message, timestamp FROM messages WHERE case_id = ? ORDER BY timestamp ASC
            """, (case_id,))
            messages = [{'sender_type': r[0], 'message': r[1], 'timestamp': r[2]} for r in cursor.fetchall()]
        finally:
            conn.close()
        
        return {"messages": messages}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# -------------------------
# Save Doctor Recommendations
# -------------------------
@app.route("/save_recommendations/<int:case_id>", methods=["POST"])
def save_recommendations(case_id):
    try:
        data = request.get_json()
        conn = sqlite3.connect("medcare.db")
        try:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE patient_cases SET
                recovery_progress = ?,
                physiotherapy = ?,
                diet = ?,
                medications = ?,
                doctor_recommendations = ?
            WHERE id = ?
            """, (
                json.dumps(data.get('recovery_progress', [])),
                json.dumps(data.get('physiotherapy', [])),
                json.dumps(data.get('diet', [])),
                json.dumps(data.get('medications', [])),
                data.get('doctor_recommendations', ''),
                case_id
            ))
            conn.commit()
        finally:
            conn.close()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# -------------------------
# Run App
# -------------------------
if __name__ == "__main__":
    print("Starting server at http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)


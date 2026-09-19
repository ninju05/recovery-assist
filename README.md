Recovery Assist: A Digital Health Tool

B.Tech Computer Science & Engineering – Mini Project

Recovery Assist is a web-based digital health tool designed to support patients after hospital discharge. The system processes discharge-summary documents and provides simplified medical information, recovery guidance, diet recommendations, medication schedules, physiotherapy guidance, follow-up information, and doctor consultation features through a centralized web application.

🌐 Live Demo

https://recovery-assist.onrender.com

The application is deployed for demonstration purposes.

🎯 Project Objective

The main objective of Recovery Assist is to provide patients with an easy-to-understand digital platform for managing important post-discharge information.

The system aims to:
Simplify information from discharge-summary documents.
Provide personalized recovery and care guidance.
Display medication schedules and reminders.
Provide diet and physiotherapy recommendations.
Help patients keep track of follow-up information.
Connect patients with registered doctors for consultation.
Provide a centralized platform for post-discharge support.

✨ Key Features
Discharge Summary Processing – Upload a discharge-summary PDF and extract relevant information.
Simplified Report – Presents important medical information in an easier-to-understand format.
Recovery Guidance – Provides recovery tips based on the identified case.
Diet Recommendations – Displays suitable dietary recommendations.
Medication Schedule – Helps patients view medication timing and reminders.
Physiotherapy Guidance – Provides recommended physiotherapy and exercise information.
Follow-up Information – Displays follow-up and consultation details.
Doctor Module – Doctors can register, log in, view patient case information, and respond to patients.
Patient–Doctor Chat – Enables communication between patients and registered doctors.
Patient Dashboard – Provides access to the patient's post-discharge information from one place.

🛠️ Technologies Used
Frontend
HTML
CSS
JavaScript
Jinja2 Templates
Backend
Python
Flask
Data Processing
Pandas
PyPDF2
pypdfium2
Tesseract OCR
Regular Expression-based information extraction
Database
SQLite
Development Tools
Visual Studio Code
Git
GitHub

⚙️ System Workflow
Patient
   ↓
Upload Discharge Summary
   ↓
PDF Text Extraction / OCR
   ↓
Patient & Medical Information Extraction
   ↓
Case Identification
   ↓
Rule-Based Processing
   ↓
┌─────────────────────────────┐
│ Simplified Report           │
│ Recovery Guidance           │
│ Diet Recommendations        │
│ Medication Schedule         │
│ Physiotherapy Guidance      │
│ Follow-up Information       │
└─────────────────────────────┘
   ↓
Patient Dashboard
   ↓
Doctor Consultation / Chat

📁 Project Structure
recovery-assist/
│
├── app.py
├── models.py
├── clear_db.py
├── requirements.txt
├── README.md
│
├── datasets/
│   └── ...
│
├── static/
│   ├── logo.svg
│   ├── main.css
│   └── script.js
│
└── templates/
    ├── home.html
    ├── login.html
    ├── register.html
    ├── dashboard.html
    ├── upload.html
    ├── simplified_report.html
    ├── recovery.html
    ├── diet.html
    ├── medication.html
    ├── physiotherapy.html
    ├── followup.html
    ├── doctors_list.html
    ├── chat.html
    └── ...

👥 Team 
Team Size: 4 Members
Project Type: B.Tech Mini Project
Department: Computer Science and Engineering

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


# ---------------- PATIENT MODEL ----------------

class User(UserMixin, db.Model):

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)

    age = db.Column(db.Integer, nullable=False)

    gender = db.Column(db.String(10), nullable=False)

    phone = db.Column(db.String(15), nullable=False)

    email = db.Column(db.String(120), unique=True, nullable=False)

    password = db.Column(db.String(200), nullable=False)



# ---------------- DOCTOR MODEL ----------------

class Doctor(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    doctor_id = db.Column(db.String(50), unique=True, nullable=False)

    name = db.Column(db.String(100), nullable=False)

    password = db.Column(db.String(200), nullable=False)



# ---------------- CHAT MODEL ----------------

class Chat(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    sender = db.Column(db.String(50))

    message = db.Column(db.Text)

    timestamp = db.Column(db.DateTime, server_default=db.func.now())
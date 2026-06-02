from __future__ import annotations

BRANCHES = ["Mysore Central", "Bangalore North"]

SPECIALTY_LABELS = {
    "General": {
        "doctor": "DOCQ General",
        "department": "General Medicine",
        "slot_note": "Same-day general consultation slots are available.",
        "branch": "Mysore Central",
    },
    "Cardiology": {
        "doctor": "DOCQ Cardiology",
        "department": "Cardiology",
        "slot_note": "Cardiac review slots are prioritized for chest discomfort cases.",
        "branch": "Mysore Central",
    },
    "Orthopedics": {
        "doctor": "DOCQ Orthopedics",
        "department": "Orthopedics",
        "slot_note": "Musculoskeletal assessment slots are available this week.",
        "branch": "Mysore Central",
    },
    "Neurology": {
        "doctor": "DOCQ Neurology",
        "department": "Neurology",
        "slot_note": "Neurology triage slots are available for further evaluation.",
        "branch": "Bangalore North",
    },
    "Dermatology": {
        "doctor": "DOCQ Dermatology",
        "department": "Dermatology",
        "slot_note": "Skin consultation slots are open for new patients.",
        "branch": "Bangalore North",
    },
    "Gastroenterology": {
        "doctor": "DOCQ Gastro",
        "department": "Gastroenterology",
        "slot_note": "Digestive health consultations can be scheduled for the next working day.",
        "branch": "Bangalore North",
    },
    "Psychiatry": {
        "doctor": "DOCQ Psychiatry",
        "department": "Psychiatry",
        "slot_note": "Mental health intake calls are available for follow-up scheduling.",
        "branch": "Bangalore North",
    },
    "Oncology": {
        "doctor": "DOCQ Oncology",
        "department": "Oncology",
        "slot_note": "Specialist referral review is required before slot confirmation.",
        "branch": "Bangalore North",
    },
    "Pulmonology": {
        "doctor": "DOCQ Pulmonology",
        "department": "Pulmonology",
        "slot_note": "Respiratory care slots are available for breathing-related cases.",
        "branch": "Mysore Central",
    },
    "ENT": {
        "doctor": "DOCQ ENT",
        "department": "ENT",
        "slot_note": "ENT consultations are open for throat, ear, and sinus complaints.",
        "branch": "Mysore Central",
    },
    "Ophthalmology": {
        "doctor": "DOCQ Ophthalmology",
        "department": "Ophthalmology",
        "slot_note": "Eye-care consultations are available for vision and eye discomfort concerns.",
        "branch": "Bangalore North",
    },
    "Endocrinology": {
        "doctor": "DOCQ Endocrinology",
        "department": "Endocrinology",
        "slot_note": "Endocrine and diabetes review slots are available this week.",
        "branch": "Mysore Central",
    },
    "Gynecology": {
        "doctor": "DOCQ Gynecology",
        "department": "Gynecology",
        "slot_note": "Women's health consultations are available for pregnancy and gynecology concerns.",
        "branch": "Bangalore North",
    },
    "Pediatrics": {
        "doctor": "DOCQ Pediatrics",
        "department": "Pediatrics",
        "slot_note": "Pediatric consultations are available for child health concerns.",
        "branch": "Mysore Central",
    },
}

DEFAULT_SPECIALTY = {
    "doctor": "DOCQ General",
    "department": "General Medicine",
    "slot_note": "General screening is available for new patient intake.",
    "branch": "Mysore Central",
}

DOCTOR_ACCOUNTS = [
    {"name": "Dr. Asha Rao", "email": "cardio@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Cardiology", "specialty": "Cardiology", "branch": "Mysore Central", "phone": "9100000001"},
    {"name": "Dr. Vikram Iyer", "email": "cardio2@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Cardiology Plus", "specialty": "Cardiology", "branch": "Bangalore North", "phone": "9100000005"},
    {"name": "Dr. Naveen Bhat", "email": "ortho@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Orthopedics", "specialty": "Orthopedics", "branch": "Mysore Central", "phone": "9100000002"},
    {"name": "Dr. Kavya Menon", "email": "ortho2@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Ortho Motion", "specialty": "Orthopedics", "branch": "Mysore Central", "phone": "9100000006"},
    {"name": "Dr. Suraj Kulkarni", "email": "ortho3@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Ortho Spine", "specialty": "Orthopedics", "branch": "Bangalore North", "phone": "9100000007"},
    {"name": "Dr. Meera Jain", "email": "derma@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Dermatology", "specialty": "Dermatology", "branch": "Bangalore North", "phone": "9100000003"},
    {"name": "Dr. Rahul Sen", "email": "general@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ General", "specialty": "General", "branch": "Mysore Central", "phone": "9100000004"},
    {"name": "Dr. Nisha Prabhu", "email": "general2@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Family Care", "specialty": "General", "branch": "Bangalore North", "phone": "9100000008"},
    {"name": "Dr. Ananya Desai", "email": "neuro@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Neurology", "specialty": "Neurology", "branch": "Bangalore North", "phone": "9100000009"},
    {"name": "Dr. Imran Qureshi", "email": "pulmo@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Pulmonology", "specialty": "Pulmonology", "branch": "Mysore Central", "phone": "9100000010"},
    {"name": "Dr. Kavita Shah", "email": "ent@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ ENT", "specialty": "ENT", "branch": "Mysore Central", "phone": "9100000011"},
    {"name": "Dr. Rohan Pai", "email": "ophthal@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Ophthalmology", "specialty": "Ophthalmology", "branch": "Bangalore North", "phone": "9100000012"},
    {"name": "Dr. Farah Khan", "email": "endo@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Endocrinology", "specialty": "Endocrinology", "branch": "Mysore Central", "phone": "9100000013"},
    {"name": "Dr. Shalini Rao", "email": "gyn@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Gynecology", "specialty": "Gynecology", "branch": "Bangalore North", "phone": "9100000014"},
    {"name": "Dr. Peter D'Souza", "email": "peds@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Pediatrics", "specialty": "Pediatrics", "branch": "Mysore Central", "phone": "9100000015"},
    {"name": "Dr. Devika Nair", "email": "gastro@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Gastro", "specialty": "Gastroenterology", "branch": "Bangalore North", "phone": "9100000016"},
    {"name": "Dr. Sameer Ali", "email": "psych@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Psychiatry", "specialty": "Psychiatry", "branch": "Bangalore North", "phone": "9100000017"},
    {"name": "Dr. Leela Mathew", "email": "onco@docq.local", "role": "doctor", "password": "doctor123", "doctor_name": "DOCQ Oncology", "specialty": "Oncology", "branch": "Bangalore North", "phone": "9100000018"},
]

DEFAULT_SLOT_TIMES = ["09:00", "09:30", "10:00", "10:30", "11:30", "12:00", "14:00", "14:30", "15:30", "16:00"]
URGENT_KEYWORDS = {
    "chest pain",
    "shortness of breath",
    "breathing difficulty",
    "stroke",
    "unconscious",
    "severe bleeding",
    "heavy bleeding",
    "blood vomiting",
    "seizure",
    "heart attack",
    "fainting",
    "broken bone",
    "bone broke",
    "visible deformity",
    "visibly deformed",
    "bone broke",
    "road accident",
    "head injury",
    "facial droop",
    "slurred speech",
    "sudden weakness",
}
LOW_CONFIDENCE_THRESHOLD = 55.0
REVIEW_CONFIDENCE_THRESHOLD = 70.0

SYMPTOM_PATTERNS = {
    "chest pain": ["chest pain", "chest tightness", "pressure in chest"],
    "breathing difficulty": ["shortness of breath", "breathing difficulty", "trouble breathing", "breathless"],
    "fever": ["fever", "high temperature"],
    "headache": ["headache", "migraine"],
    "dizziness": ["dizziness", "lightheaded", "vertigo"],
    "weakness": ["weakness", "fatigue", "tiredness"],
    "numbness": ["numbness", "tingling"],
    "rash": ["rash", "itching", "skin eruption"],
    "joint pain": ["joint pain", "knee pain", "back pain", "shoulder pain"],
    "broken bone": ["broken bone", "bone broke", "fracture", "limb deformity", "visible deformity", "knee broke"],
    "stomach pain": ["stomach pain", "abdominal pain", "abdomen pain"],
    "vomiting": ["vomiting", "nausea"],
    "cough": ["cough", "persistent cough"],
    "speech difficulty": ["slurred speech", "speech difficulty", "trouble speaking"],
    "facial droop": ["facial droop", "face droop", "face dropping"],
    "severe bleeding": ["severe bleeding", "heavy bleeding", "bleeding heavily"],
    "head injury": ["head injury", "hit my head", "head trauma"],
    "eye pain": ["eye pain", "vision loss", "blurred vision"],
    "ear pain": ["ear pain", "earache", "ear infection"],
    "diabetes concern": ["diabetes", "blood sugar", "high sugar"],
    "pregnancy concern": ["pregnant", "pregnancy", "pregnancy pain"],
    "child illness": ["child fever", "baby fever", "infant fever", "child illness"],
}

EMERGENCY_KEYWORDS = {
    "chest pain",
    "shortness of breath",
    "trouble breathing",
    "breathing difficulty",
    "stroke",
    "face droop",
    "facial droop",
    "slurred speech",
    "speech difficulty",
    "sudden weakness",
    "severe bleeding",
    "heavy bleeding",
    "unconscious",
    "seizure",
    "heart attack",
    "road accident",
    "head injury",
    "broken bone with bleeding",
    "visible deformity",
}

HIGH_SEVERITY_KEYWORDS = {
    "persistent",
    "worsening",
    "severe",
    "fainting",
    "blood vomiting",
    "high fever",
    "breathing difficulty",
    "broken bone",
    "bone broke",
    "broke",
    "fracture",
    "head injury",
    "major trauma",
    "deformed",
}

CONDITION_RISK_PATTERNS = {
    "diabetes": ["diabetes", "high blood sugar"],
    "hypertension": ["hypertension", "high blood pressure", "bp"],
    "asthma": ["asthma", "wheezing"],
    "heart disease": ["heart disease", "cardiac history", "angioplasty"],
    "stroke history": ["stroke history", "previous stroke"],
    "pregnancy": ["pregnant", "pregnancy"],
}

QUICK_AID_RULES = [
    {
        "triggers": ["chest pain", "shortness of breath", "breathing difficulty"],
        "advice": "Sit upright, reduce activity, loosen tight clothing, and seek immediate emergency evaluation if pain or breathlessness is worsening.",
    },
    {
        "triggers": ["fever", "vomiting"],
        "advice": "Hydrate in small sips, monitor temperature, and avoid self-medicating beyond routine fever relief already approved by your clinician.",
    },
    {
        "triggers": ["dizziness", "weakness"],
        "advice": "Sit or lie down, avoid driving, and keep someone nearby if symptoms are progressing.",
    },
    {
        "triggers": ["joint pain"],
        "advice": "Limit strain on the affected area, rest it, and avoid forceful movement until clinical review.",
    },
    {
        "triggers": ["rash"],
        "advice": "Keep the area clean and avoid new creams or irritants until a clinician reviews the rash.",
    },
]

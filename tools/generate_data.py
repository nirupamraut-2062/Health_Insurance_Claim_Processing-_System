"""Generate the synthetic ClaimSure dataset into data/*.json.

Files are written as MongoDB Extended JSON (dates as {"$date": ...}) so they
load with both ``bson.json_util`` (Python) and ``EJSON.parse`` (mongosh).

The data is deterministic (fixed random seed) and entirely fictional: insurer,
hospital, people and company names are made up.

    python tools/generate_data.py
"""
import math
import os
import random
import sys
from datetime import datetime, timedelta

from bson import json_util
from bson.json_util import RELAXED_JSON_OPTIONS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from claimsure.adjudication import adjudicate  # noqa: E402
from claimsure.constants import (  # noqa: E402
    ROOM_TARIFF, ROLES, DOCUMENT_TYPES,
    INTIMATED, DOCS_PENDING, UNDER_REVIEW, QUERY_RAISED, ESCALATED,
    APPROVED, PARTIALLY_APPROVED, REJECTED, SETTLED, WITHDRAWN,
)
from claimsure.fraud import score_claim  # noqa: E402
from claimsure.utils import age_on  # noqa: E402

SEED = 2026
TODAY = datetime(2026, 10, 4, 18, 0)
CLAIMS_FROM = datetime(2024, 4, 1)
N_HOUSEHOLDS = 2200
N_CLAIMS = 2600
DATA_DIR = os.path.join(ROOT, "data")

rng = random.Random(SEED)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def pick(seq, weights=None):
    return rng.choices(seq, weights=weights, k=1)[0]


def rand_dt(start, end):
    span = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(span, 0)))


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def day(d):
    return datetime(d.year, d.month, d.day)


def round_to(x, base=10):
    return int(base * round(float(x) / base))


def office_time(d):
    """Move a timestamp into 09:00-19:00 on the same day."""
    return d.replace(hour=rng.randint(9, 18), minute=rng.randint(0, 59), second=0, microsecond=0)


# --------------------------------------------------------------------------- #
# reference data
# --------------------------------------------------------------------------- #
CITIES = [
    # name, state, tier, pincode prefix, lat, lon, population weight
    ("Mumbai", "Maharashtra", 1, "400", 19.0760, 72.8777, 10),
    ("Pune", "Maharashtra", 1, "411", 18.5204, 73.8567, 7),
    ("Nagpur", "Maharashtra", 2, "440", 21.1458, 79.0882, 3),
    ("New Delhi", "Delhi", 1, "110", 28.6139, 77.2090, 10),
    ("Gurugram", "Haryana", 1, "122", 28.4595, 77.0266, 4),
    ("Noida", "Uttar Pradesh", 1, "201", 28.5355, 77.3910, 4),
    ("Lucknow", "Uttar Pradesh", 2, "226", 26.8467, 80.9462, 4),
    ("Bengaluru", "Karnataka", 1, "560", 12.9716, 77.5946, 10),
    ("Mysuru", "Karnataka", 2, "570", 12.2958, 76.6394, 2),
    ("Chennai", "Tamil Nadu", 1, "600", 13.0827, 80.2707, 8),
    ("Coimbatore", "Tamil Nadu", 2, "641", 11.0168, 76.9558, 3),
    ("Vellore", "Tamil Nadu", 3, "632", 12.9165, 79.1325, 2),
    ("Hyderabad", "Telangana", 1, "500", 17.3850, 78.4867, 8),
    ("Kolkata", "West Bengal", 1, "700", 22.5726, 88.3639, 7),
    ("Ahmedabad", "Gujarat", 1, "380", 23.0225, 72.5714, 6),
    ("Surat", "Gujarat", 2, "395", 21.1702, 72.8311, 3),
    ("Jaipur", "Rajasthan", 2, "302", 26.9124, 75.7873, 4),
    ("Kochi", "Kerala", 2, "682", 9.9312, 76.2673, 3),
    ("Thiruvananthapuram", "Kerala", 2, "695", 8.5241, 76.9366, 2),
    ("Bhopal", "Madhya Pradesh", 2, "462", 23.2599, 77.4126, 2),
    ("Indore", "Madhya Pradesh", 2, "452", 22.7196, 75.8577, 3),
    ("Chandigarh", "Chandigarh", 2, "160", 30.7333, 76.7794, 2),
    ("Patna", "Bihar", 2, "800", 25.5941, 85.1376, 2),
    ("Bhubaneswar", "Odisha", 2, "751", 20.2961, 85.8245, 2),
    ("Guwahati", "Assam", 2, "781", 26.1445, 91.7362, 2),
    ("Visakhapatnam", "Andhra Pradesh", 2, "530", 17.6868, 83.2185, 2),
]
CITY = {c[0]: c for c in CITIES}
REGION = {
    "Delhi": "North", "Haryana": "North", "Uttar Pradesh": "North", "Rajasthan": "North",
    "Chandigarh": "North", "Bihar": "East", "West Bengal": "East", "Odisha": "East", "Assam": "East",
    "Maharashtra": "West", "Gujarat": "West", "Madhya Pradesh": "West",
    "Karnataka": "South", "Tamil Nadu": "South", "Telangana": "South", "Kerala": "South",
    "Andhra Pradesh": "South",
}
STATE_CODE = {
    "Maharashtra": "MMC", "Delhi": "DMC", "Haryana": "HMC", "Uttar Pradesh": "UPMC",
    "Karnataka": "KMC", "Tamil Nadu": "TNMC", "Telangana": "TSMC", "West Bengal": "WBMC",
    "Gujarat": "GMC", "Rajasthan": "RMC", "Kerala": "TCMC", "Madhya Pradesh": "MPMC",
    "Chandigarh": "PMC", "Bihar": "BMC", "Odisha": "OCMR", "Assam": "AMC", "Andhra Pradesh": "APMC",
}
TIER_COST = {1: 1.15, 2: 0.95, 3: 0.8}

MALE = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Rahul", "Amit", "Vikram", "Sanjay", "Rajesh",
        "Suresh", "Ramesh", "Anil", "Sunil", "Deepak", "Manoj", "Karthik", "Pranav", "Nikhil", "Siddharth",
        "Harsh", "Kunal", "Varun", "Abhishek", "Gaurav", "Ankit", "Mohit", "Naveen", "Prakash", "Venkatesh",
        "Srinivas", "Arun", "Ravi", "Ganesh", "Mahesh", "Imran", "Faisal", "Joseph", "Thomas", "Gurpreet",
        "Harjit", "Sourav", "Debashish", "Anirban", "Bhavesh", "Hitesh", "Kiran", "Yash", "Ishaan", "Kabir",
        "Dhruv", "Reyansh", "Ayaan", "Vihaan", "Atharv", "Shaurya", "Rudra", "Tejas", "Farhan", "Akash"]
FEMALE = ["Aadhya", "Ananya", "Diya", "Saanvi", "Pari", "Priya", "Pooja", "Sneha", "Neha", "Kavita",
          "Sunita", "Anita", "Meena", "Lakshmi", "Divya", "Swati", "Shreya", "Ritu", "Nisha", "Anjali",
          "Deepa", "Rekha", "Geeta", "Sarita", "Asha", "Usha", "Lata", "Radha", "Kavya", "Ishita",
          "Tanvi", "Riya", "Aishwarya", "Bhavana", "Charu", "Fatima", "Ayesha", "Mary", "Simran", "Harpreet",
          "Sushmita", "Moumita", "Payal", "Hetal", "Janhvi", "Meera", "Nandini", "Pallavi", "Rashmi", "Shalini",
          "Sowmya", "Vaishnavi", "Yamini", "Zara", "Myra", "Anika", "Kiara", "Navya", "Aarohi", "Tara"]
SURNAMES = ["Sharma", "Verma", "Gupta", "Agarwal", "Singh", "Kumar", "Patel", "Shah", "Mehta", "Desai",
            "Joshi", "Kulkarni", "Deshpande", "Patil", "Pawar", "Iyer", "Iyengar", "Nair", "Menon", "Pillai",
            "Reddy", "Rao", "Naidu", "Choudhary", "Yadav", "Mishra", "Tiwari", "Pandey", "Dubey", "Srivastava",
            "Banerjee", "Chatterjee", "Mukherjee", "Das", "Ghosh", "Bose", "Sen", "Dutta", "Khan", "Ansari",
            "Qureshi", "Fernandes", "D'Souza", "Thomas", "George", "Gill", "Sandhu", "Dhillon", "Malhotra",
            "Kapoor", "Khanna", "Chopra", "Bhatt", "Trivedi", "Saxena", "Raut", "Hegde", "Shetty", "Kamath",
            "Murthy"]
STREETS = ["MG Road", "Station Road", "Gandhi Nagar", "Shivaji Nagar", "Park Street", "Nehru Colony",
           "Civil Lines", "Model Town", "Anna Nagar", "Banjara Hills", "Koramangala", "Salt Lake",
           "Navrangpura", "Sector 15", "Malviya Nagar", "Jubilee Hills", "Indiranagar", "Andheri East",
           "Kothrud", "Vasant Kunj", "Rajaji Nagar", "Ashok Vihar", "Tilak Road", "Lake View Road"]
OCCUPATIONS = ["Software Engineer", "Teacher", "Business Owner", "Doctor", "Accountant", "Bank Officer",
               "Government Employee", "Sales Executive", "Homemaker", "Consultant", "Lawyer", "Architect",
               "Shop Owner", "Nurse", "Civil Engineer", "Pharmacist", "Marketing Manager", "Farmer",
               "Chartered Accountant", "Professor"]
EMPLOYERS = ["Nimbus Technologies Pvt. Ltd.", "Orion Fintech Solutions Ltd.", "Vasudha Textiles Ltd.",
             "Kaveri Logistics Pvt. Ltd.", "Zenith Pharma Ltd.", "Aravali Infra Projects Ltd.",
             "BlueWave Retail Pvt. Ltd.", "Sahyog Co-operative Bank", "Trident Auto Components Ltd.",
             "Indigo Hospitality Pvt. Ltd."]
BANKS = [("Pragati Bank", "PRGB"), ("Sahakari Bank", "SHKB"), ("Janseva Bank", "JNSB"),
         ("Unity Federal Bank", "UNFB"), ("Bharat Co-operative Bank", "BCOB")]

INSURERS = [
    # id, name, code, brand, type, hq city, founded, market share, TAT factor, strictness
    ("INS01", "Suraksha General Insurance Co. Ltd.", "SGI", "Suraksha", "General Insurer (Private)", "Mumbai", 2001, 12, 1.0, 1.0),
    ("INS02", "Arogya Shield Health Insurance Ltd.", "ASH", "Arogya Shield", "Standalone Health Insurer", "Chennai", 2006, 14, 0.7, 0.9),
    ("INS03", "Bharat Mutual Assurance Co. Ltd.", "BMA", "Bharat", "General Insurer (Public Sector)", "New Delhi", 1956, 13, 1.6, 1.3),
    ("INS04", "Kavach Health Insurance Ltd.", "KHI", "Kavach", "Standalone Health Insurer", "Bengaluru", 2012, 9, 0.8, 0.8),
    ("INS05", "Navjeevan General Insurance Co. Ltd.", "NGI", "Navjeevan", "General Insurer (Private)", "Pune", 2008, 10, 1.1, 1.1),
    ("INS06", "Sanjeevani Health Insurance Ltd.", "SHI", "Sanjeevani", "Standalone Health Insurer", "Hyderabad", 2015, 7, 0.9, 1.2),
    ("INS07", "Prithvi General Insurance Co. Ltd.", "PGI", "Prithvi", "General Insurer (Private)", "Gurugram", 2007, 8, 1.0, 1.0),
    ("INS08", "Raksha National Insurance Co. Ltd.", "RNI", "Raksha", "General Insurer (Public Sector)", "Kolkata", 1947, 11, 1.7, 1.4),
    ("INS09", "Unity Care General Insurance Ltd.", "UCG", "Unity Care", "General Insurer (Private)", "Ahmedabad", 2010, 8, 1.2, 1.2),
    ("INS10", "Medisure Health Insurance Ltd.", "MHI", "Medisure", "Standalone Health Insurer", "Kochi", 2018, 6, 0.85, 0.9),
]
INS = {i[0]: i for i in INSURERS}

TPAS = [("TPA01", "CareLink TPA Services Pvt. Ltd.", "Mumbai", 2002),
        ("TPA02", "MedServe Health TPA Pvt. Ltd.", "Bengaluru", 2005),
        ("TPA03", "HealthBridge TPA Ltd.", "New Delhi", 2001),
        ("TPA04", "Sahayog Health TPA Pvt. Ltd.", "Chennai", 2009),
        ("TPA05", "TrueClaim TPA Services Ltd.", "Hyderabad", 2011),
        ("TPA06", "Nirvana Health TPA Pvt. Ltd.", "Kolkata", 2004)]
INSURER_TPAS = {"INS01": ["TPA01", "TPA03"], "INS02": [], "INS03": ["TPA03", "TPA06", "TPA04"],
                "INS04": [], "INS05": ["TPA02"], "INS06": [], "INS07": ["TPA01", "TPA05"],
                "INS08": ["TPA06", "TPA04"], "INS09": ["TPA05", "TPA02"], "INS10": []}

SPECIALTIES = ["General Medicine", "General Surgery", "Cardiology", "Orthopaedics", "Neurology", "Oncology",
               "Pulmonology", "Gastroenterology", "Nephrology", "Urology", "Obstetrics & Gynaecology",
               "Paediatrics", "Ophthalmology", "ENT", "Psychiatry", "Emergency Medicine"]
QUALIFICATION = {
    "General Medicine": "MBBS, MD (General Medicine)", "General Surgery": "MBBS, MS (General Surgery)",
    "Cardiology": "MBBS, MD, DM (Cardiology)", "Orthopaedics": "MBBS, MS (Orthopaedics)",
    "Neurology": "MBBS, MD, DM (Neurology)", "Oncology": "MBBS, MD, DM (Medical Oncology)",
    "Pulmonology": "MBBS, MD (Pulmonary Medicine)", "Gastroenterology": "MBBS, MD, DM (Gastroenterology)",
    "Nephrology": "MBBS, MD, DM (Nephrology)", "Urology": "MBBS, MS, MCh (Urology)",
    "Obstetrics & Gynaecology": "MBBS, MS (Obstetrics & Gynaecology)", "Paediatrics": "MBBS, MD (Paediatrics)",
    "Ophthalmology": "MBBS, MS (Ophthalmology)", "ENT": "MBBS, MS (ENT)", "Psychiatry": "MBBS, MD (Psychiatry)",
    "Emergency Medicine": "MBBS, MD (Emergency Medicine)",
}

# code, description, ICD chapter, specialty, procedure, surgical, avg cost, avg LOS, frequency weight, extras
ICD = [
    ("A90", "Dengue fever", "Infectious diseases", "General Medicine", None, False, 45000, 4, 8, {"season": "monsoon"}),
    ("A01.0", "Typhoid fever", "Infectious diseases", "General Medicine", None, False, 38000, 4, 5, {"season": "monsoon"}),
    ("B50.9", "Plasmodium falciparum malaria", "Infectious diseases", "General Medicine", None, False, 42000, 4, 3, {"season": "monsoon"}),
    ("A09", "Infectious gastroenteritis and colitis", "Infectious diseases", "General Medicine", None, False, 28000, 2, 9, {"season": "monsoon"}),
    ("A41.9", "Sepsis, unspecified organism", "Infectious diseases", "General Medicine", None, False, 230000, 8, 2, {"icu": True, "min_age": 40}),
    ("U07.1", "COVID-19, virus identified", "Special purposes", "Pulmonology", None, False, 120000, 8, 1, {"icu": True}),
    ("J18.9", "Pneumonia, unspecified organism", "Respiratory system", "Pulmonology", None, False, 85000, 5, 7, {"season": "winter"}),
    ("J44.1", "COPD with acute exacerbation", "Respiratory system", "Pulmonology", None, False, 72000, 5, 3, {"season": "winter", "min_age": 45, "ped": "Asthma"}),
    ("J45.909", "Asthma, uncomplicated", "Respiratory system", "Pulmonology", None, False, 35000, 3, 4, {"season": "winter", "ped": "Asthma"}),
    ("J32.9", "Chronic sinusitis", "Respiratory system", "ENT", "Functional endoscopic sinus surgery (FESS)", True, 72000, 1, 2, {"specific": True, "day_care": True}),
    ("J35.01", "Chronic tonsillitis", "Respiratory system", "ENT", "Tonsillectomy", True, 52000, 1, 2, {"specific": True, "max_age": 40}),
    ("H66.90", "Otitis media, unspecified", "Ear and mastoid", "ENT", None, False, 25000, 1, 1, {"day_care": True}),
    ("I21.9", "Acute myocardial infarction", "Circulatory system", "Cardiology", "Coronary angioplasty (PTCA) with stent", True, 360000, 6, 4, {"icu": True, "implant": True, "min_age": 30, "ped": "Heart Disease", "emergency": True}),
    ("I25.10", "Coronary artery disease", "Circulatory system", "Cardiology", "Coronary artery bypass grafting (CABG)", True, 420000, 8, 3, {"icu": True, "min_age": 35, "ped": "Heart Disease"}),
    ("I10", "Essential (primary) hypertension", "Circulatory system", "General Medicine", None, False, 32000, 2, 4, {"min_age": 25, "ped": "Hypertension"}),
    ("I50.9", "Heart failure, unspecified", "Circulatory system", "Cardiology", None, False, 180000, 7, 2, {"icu": True, "min_age": 40, "ped": "Heart Disease"}),
    ("I48.91", "Atrial fibrillation", "Circulatory system", "Cardiology", None, False, 110000, 4, 1, {"min_age": 40, "ped": "Heart Disease"}),
    ("R07.9", "Chest pain, unspecified", "Symptoms and signs", "Cardiology", None, False, 32000, 2, 3, {"min_age": 25}),
    ("I63.9", "Cerebral infarction (stroke)", "Circulatory system", "Neurology", None, False, 310000, 9, 2, {"icu": True, "min_age": 40, "ped": "Hypertension", "emergency": True}),
    ("G40.909", "Epilepsy, unspecified", "Nervous system", "Neurology", None, False, 60000, 3, 2, {}),
    ("E11.9", "Type 2 diabetes mellitus without complications", "Endocrine & metabolic", "General Medicine", None, False, 42000, 3, 5, {"min_age": 25, "ped": "Diabetes"}),
    ("E11.65", "Type 2 diabetes mellitus with hyperglycaemia", "Endocrine & metabolic", "General Medicine", None, False, 85000, 5, 2, {"min_age": 25, "ped": "Diabetes"}),
    ("E05.90", "Thyrotoxicosis, unspecified", "Endocrine & metabolic", "General Medicine", None, False, 30000, 2, 1, {"ped": "Thyroid"}),
    ("E86.0", "Dehydration", "Endocrine & metabolic", "General Medicine", None, False, 22000, 2, 3, {"season": "summer"}),
    ("K35.80", "Acute appendicitis", "Digestive system", "General Surgery", "Laparoscopic appendectomy", True, 95000, 3, 5, {"emergency": True}),
    ("K80.20", "Calculus of gallbladder (gallstones)", "Digestive system", "General Surgery", "Laparoscopic cholecystectomy", True, 115000, 3, 4, {"specific": True}),
    ("K40.90", "Inguinal hernia", "Digestive system", "General Surgery", "Hernioplasty with mesh repair", True, 85000, 2, 3, {"specific": True, "implant": True}),
    ("K64.9", "Haemorrhoids", "Digestive system", "General Surgery", "Laser haemorrhoidectomy", True, 58000, 1, 3, {"specific": True, "day_care": True}),
    ("K29.70", "Gastritis, unspecified", "Digestive system", "Gastroenterology", None, False, 26000, 2, 4, {}),
    ("K74.60", "Cirrhosis of liver", "Digestive system", "Gastroenterology", None, False, 165000, 7, 1, {"min_age": 35}),
    ("K85.90", "Acute pancreatitis", "Digestive system", "Gastroenterology", None, False, 140000, 6, 1, {"icu": True, "min_age": 20}),
    ("N20.0", "Calculus of kidney", "Genitourinary system", "Urology", "Ureteroscopic lithotripsy (URSL)", True, 98000, 2, 4, {"specific": True}),
    ("N40.1", "Benign prostatic hyperplasia", "Genitourinary system", "Urology", "Transurethral resection of prostate (TURP)", True, 125000, 3, 2, {"specific": True, "gender": "Male", "min_age": 50}),
    ("N39.0", "Urinary tract infection", "Genitourinary system", "General Medicine", None, False, 30000, 3, 4, {}),
    ("N18.6", "End-stage renal disease", "Genitourinary system", "Nephrology", "Haemodialysis", False, 38000, 1, 1, {"day_care": True, "ped": "Kidney Disease", "min_age": 30}),
    ("H25.9", "Age-related cataract", "Eye and adnexa", "Ophthalmology", "Phacoemulsification with IOL", True, 48000, 1, 4, {"specific": True, "day_care": True, "implant": True, "min_age": 50}),
    ("M17.11", "Primary osteoarthritis, right knee", "Musculoskeletal system", "Orthopaedics", "Total knee replacement", True, 360000, 5, 2, {"specific": True, "implant": True, "min_age": 50, "ped": "Arthritis"}),
    ("M51.26", "Lumbar disc herniation", "Musculoskeletal system", "Orthopaedics", "Microdiscectomy", True, 230000, 4, 2, {"specific": True, "min_age": 25}),
    ("S72.00", "Fracture of neck of femur", "Injury & trauma", "Orthopaedics", "Hip hemiarthroplasty", True, 260000, 6, 2, {"accident": True, "implant": True, "min_age": 50, "emergency": True}),
    ("S82.20", "Fracture of shaft of tibia", "Injury & trauma", "Orthopaedics", "Open reduction internal fixation (ORIF)", True, 155000, 4, 3, {"accident": True, "implant": True, "emergency": True}),
    ("S52.50", "Fracture of lower end of radius", "Injury & trauma", "Orthopaedics", "Closed reduction and casting", True, 60000, 1, 3, {"accident": True, "day_care": True, "emergency": True}),
    ("T07", "Multiple injuries (road traffic accident)", "Injury & trauma", "Emergency Medicine", None, False, 210000, 7, 2, {"accident": True, "icu": True, "min_age": 16, "emergency": True}),
    ("S06.0", "Concussion", "Injury & trauma", "Neurology", None, False, 45000, 2, 2, {"accident": True, "emergency": True}),
    ("C50.9", "Malignant neoplasm of breast", "Neoplasms", "Oncology", "Modified radical mastectomy", True, 420000, 6, 2, {"gender": "Female", "min_age": 30}),
    ("C34.90", "Malignant neoplasm of lung", "Neoplasms", "Oncology", None, False, 460000, 7, 1, {"min_age": 40}),
    ("C18.9", "Malignant neoplasm of colon", "Neoplasms", "Oncology", "Hemicolectomy", True, 430000, 8, 1, {"min_age": 40}),
    ("Z51.11", "Antineoplastic chemotherapy session", "Factors influencing health", "Oncology", "Chemotherapy (day care)", False, 62000, 1, 2, {"day_care": True, "min_age": 20}),
    ("O80", "Single spontaneous (normal) delivery", "Pregnancy & childbirth", "Obstetrics & Gynaecology", "Normal vaginal delivery", False, 48000, 2, 4, {"maternity": True, "gender": "Female", "min_age": 21, "max_age": 40}),
    ("O82", "Caesarean delivery", "Pregnancy & childbirth", "Obstetrics & Gynaecology", "Lower segment caesarean section (LSCS)", True, 88000, 4, 4, {"maternity": True, "gender": "Female", "min_age": 21, "max_age": 42}),
    ("D25.9", "Leiomyoma of uterus (fibroids)", "Neoplasms", "Obstetrics & Gynaecology", "Laparoscopic hysterectomy", True, 135000, 3, 2, {"specific": True, "gender": "Female", "min_age": 30, "max_age": 60}),
    ("F32.2", "Major depressive disorder, severe", "Mental & behavioural", "Psychiatry", None, False, 65000, 10, 1, {"min_age": 18}),
    ("L03.90", "Cellulitis, unspecified", "Skin & subcutaneous", "General Medicine", None, False, 40000, 4, 2, {}),
    ("P07.30", "Preterm newborn requiring NICU care", "Perinatal conditions", "Paediatrics", None, False, 260000, 14, 1, {"icu": True, "max_age": 0}),
    ("Q21.0", "Ventricular septal defect", "Congenital anomalies", "Cardiology", "Device closure of VSD", True, 320000, 6, 1, {"implant": True, "max_age": 12}),
    ("Z41.1", "Cosmetic surgery for appearance", "Factors influencing health", "General Surgery", "Liposuction / cosmetic procedure", True, 150000, 2, 0.4, {"min_age": 20, "max_age": 55}),
]

PLAN_TEMPLATES = {
    "BASIC": dict(
        name="Arogya Basic", policy_types=["Individual", "Family Floater"], si=[300000, 500000], si_w=[6, 4],
        base_premium=11500, room={"type": "percent_si", "value": 1}, copay=0, pre=30, post=60, amb=2000,
        maternity=False, maternity_limit=0, newborn=False, restoration=False, ncb=(10, 50),
        wait=(30, 48, 24, 0), checkup=False,
        sub_limits=[{"label": "Cataract (per eye)", "icd_codes": ["H25.9"], "limit": 40000},
                    {"label": "Joint replacement", "icd_codes": ["M17.11"], "limit": 150000},
                    {"label": "Hernia / Haemorrhoids", "icd_codes": ["K40.90", "K64.9"], "limit": 50000}],
        excluded=["Z41.1", "P07.30"], entry=(18, 65)),
    "COMPREHENSIVE": dict(
        name="Family Health Plus", policy_types=["Individual", "Family Floater"], si=[500000, 1000000, 1500000],
        si_w=[5, 4, 1], base_premium=11500, room={"type": "category", "value": "Single Private"}, copay=0,
        pre=60, post=90, amb=5000, maternity=True, maternity_limit=60000, newborn=True, restoration=True,
        ncb=(20, 100), wait=(30, 36, 24, 24), checkup=True,
        sub_limits=[{"label": "Cataract (per eye)", "icd_codes": ["H25.9"], "limit": 60000}],
        excluded=["Z41.1"], entry=(18, 65)),
    "SENIOR": dict(
        name="Senior Citizen Care", policy_types=["Senior Citizen"], si=[300000, 500000, 1000000], si_w=[4, 4, 2],
        base_premium=21000, room={"type": "percent_si", "value": 1}, copay=20, pre=30, post=60, amb=2000,
        maternity=False, maternity_limit=0, newborn=False, restoration=False, ncb=(10, 50),
        wait=(30, 12, 24, 0), checkup=True,
        sub_limits=[{"label": "Cataract (per eye)", "icd_codes": ["H25.9"], "limit": 40000},
                    {"label": "Joint replacement", "icd_codes": ["M17.11"], "limit": 200000}],
        excluded=["Z41.1", "P07.30"], entry=(60, 80)),
    "PREMIER": dict(
        name="Premier Elite", policy_types=["Individual", "Family Floater"], si=[1000000, 2500000, 5000000],
        si_w=[5, 3, 1], base_premium=13500, room={"type": "none"}, copay=0, pre=90, post=180, amb=10000,
        maternity=True, maternity_limit=100000, newborn=True, restoration=True, ncb=(50, 100),
        wait=(30, 24, 24, 24), checkup=True, sub_limits=[], excluded=["Z41.1"], entry=(18, 65)),
    "GROUP": dict(
        name="Group Mediclaim", policy_types=["Group"], si=[300000, 500000], si_w=[5, 5], base_premium=15000,
        room={"type": "percent_si", "value": 2}, copay=0, pre=30, post=60, amb=3000, maternity=True,
        maternity_limit=50000, newborn=True, restoration=False, ncb=(0, 0), wait=(0, 0, 0, 0), checkup=False,
        sub_limits=[{"label": "Cataract (per eye)", "icd_codes": ["H25.9"], "limit": 50000}],
        excluded=["Z41.1"], entry=(18, 65)),
}
PLAN_OFFERINGS = {
    "BASIC": [i[0] for i in INSURERS], "COMPREHENSIVE": [i[0] for i in INSURERS],
    "SENIOR": ["INS02", "INS03", "INS04", "INS06", "INS08", "INS10"],
    "PREMIER": ["INS01", "INS02", "INS05", "INS07"],
    "GROUP": ["INS01", "INS03", "INS05", "INS07", "INS09"],
}

STAFF = [
    ("USR001", "Ananya Rao", "Admin", "Operations", "All India"),
    ("USR002", "Vikram Malhotra", "Claims Manager", "Claims", "North"),
    ("USR003", "Lakshmi Iyer", "Claims Manager", "Claims", "South"),
    ("USR004", "Sourav Banerjee", "Claims Manager", "Claims", "East"),
    ("USR005", "Hetal Shah", "Claims Manager", "Claims", "West"),
    ("USR006", "Rohan Deshpande", "Senior Claims Adjuster", "Claims", "West"),
    ("USR007", "Priya Nair", "Senior Claims Adjuster", "Claims", "South"),
    ("USR008", "Amit Choudhary", "Senior Claims Adjuster", "Claims", "North"),
    ("USR009", "Moumita Ghosh", "Senior Claims Adjuster", "Claims", "East"),
    ("USR010", "Karthik Reddy", "Senior Claims Adjuster", "Claims", "South"),
    ("USR011", "Neha Kapoor", "Claims Executive", "Claims", "North"),
    ("USR012", "Imran Qureshi", "Claims Executive", "Claims", "North"),
    ("USR013", "Swati Kulkarni", "Claims Executive", "Claims", "West"),
    ("USR014", "Bhavesh Patel", "Claims Executive", "Claims", "West"),
    ("USR015", "Sowmya Murthy", "Claims Executive", "Claims", "South"),
    ("USR016", "Arun Pillai", "Claims Executive", "Claims", "South"),
    ("USR017", "Debashish Das", "Claims Executive", "Claims", "East"),
    ("USR018", "Dr. Meera Menon", "Medical Officer", "Medical Review", "South"),
    ("USR019", "Dr. Gurpreet Gill", "Medical Officer", "Medical Review", "North"),
    ("USR020", "Dr. Faisal Ansari", "Medical Officer", "Medical Review", "West"),
    ("USR021", "Ritu Saxena", "Fraud Analyst", "Investigation Unit", "All India"),
    ("USR022", "Joseph Fernandes", "Fraud Analyst", "Investigation Unit", "All India"),
]

QUERY_QUESTIONS = [
    "Please submit indoor case papers for the entire stay.",
    "Provide the first consultation prescription for this ailment.",
    "Submit original pharmacy bills along with prescriptions.",
    "Clarify history and duration of the present illness.",
    "Provide investigation reports confirming the diagnosis.",
    "Submit implant invoice and sticker.",
    "Provide FIR / MLC copy for the accident.",
    "Confirm whether the patient has a history of alcohol consumption.",
]
QUERY_RESPONSES = ["Documents uploaded as requested.", "Clarification letter from treating doctor attached.",
                   "Original bills couriered and scanned copies uploaded.", "Reports attached."]


# --------------------------------------------------------------------------- #
# master data
# --------------------------------------------------------------------------- #
def build_insurers():
    docs = []
    for iid, name, code, brand, itype, hq, founded, *_ in INSURERS:
        city = CITY[hq]
        slug = brand.lower().replace(" ", "")
        docs.append({
            "insurer_id": iid, "name": name, "code": code, "brand": brand, "type": itype,
            "irdai_reg_no": f"IRDAI/{'HLT' if 'Standalone' in itype else 'GEN'}/{100 + int(iid[3:]) * 7:03d}",
            "headquarters": {"city": hq, "state": city[1]},
            "contact": {"toll_free": f"1800-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
                        "claims_email": f"claims@{slug}.example", "website": f"www.{slug}.example"},
            "established_year": founded,
            "solvency_ratio": round(rng.uniform(1.55, 2.6), 2),
            "customer_rating": round(rng.uniform(3.4, 4.6), 1),
            "tpa_ids": INSURER_TPAS[iid],
            "claims_handling": "Third Party Administrator" if INSURER_TPAS[iid] else "In-house",
        })
    return docs


def build_tpas():
    docs = []
    for tid, name, hq, est in TPAS:
        served = [i for i, t in INSURER_TPAS.items() if tid in t]
        docs.append({
            "tpa_id": tid, "name": name, "irdai_license_no": f"TPA-{int(tid[3:]):03d}/{est}",
            "headquarters": {"city": hq, "state": CITY[hq][1]},
            "contact": {"phone": f"+91-{rng.randint(20, 99)}-{rng.randint(20000000, 99999999)}",
                        "email": f"support@{name.split()[0].lower()}tpa.example"},
            "established_year": est, "insurer_ids": served,
            "sla_hours": {"cashless_preauth": rng.choice([2, 3, 4]), "reimbursement_days": rng.choice([15, 21, 30])},
        })
    return docs


def build_plans():
    docs = []
    for tkey, insurer_ids in PLAN_OFFERINGS.items():
        t = PLAN_TEMPLATES[tkey]
        for iid in insurer_ids:
            ins = INS[iid]
            factor = rng.uniform(0.9, 1.15)
            docs.append({
                "plan_id": f"PLN-{ins[2]}-{tkey[:3]}",
                "insurer_id": iid,
                "name": f"{ins[3]} {t['name']}",
                "category": tkey.title(),
                "policy_types": t["policy_types"],
                "sum_insured_options": t["si"],
                "base_premium": round_to(t["base_premium"] * factor, 100),
                "entry_age": {"min": t["entry"][0], "max": t["entry"][1]},
                "features": {
                    "room_rent_limit": t["room"],
                    "copay_percent": t["copay"],
                    "pre_hospitalisation_days": t["pre"],
                    "post_hospitalisation_days": t["post"],
                    "ambulance_limit": t["amb"],
                    "day_care_covered": True,
                    "ayush_covered": tkey != "BASIC",
                    "maternity_cover": t["maternity"],
                    "maternity_limit": t["maternity_limit"],
                    "newborn_cover": t["newborn"],
                    "restoration_benefit": t["restoration"],
                    "no_claim_bonus": {"percent_per_year": t["ncb"][0], "max_percent": t["ncb"][1]},
                    "annual_health_checkup": t["checkup"],
                },
                "waiting_periods": {"initial_days": t["wait"][0], "pre_existing_months": t["wait"][1],
                                    "specific_illness_months": t["wait"][2], "maternity_months": t["wait"][3]},
                "sub_limits": t["sub_limits"],
                "excluded_icd_codes": t["excluded"],
                "exclusions": ["Cosmetic or aesthetic treatment", "Self-inflicted injury",
                               "Experimental / unproven treatment", "Treatment outside India",
                               "War, nuclear and terrorism related injuries"],
                "launched_on": datetime(rng.randint(2014, 2019), rng.randint(1, 12), 1),
                "status": "Active",
            })
    return docs


def build_icd_codes():
    docs = []
    for code, desc, chapter, spec, proc, surgical, cost, los, weight, ex in ICD:
        docs.append({
            "code": code, "description": desc, "chapter": chapter, "specialty": spec,
            "procedure": proc, "is_surgical": surgical, "avg_cost": cost, "avg_los": los,
            "day_care": ex.get("day_care", False), "specific_illness": ex.get("specific", False),
            "is_maternity": ex.get("maternity", False), "is_accident": ex.get("accident", False),
            "requires_icu": ex.get("icu", False), "uses_implant": ex.get("implant", False),
            "ped_group": ex.get("ped"), "gender": ex.get("gender"),
            "min_age": ex.get("min_age", 0), "max_age": ex.get("max_age", 100),
            "season": ex.get("season"), "frequency_weight": weight,
        })
    return docs


HOSPITAL_PREFIX = ["Sunrise", "Lotus", "Lifeline", "CityCare", "Shanti", "Ganga", "Narmada", "Medipoint",
                   "Arogya", "Jeevan Jyoti", "Seva Sadan", "Prerna", "Nova", "Silverline", "Greenview",
                   "Riverside", "Pinnacle", "Crescent", "Lakeview", "Harmony", "Vardaan", "Sanjivani",
                   "Shree Sai", "Aastha", "Starlight", "Trinity", "Heritage", "Orchid", "Kamal", "Aakash",
                   "Swasthya", "Amrut", "Charak", "Sushrut", "Dhanvantari", "Ashwini", "Parijat", "Tulsi",
                   "Vatsalya", "Anand", "Kalpataru", "Indus", "Bodhi", "Kailash", "Neelkanth", "Mangal"]
HOSPITAL_KINDS = [
    # suffix, type, weight, beds, tariff, specialties
    ("Super Speciality Hospital", "Super Speciality", 15, (250, 700), (1.3, 1.6), None),
    ("Multispeciality Hospital", "Multi Speciality", 33, (100, 300), (1.0, 1.3), (9, 13)),
    ("Hospital & Research Centre", "Multi Speciality", 10, (150, 400), (1.05, 1.35), (10, 14)),
    ("Medical Centre", "Multi Speciality", 15, (50, 150), (0.9, 1.1), (6, 9)),
    ("Nursing Home", "Nursing Home", 12, (20, 50), (0.75, 0.95), (3, 5)),
    ("Eye Institute", "Single Speciality", 3, (20, 60), (0.9, 1.2), ["Ophthalmology"]),
    ("Mother & Child Hospital", "Single Speciality", 4, (40, 120), (0.95, 1.2), ["Obstetrics & Gynaecology", "Paediatrics"]),
    ("Heart Institute", "Single Speciality", 3, (80, 200), (1.2, 1.5), ["Cardiology", "Emergency Medicine"]),
    ("Orthopaedic Centre", "Single Speciality", 3, (40, 120), (1.0, 1.3), ["Orthopaedics", "Emergency Medicine"]),
    ("Kidney & Urology Institute", "Single Speciality", 2, (40, 100), (1.0, 1.25), ["Urology", "Nephrology"]),
]
CORE = ["General Medicine", "General Surgery", "Emergency Medicine"]


def build_hospitals():
    docs, used = [], set()
    seq = 0
    for cname, state, tier, pin, lat, lon, weight in CITIES:
        count = {1: rng.randint(5, 6), 2: rng.randint(2, 3), 3: 2}[tier]
        if weight >= 8:
            count += 1
        for _ in range(count):
            seq += 1
            kind = pick(HOSPITAL_KINDS, [k[2] for k in HOSPITAL_KINDS])
            suffix, htype, _, beds_r, tariff_r, spec_rule = kind
            while True:
                name = f"{pick(HOSPITAL_PREFIX)} {suffix}"
                if (name, cname) not in used:
                    used.add((name, cname))
                    break
            if spec_rule is None:
                specs = list(SPECIALTIES)
            elif isinstance(spec_rule, tuple):
                extra = [s for s in SPECIALTIES if s not in CORE]
                specs = CORE + rng.sample(extra, rng.randint(*spec_rule) - len(CORE))
            else:
                specs = list(spec_rule) + (["General Medicine"] if "General Medicine" not in spec_rule else [])
            if htype == "Nursing Home":
                specs = ["General Medicine", "General Surgery"] + rng.sample(
                    ["Obstetrics & Gynaecology", "Paediatrics", "ENT", "Orthopaedics"], rng.randint(1, 3))
            ownership = pick(["Private", "Trust", "Government"], [76, 14, 10])
            tariff = rng.uniform(*tariff_r)
            if ownership == "Government":
                tariff *= 0.6
            beds = rng.randint(*beds_r)
            n_ins = rng.randint(6, 10) if beds > 150 else rng.randint(2, 7)
            if ownership == "Government":
                pool = ["INS03", "INS08"] + rng.sample([i[0] for i in INSURERS if i[0] not in ("INS03", "INS08")], 2)
            else:
                pool = rng.sample([i[0] for i in INSURERS], n_ins)
            accreditation = ("JCI" if htype == "Super Speciality" and rng.random() < 0.4 else
                             "NABH" if beds > 60 or rng.random() < 0.4 else
                             "NABH Entry Level" if rng.random() < 0.5 else "None")
            slug = name.lower().replace(" & ", "-").replace(" ", "-")
            docs.append({
                "hospital_id": f"HSP{seq:04d}",
                "rohini_id": f"8900080{rng.randint(100000, 999999)}",
                "name": name,
                "type": htype,
                "ownership": ownership,
                "accreditation": accreditation,
                "address": {"line1": f"{rng.randint(1, 300)}, {pick(STREETS)}", "city": cname,
                            "state": state, "pincode": f"{pin}{rng.randint(1, 99):03d}"},
                "location": {"type": "Point",
                             "coordinates": [round(lon + rng.uniform(-0.08, 0.08), 5),
                                             round(lat + rng.uniform(-0.08, 0.08), 5)]},
                "city_tier": tier,
                "contact": {"phone": f"+91-{rng.randint(20, 99)}-{rng.randint(20000000, 99999999)}",
                            "email": f"tpa.desk@{slug}.example",
                            "emergency_24x7": htype != "Single Speciality" or "Emergency Medicine" in specs},
                "beds": beds,
                "icu_beds": max(0, int(beds * rng.uniform(0.06, 0.14))) if htype != "Nursing Home" else rng.randint(0, 4),
                "specialties": sorted(set(specs)),
                "tariff_factor": round(tariff, 2),
                "network": {"insurer_ids": sorted(pool),
                            "empanelled_since": datetime(rng.randint(2012, 2022), rng.randint(1, 12), 1)},
                "established_year": rng.randint(1965, 2018),
                "rating": round(rng.uniform(3.2, 4.8), 1),
                "watchlisted": False,
            })
    # A few small hospitals are under investigation for inflated billing
    for h in rng.sample([d for d in docs if d["type"] in ("Nursing Home", "Multi Speciality") and d["beds"] < 120], 4):
        h["watchlisted"] = True
        h["watchlist_reason"] = pick(["Repeated inflated billing", "Unusual admission patterns",
                                      "Mismatch between case papers and bills"])
    return docs


def build_doctors(hospitals):
    docs, seq = [], 0
    for h in hospitals:
        n = {"Super Speciality": rng.randint(6, 8), "Multi Speciality": rng.randint(4, 6),
             "Nursing Home": rng.randint(2, 3), "Single Speciality": rng.randint(2, 4)}[h["type"]]
        specs = list(h["specialties"])
        rng.shuffle(specs)
        chosen = (specs * 3)[:n]
        for spec in chosen:
            seq += 1
            gender = pick(["Male", "Female"], [62, 38])
            first = pick(MALE if gender == "Male" else FEMALE)
            exp = rng.randint(3, 35)
            docs.append({
                "doctor_id": f"DOC{seq:05d}",
                "name": f"Dr. {first} {pick(SURNAMES)}",
                "gender": gender,
                "specialization": spec,
                "qualification": QUALIFICATION[spec],
                "registration_no": f"{STATE_CODE[h['address']['state']]}/{2024 - exp - rng.randint(0, 3)}/{rng.randint(1000, 99999):05d}",
                "experience_years": exp,
                "hospital_id": h["hospital_id"],
                "consultation_fee": round_to(rng.uniform(500, 1500) + exp * 25, 50),
            })
    return docs


def build_users():
    docs = []
    for uid, name, role, dept, region in STAFF:
        plain = name.replace("Dr. ", "")
        docs.append({
            "user_id": uid, "name": name, "email": plain.lower().replace(" ", ".") + "@claimsure.example",
            "role": role, "department": dept, "region": region,
            "approval_limit": ROLES[role]["approval_limit"], "active": True,
            "joined_on": datetime(rng.randint(2012, 2023), rng.randint(1, 12), rng.randint(1, 28)),
        })
    return docs


def build_agents():
    docs = []
    brokers = ["Shield Insurance Brokers Pvt. Ltd.", "Trustline Insurance Brokers Ltd.", "Policywise Brokers Pvt. Ltd.",
               "Assure Partners Insurance Brokers", "SafeHarbour Insurance Brokers"]
    for i in range(1, 46):
        city = pick(CITIES, [c[6] for c in CITIES])
        kind = pick(["Individual Agent", "Broker", "Bancassurance", "Corporate Agent"], [62, 12, 16, 10])
        if kind == "Individual Agent":
            gender = pick(["Male", "Female"])
            name = f"{pick(MALE if gender == 'Male' else FEMALE)} {pick(SURNAMES)}"
            insurers = [pick([x[0] for x in INSURERS], [x[7] for x in INSURERS])]
            rate = 15.0
        elif kind == "Broker":
            name = f"{brokers[i % len(brokers)]} ({city[0]})"
            insurers = sorted(rng.sample([x[0] for x in INSURERS], 6))
            rate = 12.5
        elif kind == "Bancassurance":
            name = f"{pick(BANKS)[0]} - {city[0]} Main Branch"
            insurers = sorted(rng.sample([x[0] for x in INSURERS], 2))
            rate = 10.0
        else:
            name = f"{pick(['Prime', 'Secure', 'Apex', 'Vishwas', 'Sampoorna'])} Insurance Marketing Firm"
            insurers = sorted(rng.sample([x[0] for x in INSURERS], 3))
            rate = 12.0
        docs.append({
            "agent_id": f"AGT{i:04d}", "name": name, "type": kind,
            "license_no": f"IRDAI-{kind.split()[0][:3].upper()}-{rng.randint(10000000, 99999999)}",
            "insurer_ids": insurers, "branch": {"city": city[0], "state": city[1]},
            "region": REGION[city[1]], "commission_rate": rate,
            "joined_on": datetime(rng.randint(2010, 2024), rng.randint(1, 12), rng.randint(1, 28)),
            "status": "Active" if rng.random() > 0.08 else "Inactive",
            "contact": {"phone": f"+91-9{rng.randint(100000000, 999999999)}"},
        })
    return docs


# --------------------------------------------------------------------------- #
# members and policies
# --------------------------------------------------------------------------- #
def make_person(age, gender, surname, city, adult=True):
    first = pick(MALE if gender == "Male" else FEMALE)
    dob = day(TODAY - timedelta(days=int(age * 365.25) + rng.randint(0, 364)))
    peds = []
    if age >= 35 and rng.random() < 0.16:
        peds.append("Diabetes")
    if age >= 35 and rng.random() < 0.20:
        peds.append("Hypertension")
    if gender == "Female" and age >= 25 and rng.random() < 0.09:
        peds.append("Thyroid")
    if rng.random() < 0.05:
        peds.append("Asthma")
    if age >= 50 and rng.random() < 0.08:
        peds.append("Heart Disease")
    if age >= 50 and rng.random() < 0.09:
        peds.append("Arthritis")
    if age >= 45 and rng.random() < 0.015:
        peds.append("Kidney Disease")
    height = rng.gauss(170 if gender == "Male" else 157, 7) if age >= 16 else 80 + age * 5.5
    bmi = rng.gauss(25, 3.8) if age >= 16 else rng.gauss(17, 2)
    weight = bmi * (height / 100) ** 2
    person = {
        "name": f"{first} {surname}", "gender": gender, "dob": dob,
        "blood_group": pick(["O+", "B+", "A+", "AB+", "O-", "B-", "A-", "AB-"], [37, 32, 22, 7, 1, 1, 0.5, 0.5]),
        "height_cm": round(height), "weight_kg": round(weight, 1),
        "pre_existing_conditions": peds,
        "lifestyle": {
            "smoker": adult and rng.random() < (0.18 if gender == "Male" else 0.03),
            "alcohol": pick(["Never", "Occasional", "Regular"], [60, 32, 8]) if adult else "Never",
        },
        "address": city,
    }
    if adult:
        person["occupation"] = "Retired" if age >= 60 else pick(OCCUPATIONS)
    return person


def build_households():
    """Return (members, policies, households) with policies linked to insured members."""
    members, policies = [], []
    used_phones = set()
    member_seq = policy_seq = 0
    plans_by = {}
    for p in PLANS:
        for t in p["policy_types"]:
            plans_by.setdefault(t, []).append(p)
    agents_by_insurer = {}
    for a in AGENTS:
        if a["status"] == "Active":
            for i in a["insurer_ids"]:
                agents_by_insurer.setdefault(i, []).append(a)

    def phone():
        while True:
            p = f"+91-{rng.choice('6789')}{rng.randint(100000000, 999999999)}"
            if p not in used_phones:
                used_phones.add(p)
                return p

    years = list(range(2017, 2027))
    year_w = [3, 4, 5, 6, 8, 9, 10, 11, 12, 8]

    for h in range(1, N_HOUSEHOLDS + 1):
        c = pick(CITIES, [x[6] for x in CITIES])
        address = {"line1": f"{rng.randint(1, 400)}, {pick(STREETS)}", "city": c[0], "state": c[1],
                   "pincode": f"{c[3]}{rng.randint(1, 99):03d}"}
        surname = pick(SURNAMES)
        kind = pick(["single", "couple", "family", "senior_couple", "single_senior"], [24, 15, 41, 13, 7])
        people = []  # (person, relation)
        if kind in ("single", "couple", "family"):
            g = pick(["Male", "Female"], [65, 35])
            a1 = rng.randint(23, 58) if kind != "single" else rng.randint(22, 50)
            people.append((make_person(a1, g, surname, address), "Self"))
            if kind != "single":
                sg = "Female" if g == "Male" else "Male"
                a2 = max(21, a1 + rng.randint(-6, 4))
                people.append((make_person(a2, sg, surname, address), "Spouse"))
            if kind == "family":
                for _ in range(rng.choice([1, 1, 2, 2, 3])):
                    kg = pick(["Male", "Female"])
                    ka = rng.randint(0, min(17, max(0, a1 - 22)))
                    people.append((make_person(ka, kg, surname, address, adult=False),
                                   "Son" if kg == "Male" else "Daughter"))
        else:
            g = pick(["Male", "Female"], [60, 40])
            a1 = rng.randint(60, 76)
            people.append((make_person(a1, g, surname, address), "Self"))
            if kind == "senior_couple":
                people.append((make_person(max(58, a1 + rng.randint(-5, 2)), "Female" if g == "Male" else "Male",
                                           surname, address), "Spouse"))

        # policy
        group = kind in ("single", "couple", "family") and rng.random() < 0.17
        if group:
            ptype = "Group"
        elif kind in ("senior_couple", "single_senior"):
            ptype = "Senior Citizen"
        elif len(people) == 1:
            ptype = "Individual"
        else:
            ptype = "Family Floater"
        candidates = plans_by[ptype]
        if ptype in ("Individual", "Family Floater"):
            weights = [{"Basic": 45, "Comprehensive": 40, "Premier": 15}[p["category"]] *
                       INS[p["insurer_id"]][7] for p in candidates]
        else:
            weights = [INS[p["insurer_id"]][7] for p in candidates]
        plan = pick(candidates, weights)
        insurer = INS[plan["insurer_id"]]
        si = pick(plan["sum_insured_options"], PLAN_TEMPLATES[plan["category"].upper()]["si_w"])

        inception = day(datetime(pick(years, year_w), 1, 1) + timedelta(days=rng.randint(0, 364)))
        if inception > TODAY - timedelta(days=20):
            inception = day(TODAY - timedelta(days=rng.randint(20, 200)))
        terms_to_date = 0
        while add_years(inception, terms_to_date + 1) <= TODAY:
            terms_to_date += 1
        status, cancelled_on = "Active", None
        roll = rng.random()
        last_term = terms_to_date
        if terms_to_date >= 1 and roll < 0.11:
            status = "Expired"
            last_term = rng.randint(max(0, terms_to_date - 3), terms_to_date - 1)
        elif roll < 0.13:
            status = "Cancelled"
        start = add_years(inception, last_term)
        end = add_years(start, 1) - timedelta(days=1)
        if status == "Cancelled":
            cancelled_on = day(start + timedelta(days=rng.randint(30, 300)))
            if cancelled_on > TODAY:
                cancelled_on = day(TODAY - timedelta(days=5))

        eldest = max(age_on(p["dob"], start) for p, _ in people)
        ncb = PLAN_TEMPLATES[plan["category"].upper()]["ncb"]
        renewal_history = []
        for t in range(last_term + 1):
            ts = add_years(inception, t)
            age_factor = 1 + max(0, eldest - (last_term - t) - 30) * 0.035
            base = plan["base_premium"] * (si / 500000) ** 0.7 * age_factor * (1 + 0.45 * (len(people) - 1))
            if ptype == "Group":
                base = plan["base_premium"] * (si / 500000) ** 0.7 * (1 + 0.3 * (len(people) - 1))
            base = round_to(base * (1.07 ** (t - last_term)), 10)
            renewal_history.append({
                "term": t + 1, "start_date": ts, "end_date": add_years(ts, 1) - timedelta(days=1),
                "sum_insured": si, "ncb_percent": min(ncb[1], ncb[0] * t),
                "premium": {"base": base, "gst": round(base * 0.18), "total": base + round(base * 0.18)},
            })
        current = renewal_history[-1]

        policy_seq += 1
        proposer_id = None
        insured = []
        for person, relation in people:
            member_seq += 1
            mid = f"MEM{member_seq:06d}"
            age = age_on(person["dob"])
            adult = age >= 18
            email = f"{person['name'].split()[0].lower()}.{surname.lower().replace(chr(39), '')}{rng.randint(1, 999)}@example.com"
            member = {
                "member_id": mid,
                "name": person["name"],
                "gender": person["gender"],
                "dob": person["dob"],
                "marital_status": "Married" if relation in ("Self", "Spouse") and kind != "single" else "Single",
                "contact": {"phone": phone(), "email": email} if adult else
                           {"phone": members[-1]["contact"]["phone"] if members else phone(), "email": None},
                "address": address,
                "kyc": {"aadhaar_masked": f"XXXX-XXXX-{rng.randint(1000, 9999)}",
                        "pan_masked": f"XXXXX{rng.randint(1000, 9999)}{chr(rng.randint(65, 90))}" if adult else None,
                        "verified": True},
                "blood_group": person["blood_group"],
                "height_cm": person["height_cm"],
                "weight_kg": person["weight_kg"],
                "pre_existing_conditions": person["pre_existing_conditions"],
                "lifestyle": person["lifestyle"],
                "occupation": person.get("occupation", "Student" if age >= 5 else "Child"),
                "family_id": f"FAM{h:05d}",
                "registered_on": inception,
            }
            if relation == "Self":
                proposer_id = mid
                if group:
                    member["employer"] = {"name": pick(EMPLOYERS), "employee_id": f"E{rng.randint(10000, 99999)}"}
            members.append(member)
            insured.append({"member_id": mid, "name": person["name"], "relation": relation,
                            "gender": person["gender"], "dob": person["dob"],
                            "pre_existing_conditions": person["pre_existing_conditions"],
                            "covered_since": max(inception, person["dob"])})

        channel = "Employer" if group else pick(["Agent", "Online", "Bancassurance", "Broker"], [45, 30, 15, 10])
        agent = None
        if channel in ("Agent", "Bancassurance", "Broker"):
            kind_map = {"Agent": ("Individual Agent", "Corporate Agent"), "Bancassurance": ("Bancassurance",),
                        "Broker": ("Broker",)}
            options = [a for a in agents_by_insurer.get(insurer[0], []) if a["type"] in kind_map[channel]]
            if options:
                agent = pick(options)["agent_id"]
            else:
                channel = "Online"
        si_total = si + si * current["ncb_percent"] // 100
        policies.append({
            "policy_number": f"{insurer[2]}-HL-{inception.year}-{policy_seq:06d}",
            "plan_id": plan["plan_id"],
            "insurer_id": insurer[0],
            "tpa_id": pick(INSURER_TPAS[insurer[0]]) if INSURER_TPAS[insurer[0]] else None,
            "policy_type": ptype,
            "proposer_id": proposer_id,
            "insured_members": insured,
            "sum_insured": si,
            "cumulative_bonus": si_total - si,
            "total_cover": si_total,
            "utilized_amount": 0,
            "available_cover": si_total,
            "premium": {**current["premium"], "frequency": "Annual",
                        "paid_by": "Employer" if group else "Policyholder"},
            "first_inception_date": inception,
            "start_date": current["start_date"],
            "end_date": current["end_date"],
            "renewal_count": last_term,
            "renewal_history": renewal_history,
            "status": status,
            "cancelled_on": cancelled_on,
            "channel": channel,
            "agent_id": agent,
            "nominee": None if ptype == "Group" else {
                "name": next((p["name"] for p, r in people if r == "Spouse"), f"{pick(MALE + FEMALE)} {surname}"),
                "relation": "Spouse" if any(r == "Spouse" for _, r in people) else pick(["Father", "Mother", "Sibling"])},
            "employer": members[-len(people)].get("employer", {}).get("name") if group else None,
            "issued_on": inception - timedelta(days=rng.randint(1, 10)),
            "created_at": inception - timedelta(days=rng.randint(1, 10)),
            "updated_at": current["start_date"],
        })
    return members, policies


# --------------------------------------------------------------------------- #
# claims
# --------------------------------------------------------------------------- #
def month_starts(start, end):
    out, d = [], datetime(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = datetime(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return out


SEASON = {"monsoon": {7: 3.0, 8: 3.5, 9: 3.0, 10: 2.0, 6: 1.5},
          "winter": {11: 1.8, 12: 2.2, 1: 2.2, 2: 1.6},
          "summer": {4: 2.0, 5: 2.5, 6: 1.8}}


def build_bill(icd, hospital, los, icu_days, room_category, emergency, inflate):
    tier = hospital["city_tier"]
    tf = hospital["tariff_factor"]
    target = icd["avg_cost"] * tf * TIER_COST[tier] * math.exp(rng.gauss(0, 0.28)) * inflate
    room_rate = round_to(ROOM_TARIFF[room_category][tier] * min(tf, 1.4) * rng.uniform(0.9, 1.1), 100)
    icu_rate = round_to(ROOM_TARIFF["ICU"][tier] * min(tf, 1.4) * rng.uniform(0.9, 1.1), 100)
    room_days = max(los - icu_days, 1 if los else 0)
    room = room_rate * room_days
    icu = icu_rate * icu_days
    rest = max(target - room - icu, target * 0.35)
    if icd["is_surgical"]:
        shares = {"surgeon_ot_charges": .32, "implants": .18 if icd["uses_implant"] else 0, "medicines": .17,
                  "investigations": .11, "consumables": .09, "doctor_fees": .06, "nursing_charges": .05,
                  "admin_charges": .02}
    else:
        shares = {"medicines": .36, "investigations": .24, "doctor_fees": .14, "nursing_charges": .12,
                  "consumables": .10, "admin_charges": .02, "surgeon_ot_charges": 0, "implants": 0}
    shares = {k: v * rng.uniform(0.8, 1.2) for k, v in shares.items()}
    norm = sum(shares.values())
    bill = {"room_charges": room, "icu_charges": icu}
    for k, v in shares.items():
        bill[k] = round_to(rest * v / norm, 10)
    bill["ambulance"] = round_to(rng.uniform(1500, 6500), 100) if emergency else 0
    bill = {k: int(bill.get(k, 0)) for k in
            ["room_charges", "icu_charges", "nursing_charges", "doctor_fees", "surgeon_ot_charges",
             "investigations", "medicines", "consumables", "implants", "ambulance", "admin_charges"]}
    bill["total"] = sum(bill.values())
    return bill, room_rate, (icu_rate if icu_days else 0)


def build_claims(members, policies):
    member_by_id = {m["member_id"]: m for m in members}
    plan_by_id = {p["plan_id"]: p for p in PLANS}
    hospitals_by_city = {}
    for hsp in HOSPITALS:
        hospitals_by_city.setdefault(hsp["address"]["city"], []).append(hsp)
    doctors_by_hospital = {}
    for d in DOCTORS:
        doctors_by_hospital.setdefault(d["hospital_id"], []).append(d)
    users_by_role = {}
    for u in USERS:
        users_by_role.setdefault(u["role"], []).append(u)
    icd_list = ICD_CODES

    # Coverage windows: every insured member of every policy, from inception to end/cancellation
    windows = []
    for pol in policies:
        stop = pol["cancelled_on"] or pol["end_date"]
        for im in pol["insured_members"]:
            windows.append((pol, member_by_id[im["member_id"]], im["covered_since"], stop))

    months = month_starts(CLAIMS_FROM, TODAY - timedelta(days=4))
    month_w = []
    for i, m in enumerate(months):
        season = {7: 1.25, 8: 1.3, 9: 1.2, 12: 1.1, 1: 1.1}.get(m.month, 1.0)
        w = (1 + 0.04 * i) * season
        if (TODAY - m).days < 75:  # current quarter: book growth + monsoon surge
            w *= 1.5
        if m.month == TODAY.month and m.year == TODAY.year:
            w *= TODAY.day / 31
        month_w.append(w)

    def member_weight(m, adm):
        a = age_on(m["dob"], adm)
        w = 0.55 if a < 18 else 1.0 if a < 45 else 1.5 if a < 60 else 2.3
        if m["gender"] == "Female" and 22 <= a <= 38:
            w += 0.5
        return w * (1 + 0.25 * len(m["pre_existing_conditions"]))

    def choose_icd(m, adm, plan):
        a = age_on(m["dob"], adm)
        opts, ws = [], []
        for icd in icd_list:
            if icd["gender"] and icd["gender"] != m["gender"]:
                continue
            if not (icd["min_age"] <= a <= icd["max_age"]):
                continue
            w = icd["frequency_weight"]
            if icd["season"]:
                w *= SEASON[icd["season"]].get(adm.month, 0.7)
            if icd["ped_group"] and icd["ped_group"] in m["pre_existing_conditions"]:
                w *= 4
            if icd["is_maternity"] and not plan["features"]["maternity_cover"]:
                w *= 0.25
            if icd["chapter"] in ("Neoplasms", "Circulatory system") and a >= 55:
                w *= 1.8
            opts.append(icd)
            ws.append(w)
        return pick(opts, ws)

    def choose_hospital(m, icd, insurer_id, emergency):
        city = m["address"]["city"]
        state = m["address"]["state"]
        local = [x for x in hospitals_by_city.get(city, []) if icd["specialty"] in x["specialties"]]
        r = rng.random()
        if not emergency and (r < 0.05 or not local):
            pool = [x for x in HOSPITALS if icd["specialty"] in x["specialties"] and x["type"] == "Super Speciality"]
        elif not emergency and r < 0.15:
            pool = [x for x in HOSPITALS if x["address"]["state"] == state and icd["specialty"] in x["specialties"]]
        else:
            pool = local
        if not pool:
            pool = [x for x in HOSPITALS if icd["specialty"] in x["specialties"]]
        network = [x for x in pool if insurer_id in x["network"]["insurer_ids"]]
        if network and rng.random() < 0.78:
            pool = network
        return pick(pool)

    def reviewer_for(region, claimed):
        role = "Claims Executive" if claimed < 150000 else "Senior Claims Adjuster"
        local = [u for u in users_by_role[role] if u["region"] == region] or users_by_role[role]
        return pick(local)

    def decider_for(region, payable, current):
        if payable <= current["approval_limit"]:
            return current
        for role in ("Senior Claims Adjuster", "Claims Manager", "Admin"):
            if ROLES[role]["approval_limit"] >= payable:
                local = [u for u in users_by_role[role] if u["region"] == region] or users_by_role[role]
                return pick(local)
        return users_by_role["Admin"][0]

    claims, payments, history_by_member = [], [], {}
    # sum insured consumed per (policy, term) while generating
    consumed = {}
    term_of = {}

    def term_for(pol, d):
        for t in pol["renewal_history"]:
            if t["start_date"] <= d <= t["end_date"]:
                return t
        return pol["renewal_history"][-1]

    # pre-group coverage windows by month for fast sampling
    by_month = []
    for m in months:
        m_end = datetime(m.year + (m.month // 12), m.month % 12 + 1, 1)
        by_month.append([w for w in windows if w[2] < m_end and w[3] >= m])

    plan_counts = rng.choices(range(len(months)), weights=month_w, k=N_CLAIMS)
    raw = []
    for mi in plan_counts:
        m = months[mi]
        m_end = min(datetime(m.year + (m.month // 12), m.month % 12 + 1, 1), TODAY - timedelta(days=3))
        raw.append(rand_dt(m, m_end))
    raw.sort()

    seq = 0
    for adm_dt in raw:
        adm = adm_dt.replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59), second=0)
        mi = next(i for i, m in enumerate(months) if m.year == adm.year and m.month == adm.month)
        pool = [w for w in by_month[mi] if w[2] <= adm <= w[3]]
        fraud_kind = None
        r = rng.random()
        if r < 0.012:
            # claim after policy lapsed / was cancelled
            lapsed = [w for w in windows if w[2] <= w[3] < adm and (adm - w[3]).days < 120]
            if lapsed:
                pool = lapsed
        elif r < 0.05:
            fraud_kind = pick(["inflate", "inflate", "phantom", "early", "duplicate", "duplicate"])
            if fraud_kind == "early":
                early = [w for w in pool if 3 <= (adm - w[2]).days <= 40]
                pool = early or pool
        if not pool:
            continue
        pol, member, _, _ = pick(pool, [member_weight(w[1], adm) for w in pool])
        plan = plan_by_id[pol["plan_id"]]
        icd = choose_icd(member, adm, plan)
        emergency = icd["is_accident"] or icd["code"] in ("I21.9", "I63.9", "K35.80") or rng.random() < 0.15
        hospital = choose_hospital(member, icd, pol["insurer_id"], emergency)
        if fraud_kind == "phantom" or (fraud_kind == "inflate" and rng.random() < 0.5):
            watch = [x for x in HOSPITALS if x["watchlisted"] and icd["specialty"] in x["specialties"]]
            if watch:
                hospital = pick(watch)
        network = pol["insurer_id"] in hospital["network"]["insurer_ids"]
        claim_type = "Cashless" if network and rng.random() < (0.62 if emergency else 0.82) else "Reimbursement"

        if icd["day_care"]:
            los = 1
        else:
            los = max(1, int(round(rng.gauss(icd["avg_los"], icd["avg_los"] * 0.3))))
            if fraud_kind == "inflate" and rng.random() < 0.4:
                los = max(los, int(icd["avg_los"] * 3))
        icu_days = min(los, rng.randint(1, 4)) if icd["requires_icu"] and not icd["day_care"] else 0
        si = pol["sum_insured"]
        rc_opts = (["General Ward", "Twin Sharing"] if si <= 300000 else
                   ["Twin Sharing", "Single Private"] if si <= 500000 else ["Single Private", "Deluxe"])
        room_category = pick(rc_opts, [55, 45])
        if rng.random() < 0.25:
            idx = min(len(["General Ward", "Twin Sharing", "Single Private", "Deluxe"]) - 1,
                      ["General Ward", "Twin Sharing", "Single Private", "Deluxe"].index(room_category) + 1)
            room_category = ["General Ward", "Twin Sharing", "Single Private", "Deluxe"][idx]
        inflate = {"inflate": rng.uniform(3.0, 5.0), "phantom": rng.uniform(2.6, 3.6),
                   "early": rng.uniform(1.5, 2.6), "duplicate": rng.uniform(1.0, 1.6)}.get(fraud_kind, 1.0)
        if fraud_kind == "phantom":
            los = 1
            icu_days = 0
        bill, room_rate, icu_rate = build_bill(icd, hospital, los, icu_days, room_category, emergency, inflate)
        if fraud_kind in ("phantom", "inflate") and rng.random() < 0.45:
            target = int(math.ceil(bill["total"] / 10000.0) * 10000)
            bill["medicines"] += target - bill["total"]
            bill["total"] = target

        discharge = day(adm) + timedelta(days=los if not icd["day_care"] else 0,
                                         hours=rng.randint(11, 17), minutes=rng.randint(0, 59))
        if discharge <= adm:  # late-evening day-care admission: discharged next morning
            discharge = adm + timedelta(hours=rng.randint(6, 14))
        if fraud_kind == "duplicate" and history_by_member.get(member["member_id"]):
            prev = history_by_member[member["member_id"]][-1]
            adm = prev["admission_date"] + timedelta(days=rng.randint(0, 1), hours=3)
            discharge = prev["discharge_date"] + timedelta(hours=rng.randint(1, 30))
        if discharge >= TODAY - timedelta(hours=6):
            continue

        doctors = [d for d in doctors_by_hospital[hospital["hospital_id"]] if d["specialization"] == icd["specialty"]] \
            or doctors_by_hospital[hospital["hospital_id"]]
        doctor = pick(doctors)
        age = age_on(member["dob"], adm)
        relation = next(im["relation"] for im in pol["insured_members"] if im["member_id"] == member["member_id"])
        seq += 1
        claim_number = f"CLM-{adm.year}-{seq:06d}"

        diagnosis = [{"icd_code": icd["code"], "description": icd["description"], "type": "Primary"}]
        sec_map = {"Diabetes": ("E11.9", "Type 2 diabetes mellitus without complications"),
                   "Hypertension": ("I10", "Essential (primary) hypertension"),
                   "Thyroid": ("E03.9", "Hypothyroidism, unspecified"),
                   "Asthma": ("J45.909", "Asthma, uncomplicated")}
        for ped in member["pre_existing_conditions"]:
            if ped in sec_map and sec_map[ped][0] != icd["code"] and rng.random() < 0.6:
                diagnosis.append({"icd_code": sec_map[ped][0], "description": sec_map[ped][1], "type": "Secondary"})

        claim = {
            "claim_number": claim_number,
            "claim_type": claim_type,
            "admission_type": "Emergency" if emergency else "Planned",
            "status": None,
            "policy_number": pol["policy_number"],
            "member_id": member["member_id"],
            "hospital_id": hospital["hospital_id"],
            "doctor_id": doctor["doctor_id"],
            "insurer_id": pol["insurer_id"],
            "tpa_id": pol["tpa_id"],
            "plan_id": pol["plan_id"],
            "snapshot": {
                "member_name": member["name"], "member_gender": member["gender"], "member_age": age,
                "relation": relation, "hospital_name": hospital["name"], "hospital_city": hospital["address"]["city"],
                "hospital_state": hospital["address"]["state"], "hospital_tier": hospital["city_tier"],
                "network_hospital": network, "insurer_name": INS[pol["insurer_id"]][1],
                "plan_name": plan["name"], "doctor_name": doctor["name"],
            },
            "primary_icd": icd["code"],
            "diagnosis": diagnosis,
            "treatment": {
                "specialty": icd["specialty"],
                "line": "Day Care" if icd["day_care"] else ("Surgical" if icd["is_surgical"] else "Medical"),
                "procedure": icd["procedure"] or "Conservative medical management",
                "description": f"{icd['procedure'] or 'Medical management'} for {icd['description'].lower()}",
            },
            "admission_date": adm,
            "discharge_date": discharge,
            "length_of_stay": los,
            "icu_days": icu_days,
            "room_category": room_category,
            "room_rent_per_day": room_rate,
            "icu_rent_per_day": icu_rate,
            "bill": bill,
            "claimed_amount": bill["total"],
            "approved_amount": 0,
            "settled_amount": 0,
        }

        # ---- adjudication (uses sum insured consumed so far in that policy term)
        term = term_for(pol, adm)
        key = (pol["policy_number"], term["term"])
        cover_total = term["sum_insured"] + term["sum_insured"] * term["ncb_percent"] // 100
        available = cover_total - consumed.get(key, 0)
        strict = INS[pol["insurer_id"]][9]
        manual = None
        if fraud_kind and fraud_kind != "early" and rng.random() < 0.55:
            manual = "R09"
        elif claim_type == "Reimbursement" and rng.random() < 0.025 * strict:
            manual = "R08"
        elif not icd["is_surgical"] and icd["avg_cost"] < 50000 and rng.random() < 0.06 * strict:
            manual = "R10"
        policy_view = {**pol, "available_cover": available, "sum_insured": term["sum_insured"]}
        result = adjudicate(claim, policy_view, plan, icd, available_cover=available, manual_rejection=manual)

        # ---- timeline
        speed = INS[pol["insurer_id"]][8]
        region = REGION[hospital["address"]["state"]]
        events = []
        if claim_type == "Cashless":
            intimated = adm + timedelta(hours=rng.randint(1, 10)) if emergency else adm - timedelta(days=rng.randint(1, 4))
            submitted = discharge - timedelta(hours=rng.randint(2, 6))
            by, by_name = "HOSPITAL", f"{hospital['name']} (TPA Desk)"
        else:
            intimated = adm + timedelta(hours=rng.randint(2, 70))
            late = rng.random() < 0.06
            submitted = discharge + timedelta(days=rng.randint(32, 60) if late else rng.randint(2, 22),
                                              hours=rng.randint(0, 8))
            by, by_name = "MEMBER", member["name"]
        intimated = max(intimated, pol["first_inception_date"])
        events.append((intimated, INTIMATED, by, by_name,
                       "Pre-authorisation request received" if claim_type == "Cashless" else "Claim intimated by insured"))
        reviewer = reviewer_for(region, bill["total"])
        docs_pending = claim_type == "Reimbursement" and rng.random() < 0.3
        t = submitted
        if docs_pending:
            events.append((t, DOCS_PENDING, reviewer["user_id"], reviewer["name"],
                           "Awaiting " + pick(["discharge summary", "original pharmacy bills", "investigation reports",
                                               "cancelled cheque"])))
            t = t + timedelta(days=rng.randint(3, 9), hours=rng.randint(1, 8))
        queue = timedelta(hours=rng.randint(1, 20)) if claim_type == "Cashless" else timedelta(days=rng.uniform(1, 5) * speed)
        events.append((t + queue, UNDER_REVIEW, reviewer["user_id"], reviewer["name"],
                       "Claim assigned for assessment"))
        t = events[-1][0]
        queries = []
        if rng.random() < (0.12 if claim_type == "Cashless" else 0.24) * strict:
            q_at = t + timedelta(days=rng.uniform(0.5, 4) * speed)
            q = pick(QUERY_QUESTIONS)
            events.append((q_at, QUERY_RAISED, reviewer["user_id"], reviewer["name"], q))
            r_at = q_at + timedelta(days=rng.uniform(2, 9))
            queries.append({"question": q, "raised_by": reviewer["user_id"], "raised_at": q_at,
                            "response": pick(QUERY_RESPONSES), "responded_at": r_at})
            events.append((r_at, UNDER_REVIEW, "MEMBER", member["name"], "Query response submitted"))
            t = r_at
        payable = result["payable_amount"]
        decider = reviewer
        if result["eligible"]:
            decider = decider_for(region, payable, reviewer)
            if decider is not reviewer:
                e_at = t + timedelta(hours=rng.randint(4, 40))
                events.append((e_at, ESCALATED, reviewer["user_id"], reviewer["name"],
                               f"Payable amount exceeds approval limit; escalated to {decider['name']}"))
                t = e_at
        if claim_type == "Cashless":
            decision_gap = timedelta(hours=rng.uniform(3, 30) * speed)
        else:
            decision_gap = timedelta(days=rng.uniform(3, 16) * speed)
        decided = t + decision_gap
        decided = office_time(decided)
        if decided < t:
            decided += timedelta(days=1)
        if result["eligible"]:
            remark = f"Approved for ₹{payable:,}" + (
                f" after deductions of ₹{result['total_deductions']:,}" if result["total_deductions"] else "")
        else:
            remark = result["rejection_reason"]
        events.append((decided, result["decision"], decider["user_id"], decider["name"], remark))
        settled = None
        if result["eligible"]:
            settled = decided + timedelta(days=rng.uniform(5, 20) * speed if claim_type == "Cashless"
                                          else rng.uniform(1, 6) * speed)
            settled = office_time(settled)
            if settled < decided:
                settled += timedelta(days=1)
            events.append((settled, SETTLED, "USR001" if rng.random() < 0.1 else decider["user_id"],
                           "Finance Desk", "Payment released via NEFT/RTGS"))
        # 2% of claims are withdrawn before a decision
        if rng.random() < 0.02:
            w_at = events[0][0] + timedelta(days=rng.randint(1, 6))
            events = [e for e in events if e[0] < w_at][:2] + [(w_at, WITHDRAWN, "MEMBER", member["name"],
                                                               "Claim withdrawn by insured")]

        # A timeline can never go backwards: nudge any out-of-order event forward
        ordered = [events[0]]
        for e in events[1:]:
            if e[0] <= ordered[-1][0]:
                e = (ordered[-1][0] + timedelta(minutes=rng.randint(20, 240)),) + e[1:]
            ordered.append(e)
        events = ordered
        submitted = max(submitted, events[0][0])
        if settled:
            settled = events[-1][0] if events[-1][1] == SETTLED else settled
        decided = next((e[0] for e in events if e[1] == result["decision"]), decided)

        visible = [e for e in events if e[0] <= TODAY]
        if not visible:
            continue
        status = visible[-1][1]
        claim["status"] = status
        claim["intimation_date"] = visible[0][0]
        claim["submitted_at"] = submitted if submitted <= TODAY else None
        claim["status_history"] = [
            {"status": s, "at": at, "by": b, "by_name": bn, "remarks": rm} for at, s, b, bn, rm in visible]
        claim["assigned_to"] = (decider if status == ESCALATED else reviewer)["user_id"] \
            if status in (DOCS_PENDING, UNDER_REVIEW, QUERY_RAISED, ESCALATED) else None
        claim["queries"] = [q for q in queries if q["raised_at"] <= TODAY]
        for q in claim["queries"]:
            if q["responded_at"] > TODAY:
                q["response"], q["responded_at"] = None, None

        # documents
        docs = []
        for i, dt_name in enumerate(DOCUMENT_TYPES[claim_type]):
            # ID / pre-auth papers come at intimation; discharge papers only after discharge
            base = intimated if i < 3 else max(submitted, discharge)
            uploaded = base + timedelta(hours=rng.randint(1, 20))
            st = "Verified" if status not in (INTIMATED, DOCS_PENDING) else "Received"
            if status == DOCS_PENDING and i == len(DOCUMENT_TYPES[claim_type]) - 2:
                st, uploaded = "Pending", None
            if status == INTIMATED and uploaded and uploaded > TODAY:
                st, uploaded = "Pending", None
            docs.append({"type": dt_name, "status": st,
                         "file_name": f"{claim_number}_{dt_name.split()[0].lower()}.pdf" if uploaded else None,
                         "uploaded_at": min(uploaded, TODAY) if uploaded else None})
        claim["documents"] = docs

        if claim_type == "Cashless":
            req = round_to(bill["total"] * rng.uniform(0.75, 1.05), 1000)
            claim["pre_auth"] = {
                "requested_amount": req, "requested_at": intimated,
                "status": "Approved" if result["eligible"] else "Denied",
                "approved_amount": round_to(min(req, payable) * rng.uniform(0.6, 0.9), 1000) if result["eligible"] else 0,
                "decided_at": intimated + timedelta(hours=rng.uniform(1, 5) * speed),
                "remarks": "Initial approval; final approval on discharge" if result["eligible"] else result["rejection_reason"],
            }
        else:
            claim["pre_auth"] = None

        decided_visible = status in (APPROVED, PARTIALLY_APPROVED, REJECTED, SETTLED)
        if decided_visible:
            claim["adjudication"] = {k: result[k] for k in (
                "decision", "eligible", "rejection_code", "rejection_reason", "claimed_amount", "deductions",
                "total_deductions", "copay_amount", "payable_amount", "member_payable", "eligible_room_rent")}
            claim["adjudication"]["adjudicated_by"] = decider["user_id"]
            claim["adjudication"]["adjudicated_at"] = decided
            claim["approved_amount"] = payable
            claim["decided_at"] = decided
            if result["eligible"]:
                consumed[key] = consumed.get(key, 0) + payable
        else:
            claim["decided_at"] = None
        claim["settled_at"] = None
        claim["payment"] = None
        if status == SETTLED:
            claim["settled_at"] = settled
            claim["settled_amount"] = payable
            bank, ifsc = pick(BANKS)
            pay_id = f"PAY-{settled.year}-{len(payments) + 1:06d}"
            utr = f"{ifsc}{settled:%y%j}{rng.randint(10000000, 99999999)}"
            payee_type = "Hospital" if claim_type == "Cashless" else "Member"
            payment = {
                "payment_id": pay_id, "claim_number": claim_number, "policy_number": pol["policy_number"],
                "insurer_id": pol["insurer_id"], "amount": payable,
                "payee": {"type": payee_type,
                          "name": hospital["name"] if payee_type == "Hospital" else member["name"],
                          "bank": bank, "account_masked": f"XXXXXX{rng.randint(1000, 9999)}",
                          "ifsc": f"{ifsc}0{rng.randint(100000, 999999)}"},
                "mode": "RTGS" if payable >= 200000 else "NEFT",
                "utr": utr, "status": "Success",
                "initiated_by": claim["status_history"][-1]["by"],
                "initiated_at": settled - timedelta(hours=rng.randint(2, 20)),
                "paid_at": settled,
            }
            payments.append(payment)
            claim["payment"] = {"payment_id": pay_id, "utr": utr, "mode": payment["mode"], "paid_at": settled,
                                "payee_type": payee_type}
        # Turnaround: submission -> decision, and submission -> payment
        claim["tat_days"] = round((claim["decided_at"] - claim["submitted_at"]).total_seconds() / 86400, 1) \
            if claim["decided_at"] and claim["submitted_at"] else None
        claim["settlement_tat_days"] = round((claim["settled_at"] - claim["submitted_at"]).total_seconds() / 86400, 1) \
            if claim["settled_at"] and claim["submitted_at"] else None
        claim["created_at"] = claim["intimation_date"]
        claim["updated_at"] = claim["status_history"][-1]["at"]
        claim["_fraud_kind"] = fraud_kind
        claim["_inception"] = pol["first_inception_date"]
        claims.append(claim)
        history_by_member.setdefault(member["member_id"], []).append(claim)
        term_of[claim_number] = key

    # ---- fraud scoring (needs complete member histories)
    icd_by = {i["code"]: i for i in ICD_CODES}
    watch = {h["hospital_id"] for h in HOSPITALS if h["watchlisted"]}
    for c in claims:
        others = [{"claim_number": o["claim_number"], "admission_date": o["admission_date"],
                   "discharge_date": o["discharge_date"]}
                  for o in history_by_member[c["member_id"]] if o is not c and o["status"] != WITHDRAWN]
        res = score_claim(c, icd=icd_by[c["primary_icd"]], member_state=member_by_id[c["member_id"]]["address"]["state"],
                          inception=c.pop("_inception"), history=others,
                          hospital_watchlisted=c["hospital_id"] in watch)
        c.pop("_fraud_kind")
        review = "Not Required" if res["risk_level"] == "Low" else "Not Reviewed"
        if res["risk_level"] != "Low" and c["status"] in (REJECTED, SETTLED):
            if c.get("adjudication", {}).get("rejection_code") == "R09":
                review = "Confirmed Fraud"
            else:
                review = "Cleared"
        elif res["risk_level"] == "High":
            review = "Under Investigation"
        res.update(scanned_at=c["updated_at"], review_status=review)
        if review in ("Confirmed Fraud", "Cleared"):
            res["reviewed_by"] = pick(["USR021", "USR022"])
            res["reviewed_at"] = c["updated_at"]
        c["fraud"] = res

    # ---- policy utilisation for the current term
    for pol in policies:
        cur = pol["renewal_history"][-1]["term"]
        used = sum(c["settled_amount"] for c in history_by_member_iter(history_by_member, pol)
                   if term_of[c["claim_number"]] == (pol["policy_number"], cur) and c["status"] == SETTLED)
        pol["utilized_amount"] = used
        pol["available_cover"] = max(pol["total_cover"] - used, 0)
    return claims, payments


def history_by_member_iter(history, pol):
    for im in pol["insured_members"]:
        yield from history.get(im["member_id"], [])


# --------------------------------------------------------------------------- #
# grievances, audit logs, counters
# --------------------------------------------------------------------------- #
def build_grievances(claims):
    docs, seq = [], 0
    for c in claims:
        cat = None
        if c["status"] == REJECTED and rng.random() < 0.28:
            cat = "Cashless Denial" if c["claim_type"] == "Cashless" and rng.random() < 0.4 else "Claim Rejection"
        elif c["status"] == SETTLED and c.get("adjudication", {}).get("member_payable", 0) > 40000 and rng.random() < 0.12:
            cat = "Short Settlement"
        elif (c.get("settlement_tat_days") or 0) > 25 and rng.random() < 0.2:
            cat = "Delay in Settlement"
        elif c["status"] in (DOCS_PENDING, QUERY_RAISED) and rng.random() < 0.08:
            cat = "Service Issue"
        if not cat:
            continue
        seq += 1
        raised = c["updated_at"] + timedelta(days=rng.randint(1, 20), hours=rng.randint(0, 9))
        if raised > TODAY:
            raised = TODAY - timedelta(hours=rng.randint(2, 48))
        age_days = (TODAY - raised).days
        if age_days > 45:
            status = pick(["Resolved", "Escalated to Ombudsman"], [88, 12])
        elif age_days > 14:
            status = pick(["Resolved", "In Progress"], [70, 30])
        else:
            status = pick(["Open", "In Progress", "Resolved"], [50, 30, 20])
        resolved = raised + timedelta(days=rng.randint(3, min(30, max(age_days, 3)))) if status == "Resolved" else None
        if resolved and resolved > TODAY:
            resolved = TODAY - timedelta(hours=3)
        resolution = None
        if status == "Resolved":
            resolution = pick({
                "Claim Rejection": ["Rejection upheld as per policy terms", "Claim re-opened and reconsidered"],
                "Cashless Denial": ["Rejection upheld as per policy terms", "Advised to file reimbursement claim"],
                "Short Settlement": ["Deductions explained to the insured", "Additional amount paid"],
                "Delay in Settlement": ["Claim settled with interest for delay", "Delay explained; payment released"],
                "Service Issue": ["Documents re-verified and query closed", "Apology issued; process expedited"],
            }[cat])
        docs.append({
            "grievance_id": f"GRV-{raised.year}-{seq:05d}",
            "claim_number": c["claim_number"], "member_id": c["member_id"],
            "policy_number": c["policy_number"], "insurer_id": c["insurer_id"],
            "category": cat, "channel": pick(["Bima Bharosa Portal", "Email", "Call Centre", "Branch Walk-in"], [35, 30, 25, 10]),
            "description": {
                "Claim Rejection": "Insured disputes the rejection of the claim.",
                "Cashless Denial": "Cashless request denied at the hospital; insured had to pay upfront.",
                "Short Settlement": "Insured disputes deductions made in the settlement.",
                "Delay in Settlement": "Claim not settled within the promised timeline.",
                "Service Issue": "Repeated document requests and poor communication.",
            }[cat],
            "status": status, "priority": pick(["Low", "Medium", "High"], [30, 50, 20]),
            "raised_at": raised, "resolved_at": resolved, "resolution": resolution,
            "assigned_to": pick(["USR002", "USR003", "USR004", "USR005"]),
        })
    return docs


def build_audit_logs(claims, policies, users):
    names = {u["user_id"]: (u["name"], u["role"]) for u in users}
    since = TODAY - timedelta(days=150)
    logs = []
    for c in claims:
        prev = None
        for h in c["status_history"]:
            if h["at"] >= since:
                actor = names.get(h["by"], (h["by_name"], "Hospital" if h["by"] == "HOSPITAL" else "Insured Member"))
                action = ("CLAIM_INTIMATED" if h["status"] == INTIMATED else
                          "CLAIM_DECISION" if h["status"] in (APPROVED, PARTIALLY_APPROVED, REJECTED) else
                          "CLAIM_SETTLED" if h["status"] == SETTLED else "CLAIM_STATUS_CHANGED")
                logs.append({
                    "timestamp": h["at"], "actor_id": h["by"], "actor_name": actor[0], "actor_role": actor[1],
                    "action": action, "entity_type": "claim", "entity_id": c["claim_number"],
                    "summary": f"{c['claim_number']}: {prev or 'New'} → {h['status']}",
                    "details": {"from": prev, "to": h["status"], "remarks": h["remarks"]},
                })
            prev = h["status"]
    for p in policies:
        if p["start_date"] >= since:
            renewal = p["renewal_count"] > 0
            logs.append({
                "timestamp": p["start_date"], "actor_id": "SYSTEM", "actor_name": "Policy Admin System",
                "actor_role": "System", "action": "POLICY_RENEWED" if renewal else "POLICY_ISSUED",
                "entity_type": "policy", "entity_id": p["policy_number"],
                "summary": f"{p['policy_number']} {'renewed' if renewal else 'issued'} - sum insured ₹{p['sum_insured']:,}",
                "details": {"premium": p["premium"]["total"], "channel": p["channel"]},
            })
    logs.sort(key=lambda x: x["timestamp"])
    return logs


def write(name, docs):
    path = os.path.join(DATA_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("[\n")
        f.write(",\n".join(json_util.dumps(d, json_options=RELAXED_JSON_OPTIONS, ensure_ascii=False) for d in docs))
        f.write("\n]\n")
    size = os.path.getsize(path) / 1024
    print(f"  {name:14s} {len(docs):6,d} documents  {size:8.0f} KB")


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print("Generating ClaimSure dataset ...")
    INSURER_DOCS = build_insurers()
    TPA_DOCS = build_tpas()
    PLANS = build_plans()
    ICD_CODES = build_icd_codes()
    HOSPITALS = build_hospitals()
    DOCTORS = build_doctors(HOSPITALS)
    USERS = build_users()
    AGENTS = build_agents()
    MEMBERS, POLICIES = build_households()
    CLAIMS, PAYMENTS = build_claims(MEMBERS, POLICIES)
    GRIEVANCES = build_grievances(CLAIMS)
    AUDIT = build_audit_logs(CLAIMS, POLICIES, USERS)

    datasets = {
        "insurers": INSURER_DOCS, "tpas": TPA_DOCS, "plans": PLANS, "icd_codes": ICD_CODES,
        "hospitals": HOSPITALS, "doctors": DOCTORS, "users": USERS, "agents": AGENTS,
        "members": MEMBERS, "policies": POLICIES, "claims": CLAIMS, "payments": PAYMENTS,
        "grievances": GRIEVANCES, "audit_logs": AUDIT,
    }
    for name, docs in datasets.items():
        write(name, docs)
    print(f"  {'TOTAL':14s} {sum(len(d) for d in datasets.values()):6,d} documents")

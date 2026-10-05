"""Domain constants shared by the data generator, the services and the web app."""

# --------------------------------------------------------------------------- #
# Claim lifecycle
# --------------------------------------------------------------------------- #
INTIMATED = "Intimated"
DOCS_PENDING = "Documents Pending"
UNDER_REVIEW = "Under Review"
QUERY_RAISED = "Query Raised"
ESCALATED = "Escalated"
APPROVED = "Approved"
PARTIALLY_APPROVED = "Partially Approved"
REJECTED = "Rejected"
SETTLED = "Settled"
WITHDRAWN = "Withdrawn"

CLAIM_STATUSES = [
    INTIMATED, DOCS_PENDING, UNDER_REVIEW, QUERY_RAISED, ESCALATED,
    APPROVED, PARTIALLY_APPROVED, REJECTED, SETTLED, WITHDRAWN,
]
OPEN_STATUSES = [INTIMATED, DOCS_PENDING, UNDER_REVIEW, QUERY_RAISED, ESCALATED]
AWAITING_PAYMENT = [APPROVED, PARTIALLY_APPROVED]
CLOSED_STATUSES = [REJECTED, SETTLED, WITHDRAWN]

# Allowed manual transitions. Decisions (Approved / Partially Approved / Rejected)
# go through the adjudication service and Settled goes through the settlement
# transaction, but they are listed here so the state machine is complete.
TRANSITIONS = {
    INTIMATED: [DOCS_PENDING, UNDER_REVIEW, WITHDRAWN],
    DOCS_PENDING: [UNDER_REVIEW, WITHDRAWN],
    UNDER_REVIEW: [QUERY_RAISED, ESCALATED, APPROVED, PARTIALLY_APPROVED, REJECTED],
    QUERY_RAISED: [UNDER_REVIEW, WITHDRAWN],
    ESCALATED: [APPROVED, PARTIALLY_APPROVED, REJECTED],
    APPROVED: [SETTLED],
    PARTIALLY_APPROVED: [SETTLED],
    REJECTED: [UNDER_REVIEW],  # re-opened after a grievance / appeal
    SETTLED: [],
    WITHDRAWN: [],
}
DECISION_STATUSES = [APPROVED, PARTIALLY_APPROVED, REJECTED]

STATUS_COLORS = {
    INTIMATED: "slate", DOCS_PENDING: "amber", UNDER_REVIEW: "blue",
    QUERY_RAISED: "orange", ESCALATED: "purple", APPROVED: "teal",
    PARTIALLY_APPROVED: "cyan", REJECTED: "red", SETTLED: "green", WITHDRAWN: "gray",
}

CLAIM_TYPES = ["Cashless", "Reimbursement"]
ADMISSION_TYPES = ["Planned", "Emergency"]

# Daily room tariff (INR) by room category and city tier (1, 2, 3)
ROOM_CATEGORIES = ["General Ward", "Twin Sharing", "Single Private", "Deluxe", "ICU"]
ROOM_TARIFF = {
    "General Ward":   {1: 2500,  2: 1800,  3: 1200},
    "Twin Sharing":   {1: 4500,  2: 3200,  3: 2200},
    "Single Private": {1: 7500,  2: 5500,  3: 3800},
    "Deluxe":         {1: 12000, 2: 8500,  3: 6000},
    "ICU":            {1: 18000, 2: 13000, 3: 9000},
}

BILL_HEADS = [
    ("room_charges", "Room Rent"),
    ("icu_charges", "ICU Charges"),
    ("nursing_charges", "Nursing Charges"),
    ("doctor_fees", "Doctor / Consultation Fees"),
    ("surgeon_ot_charges", "Surgeon & OT Charges"),
    ("investigations", "Investigations & Diagnostics"),
    ("medicines", "Medicines & Drugs"),
    ("consumables", "Consumables"),
    ("implants", "Implants & Stents"),
    ("ambulance", "Ambulance"),
    ("admin_charges", "Registration & Admin Charges"),
]
BILL_KEYS = [k for k, _ in BILL_HEADS]
# Bill heads that are scaled down with room rent when a member stays in a
# room above their eligibility (IRDAI "proportionate deduction").
PROPORTIONATE_HEADS = ["nursing_charges", "doctor_fees", "surgeon_ot_charges", "investigations"]

DOCUMENT_TYPES = {
    "Cashless": ["Pre-Authorisation Form", "Photo ID Proof", "Health Card",
                 "Discharge Summary", "Final Hospital Bill", "Investigation Reports"],
    "Reimbursement": ["Claim Form (Part A)", "Claim Form (Part B)", "Photo ID Proof",
                      "Discharge Summary", "Final Hospital Bill", "Pharmacy Bills",
                      "Investigation Reports", "Cancelled Cheque / Bank Details"],
}

# --------------------------------------------------------------------------- #
# Adjudication
# --------------------------------------------------------------------------- #
REJECTION_REASONS = {
    "R01": "Policy not in force on date of admission",
    "R02": "Patient not covered under the policy",
    "R03": "Initial 30-day waiting period",
    "R04": "Maternity not covered / maternity waiting period",
    "R05": "Specific illness waiting period not completed",
    "R06": "Pre-existing disease waiting period not completed",
    "R07": "Treatment excluded under policy terms",
    "R08": "Mandatory documents not submitted within timeline",
    "R09": "Misrepresentation / suspected fraudulent claim",
    "R10": "Hospitalisation not medically necessary (OPD treatable)",
    "R11": "Sum insured exhausted",
}
MANUAL_REJECTION_CODES = ["R08", "R09", "R10"]

DEDUCTION_CATEGORIES = [
    "Non-Payable Items", "Room Rent Excess", "Proportionate Deduction",
    "Ambulance Limit", "Sub-Limit", "Co-Payment", "Sum Insured Limit",
]

# --------------------------------------------------------------------------- #
# Policies & people
# --------------------------------------------------------------------------- #
POLICY_STATUSES = ["Active", "Expired", "Lapsed", "Cancelled"]
POLICY_TYPES = ["Individual", "Family Floater", "Senior Citizen", "Group"]
SALES_CHANNELS = ["Agent", "Online", "Bancassurance", "Broker", "Employer"]
RELATIONS = ["Self", "Spouse", "Son", "Daughter", "Father", "Mother"]
PED_CONDITIONS = ["Diabetes", "Hypertension", "Thyroid", "Asthma",
                  "Heart Disease", "Arthritis", "Kidney Disease"]

# Staff roles and the claim amount each role may approve on its own.
ROLES = {
    "Admin":                  {"approval_limit": 10_000_000, "can_decide": True},
    "Claims Manager":         {"approval_limit": 1_000_000, "can_decide": True},
    "Senior Claims Adjuster": {"approval_limit": 300_000, "can_decide": True},
    "Medical Officer":        {"approval_limit": 200_000, "can_decide": True},
    "Claims Executive":       {"approval_limit": 75_000, "can_decide": True},
    "Fraud Analyst":          {"approval_limit": 0, "can_decide": False},
}

GRIEVANCE_CATEGORIES = ["Claim Rejection", "Short Settlement", "Delay in Settlement",
                        "Cashless Denial", "Service Issue"]
GRIEVANCE_STATUSES = ["Open", "In Progress", "Resolved", "Escalated to Ombudsman"]
GRIEVANCE_CHANNELS = ["Bima Bharosa Portal", "Email", "Call Centre", "Branch Walk-in"]

# --------------------------------------------------------------------------- #
# Fraud
# --------------------------------------------------------------------------- #
FRAUD_LEVELS = [(60, "High"), (30, "Medium"), (0, "Low")]

STATE_REGION = {
    "Delhi": "North", "Haryana": "North", "Uttar Pradesh": "North", "Rajasthan": "North",
    "Chandigarh": "North", "Bihar": "East", "West Bengal": "East", "Odisha": "East", "Assam": "East",
    "Maharashtra": "West", "Gujarat": "West", "Madhya Pradesh": "West",
    "Karnataka": "South", "Tamil Nadu": "South", "Telangana": "South", "Kerala": "South",
    "Andhra Pradesh": "South",
}

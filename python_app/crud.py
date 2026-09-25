from datetime import datetime

# --- CREATE ---
def register_patient(db, patient_data):
    result = db.patients.insert_one(patient_data)
    print(f"Registered new patient with _id: {result.inserted_id}")
    return result

def create_policy(db, policy_data):
    result = db.policies.insert_one(policy_data)
    print(f"Created new policy with _id: {result.inserted_id}")
    return result

def submit_claim(db, claim_data):
    claim_data['status'] = 'Submitted'
    claim_data['status_history'] = [{
        'status': 'Submitted',
        'date': datetime.now().isoformat(),
        'updated_by': 'System',
        'notes': 'Initial claim submission'
    }]
    result = db.claims.insert_one(claim_data)
    print(f"Submitted new claim with _id: {result.inserted_id}")
    return result

# --- READ ---
def get_all_claims(db, limit=10):
    claims = list(db.claims.find().limit(limit))
    print(f"Retrieved {len(claims)} claims.")
    return claims

def get_claims_by_status(db, status):
    claims = list(db.claims.find({'status': status}))
    print(f"Retrieved {len(claims)} claims with status: {status}")
    return claims

def get_claims_by_patient(db, patient_id):
    claims = list(db.claims.find({'patient_id': patient_id}))
    print(f"Retrieved {len(claims)} claims for patient: {patient_id}")
    return claims

def get_claim_details(db, claim_id):
    pipeline = [
        {'$match': {'claim_id': claim_id}},
        {'$lookup': {
            'from': 'patients',
            'localField': 'patient_id',
            'foreignField': 'patient_id',
            'as': 'patient_info'
        }},
        {'$lookup': {
            'from': 'hospitals',
            'localField': 'hospital_id',
            'foreignField': 'hospital_id',
            'as': 'hospital_info'
        }}
    ]
    try:
        results = list(db.claims.aggregate(pipeline))
        if results:
            print(f"Retrieved full details for claim: {claim_id}")
            return results[0]
        else:
            print(f"Claim {claim_id} not found.")
            return None
    except Exception as e:
        print(f"Lookup failed (mongomock limitation?): {e}")
        return db.claims.find_one({'claim_id': claim_id})

def search_claims_by_diagnosis(db, icd_code):
    claims = list(db.claims.find({'diagnosis_codes': icd_code}))
    print(f"Retrieved {len(claims)} claims for diagnosis: {icd_code}")
    return claims

def get_high_value_claims(db, threshold=100000):
    claims = list(db.claims.find({'billed_amount': {'$gt': threshold}}))
    print(f"Retrieved {len(claims)} high-value claims (>{threshold})")
    return claims

# --- UPDATE ---
def update_claim_status(db, claim_id, new_status, notes, updated_by):
    history_entry = {
        'status': new_status,
        'date': datetime.now().isoformat(),
        'updated_by': updated_by,
        'notes': notes
    }
    result = db.claims.update_one(
        {'claim_id': claim_id},
        {
            '$set': {'status': new_status},
            '$push': {'status_history': history_entry}
        }
    )
    if result.modified_count > 0:
        print(f"Updated status of claim {claim_id} to {new_status}")
    else:
        print(f"Claim {claim_id} not found or status already {new_status}")
    return result

def update_approved_amount(db, claim_id, amount):
    result = db.claims.update_one(
        {'claim_id': claim_id},
        {'$set': {'approved_amount': amount}}
    )
    if result.modified_count > 0:
        print(f"Updated approved amount for claim {claim_id} to {amount}")
    else:
        print(f"Claim {claim_id} not found.")
    return result

# --- DELETE ---
def cancel_claim(db, claim_id, reason):
    # Soft delete
    return update_claim_status(db, claim_id, 'Cancelled', reason, 'System')

def hard_delete_claim(db, claim_id):
    result = db.claims.delete_one({'claim_id': claim_id})
    if result.deleted_count > 0:
        print(f"Hard deleted claim {claim_id}")
    else:
        print(f"Claim {claim_id} not found.")
    return result

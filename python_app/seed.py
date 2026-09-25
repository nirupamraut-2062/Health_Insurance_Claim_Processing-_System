import json
import os
from db_config import get_database

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')

COLLECTIONS = [
    ('patients', 'patients.json'),
    ('insurers', 'insurers.json'),
    ('policies', 'policies.json'),
    ('hospitals', 'hospitals.json'),
    ('doctors', 'doctors.json'),
    ('claims', 'claims.json'),
]

def load_json(filename):
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        print(f"File {filepath} not found.")
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def seed_database(db=None):
    if db is None:
        db = get_database()
    
    total = 0
    print('\n' + '='*50)
    print('  SEEDING DATABASE: health_insurance_db')
    print('='*50)
    
    for collection_name, filename in COLLECTIONS:
        # Drop existing data
        db[collection_name].drop()
        
        # Load and insert
        data = load_json(filename)
        if data:
            db[collection_name].insert_many(data)
        
        count = db[collection_name].count_documents({})
        total += count
        print(f'  ✅ {collection_name:12s} : {count:3d} records loaded')
    
    print('-'*50)
    print(f'  📊 TOTAL RECORDS : {total}')
    print('='*50)
    return total

if __name__ == '__main__':
    seed_database()

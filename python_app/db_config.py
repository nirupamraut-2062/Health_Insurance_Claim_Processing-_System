import os
import sys

DB_NAME = 'health_insurance_db'
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')

# Set to False when using real MongoDB (local or Atlas)
USE_MOCK = True

def get_client():
    if USE_MOCK:
        try:
            import mongomock
            return mongomock.MongoClient()
        except ImportError:
            print('mongomock not installed. Install with: pip install mongomock')
            sys.exit(1)
    else:
        from pymongo import MongoClient
        return MongoClient(MONGO_URI)

def get_database():
    client = get_client()
    return client[DB_NAME]

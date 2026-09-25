import os
import sys

DB_NAME = 'health_insurance_db'
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')

# Set to False when using real MongoDB (local or Atlas)
USE_MOCK = True

_client = None

def get_client():
    global _client
    if _client is not None:
        return _client

    if USE_MOCK:
        try:
            import mongomock
            _client = mongomock.MongoClient()
            return _client
        except ImportError:
            print('mongomock not installed. Install with: pip install mongomock')
            sys.exit(1)
    else:
        from pymongo import MongoClient
        _client = MongoClient(MONGO_URI)
        return _client

def get_database():
    client = get_client()
    return client[DB_NAME]

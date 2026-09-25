import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python_app'))

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn
from db_config import get_database
from seed import seed_database

app = FastAPI(title='Health Insurance Claim Processing System')
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), 'templates'))

db = get_database()
if db.claims.count_documents({}) == 0:
    seed_database(db)

@app.get('/', response_class=HTMLResponse)
async def dashboard(request: Request):
    # Get summary stats
    total_claims = db.claims.count_documents({})
    total_patients = db.patients.count_documents({})
    total_policies = db.policies.count_documents({})
    total_hospitals = db.hospitals.count_documents({})
    
    # Status counts
    status_counts = {}
    for status in ['Submitted', 'Under Review', 'Approved', 'Rejected', 'Settled']:
        status_counts[status] = db.claims.count_documents({'status': status})

    # Type counts
    type_counts = {}
    for type_ in ['Inpatient', 'Outpatient', 'Maternity', 'Dental', 'Emergency']:
         type_counts[type_] = db.claims.count_documents({'claim_type': type_})
    
    # Recent claims
    recent_claims = list(db.claims.find({}, {'_id': 0}).sort('claim_date', -1).limit(10))
    
    return templates.TemplateResponse('index.html', {
        'request': request,
        'total_claims': total_claims,
        'total_patients': total_patients,
        'total_policies': total_policies,
        'total_hospitals': total_hospitals,
        'status_counts': status_counts,
        'type_counts': type_counts,
        'recent_claims': recent_claims
    })

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)

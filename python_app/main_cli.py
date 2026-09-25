import os
import sys
import io

# Fix Windows console encoding for Unicode characters
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from colorama import init, Fore, Style
from db_config import get_database, get_client, USE_MOCK
from seed import seed_database
from crud import *
from analytics import run_all_analytics
from indexing_benchmark import run_indexing_benchmark  
from transactions import run_transaction_demo
import json

init(autoreset=True)  # colorama

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def pause():
    input(f'\n{Fore.CYAN}Press Enter to continue...{Style.RESET_ALL}')

def print_header():
    print(f"{Fore.BLUE}{Style.BRIGHT}" + "="*60)
    print(f"{Fore.GREEN}{Style.BRIGHT}    [HEALTH] Health Insurance Claim Processing System")
    print(f"{Fore.BLUE}{Style.BRIGHT}" + "="*60)
    if USE_MOCK:
        print(f"{Fore.YELLOW}    [Running in MOCK mode (mongomock)]{Style.RESET_ALL}")
    else:
        print(f"{Fore.GREEN}    [Running in REAL MongoDB mode]{Style.RESET_ALL}")
    print()

def db_summary(db):
    print(f"\n{Fore.CYAN}--- Database Summary ---{Style.RESET_ALL}")
    collections = ['patients', 'insurers', 'policies', 'hospitals', 'doctors', 'claims']
    total = 0
    for coll in collections:
        count = db[coll].count_documents({})
        total += count
        print(f"{coll.capitalize():12}: {count} records")
    print(f"{Fore.YELLOW}Total Records: {total}{Style.RESET_ALL}")

def get_input_dict(prompt):
    print(f"Enter {prompt} data as JSON (or press enter to skip):")
    data_str = input("> ")
    if not data_str.strip():
        return None
    try:
        return json.loads(data_str)
    except json.JSONDecodeError:
        print(f"{Fore.RED}Invalid JSON format.{Style.RESET_ALL}")
        return None

def main_menu():
    db = get_database()
    client = get_client()

    # Auto-seed if empty
    if db.claims.count_documents({}) == 0:
        print(f"{Fore.YELLOW}Database is empty. Auto-seeding...{Style.RESET_ALL}")
        seed_database(db)
        pause()

    while True:
        clear_screen()
        print_header()
        
        print(f"  {Fore.YELLOW}1.{Style.RESET_ALL} [REPORT] Database Summary")
        print(f"  {Fore.YELLOW}2.{Style.RESET_ALL} [+]  Create Operations")
        print(f"  {Fore.YELLOW}3.{Style.RESET_ALL} [SEARCH]  Read Operations")
        print(f"  {Fore.YELLOW}4.{Style.RESET_ALL} [EDIT]  Update Operations")
        print(f"  {Fore.YELLOW}5.{Style.RESET_ALL} [-]  Delete Operations")
        print(f"  {Fore.YELLOW}6.{Style.RESET_ALL} [ANALYTICS] Analytics Dashboard")
        print(f"  {Fore.YELLOW}7.{Style.RESET_ALL} [INDEX] Indexing Benchmark")
        print(f"  {Fore.YELLOW}8.{Style.RESET_ALL} [ACID] Transaction Demo")
        print(f"  {Fore.YELLOW}9.{Style.RESET_ALL} [WEB]  Launch Web Dashboard (N/A in CLI)")
        print(f"  {Fore.YELLOW}0.{Style.RESET_ALL} Exit")
        
        choice = input(f"\n{Fore.CYAN}Select an option: {Style.RESET_ALL}")

        if choice == '1':
            db_summary(db)
            pause()
            
        elif choice == '2':
            print(f"\n{Fore.CYAN}--- Create Operations ---{Style.RESET_ALL}")
            print("1. Register Patient")
            print("2. Submit Claim")
            sub_choice = input("Select: ")
            if sub_choice == '1':
                data = get_input_dict("Patient")
                if data: register_patient(db, data)
            elif sub_choice == '2':
                data = get_input_dict("Claim")
                if data: submit_claim(db, data)
            pause()

        elif choice == '3':
            print(f"\n{Fore.CYAN}--- Read Operations ---{Style.RESET_ALL}")
            print("1. All Claims (limit 10)")
            print("2. Claims by Status")
            print("3. Claims by Patient")
            print("4. Claim Details (with lookup)")
            print("5. Search by Diagnosis")
            print("6. High-Value Claims")
            sub_choice = input("Select: ")
            
            if sub_choice == '1':
                claims = get_all_claims(db)
                for c in claims: print(c)
            elif sub_choice == '2':
                status = input("Enter Status (e.g., Approved, Submitted): ")
                claims = get_claims_by_status(db, status)
                for c in claims: print(c)
            elif sub_choice == '3':
                patient_id = input("Enter Patient ID (e.g., P001): ")
                claims = get_claims_by_patient(db, patient_id)
                for c in claims: print(c)
            elif sub_choice == '4':
                claim_id = input("Enter Claim ID: ")
                claim = get_claim_details(db, claim_id)
                print(claim)
            elif sub_choice == '5':
                diag = input("Enter ICD Code (e.g., J01.90): ")
                claims = search_claims_by_diagnosis(db, diag)
                for c in claims: print(c)
            elif sub_choice == '6':
                thresh = float(input("Enter threshold amount: ") or 100000)
                claims = get_high_value_claims(db, thresh)
                for c in claims: print(c)
            pause()

        elif choice == '4':
            print(f"\n{Fore.CYAN}--- Update Operations ---{Style.RESET_ALL}")
            print("1. Update Claim Status")
            print("2. Update Approved Amount")
            sub_choice = input("Select: ")
            if sub_choice == '1':
                claim_id = input("Claim ID: ")
                status = input("New Status: ")
                notes = input("Notes: ")
                update_claim_status(db, claim_id, status, notes, "CLI User")
            elif sub_choice == '2':
                claim_id = input("Claim ID: ")
                amount = float(input("Approved Amount: ") or 0)
                update_approved_amount(db, claim_id, amount)
            pause()

        elif choice == '5':
            print(f"\n{Fore.CYAN}--- Delete Operations ---{Style.RESET_ALL}")
            print("1. Cancel Claim (Soft Delete)")
            print("2. Hard Delete Claim")
            sub_choice = input("Select: ")
            if sub_choice == '1':
                claim_id = input("Claim ID: ")
                reason = input("Reason: ")
                cancel_claim(db, claim_id, reason)
            elif sub_choice == '2':
                claim_id = input("Claim ID: ")
                hard_delete_claim(db, claim_id)
            pause()

        elif choice == '6':
            run_all_analytics(db)
            pause()

        elif choice == '7':
            run_indexing_benchmark(db)
            pause()

        elif choice == '8':
            run_transaction_demo(db, client)
            pause()

        elif choice == '9':
            print("Web dashboard not implemented in CLI mode.")
            pause()

        elif choice == '0':
            print(f"{Fore.GREEN}Exiting... Have a great day!{Style.RESET_ALL}")
            sys.exit(0)
            
        else:
            print(f"{Fore.RED}Invalid option!{Style.RESET_ALL}")
            pause()

if __name__ == '__main__':
    main_menu()

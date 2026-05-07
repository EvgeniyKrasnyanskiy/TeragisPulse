from database import execute_query, DatabaseError
import os
from dotenv import load_dotenv

load_dotenv()

import traceback

def test_connection():
    print("Testing database connection...")
    try:
        # Simple query to check if we can reach the DB
        result = execute_query("SELECT 1")
        if result:
            print("Database connection: SUCCESS")
        else:
            print("Database connection: FAILED (Empty result)")
    except DatabaseError as e:
        print(f"Database connection: FAILED")
        print(f"Error details: {e}")
    except Exception as e:
        print(f"An unexpected error occurred:")
        traceback.print_exc()

if __name__ == "__main__":
    test_connection()

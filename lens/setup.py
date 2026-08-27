import os
from dotenv import load_dotenv

def check_env():
    load_dotenv()
    print("Checking environment...")
    required = ["GROQ_API_KEY", "GEMINI_API_KEY", "NEON_DATABASE_URL"]
    all_ok = True
    for req in required:
        if not os.getenv(req):
            print(f"Warning: {req} is missing in .env")
            all_ok = False
    
    if all_ok:
        print("[OK] All required environment variables are set.")
    print("Environment setup verification complete.")

if __name__ == "__main__":
    check_env()


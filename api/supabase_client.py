from dotenv import load_dotenv
from supabase import create_client
import os

load_dotenv()

url = f"https://{os.getenv('SUPABASE_PROJECT_ID')}.supabase.co"
key = os.getenv("SUPABASE_API_KEY")

print("PROJECT_ID:", os.getenv("SUPABASE_PROJECT_ID"))
print("API_KEY exists:", os.getenv("SUPABASE_API_KEY") is not None)
print("API_KEY prefix:", (os.getenv("SUPABASE_API_KEY") or "")[:10])

supabase = create_client(url, key)
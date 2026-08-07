from dotenv import load_dotenv
from supabase import create_client
import os

load_dotenv()

url = f"https://{os.getenv('SUPABASE_PROJECT_ID')}.supabase.co"
key = os.getenv("SUPABASE_API_KEY")

supabase = create_client(url, key)
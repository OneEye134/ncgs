from dotenv import load_dotenv
from supabase import create_client
import os

load_dotenv()

url = f"https://{os.getenv('SUPABASE_PROJECT_ID')}.supabase.co"
key = os.getenv("SUPABASE_API_KEY")

if not os.getenv("SUPABASE_PROJECT_ID") or not key:
    raise RuntimeError(
        "SUPABASE_PROJECT_ID and SUPABASE_API_KEY must be set (check your .env file)"
    )

# This client is shared by every request from every user - Flask handles
# many users concurrently through the same process/module, so there is
# only ever one `supabase` object. Calling `supabase.auth.sign_in_with_password(...)`
# or `.sign_up(...)` on it swaps the access token this *shared* client
# attaches to every future request (including other users' unrelated
# Storage/DB calls) until someone else logs in and overwrites it again -
# meaning one person logging in or out can silently break story/chapter
# loading for everyone else until the process restarts.
#
# Never call `.auth.*` on this client. Use `get_auth_client()` below (a
# throwaway client) for anything that verifies credentials instead.
supabase = create_client(url, key)


def get_auth_client():
    """A fresh, disposable Supabase client for verifying login/signup
    credentials via sign_in_with_password / sign_up. Its auth state is
    never shared with the rest of the app, so it's safe to mutate
    per-request."""
    return create_client(url, key)
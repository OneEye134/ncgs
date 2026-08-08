"""
Quick diagnostic: run this directly (python debug_story.py) from the same
folder as supabase_client.py, with your .env in place. It bypasses Flask
entirely so we can see exactly what Supabase returns/errors on.
"""
from supabase_client import supabase

STORY_ID = "b24c63c1-78ff-49b0-ad7b-9ba019d3e5d1"

print("=== 1. Does this id exist in the stories table? ===")
try:
    row = supabase.table("stories").select("*").eq("id", STORY_ID).execute()
    print(row.data)
except Exception as e:
    print("ERROR querying stories table:", e)

print("\n=== 2. Raw list() of the storage folder ===")
try:
    files = supabase.storage.from_("stories").list(STORY_ID)
    print(f"Got {len(files)} entries:")
    for f in files:
        print(" -", f)
except Exception as e:
    print("ERROR listing storage:", e)

print("\n=== 3. Try downloading one known chapter directly ===")
try:
    content = supabase.storage.from_("stories").download(f"{STORY_ID}/1chapS1.md")
    print("Downloaded", len(content), "bytes. First 100 chars:")
    print(content.decode("utf-8")[:100])
except Exception as e:
    print("ERROR downloading chapter:", e)
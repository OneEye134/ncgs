import re

from flask import Flask, render_template, request, redirect, session, jsonify
from supabase_client import supabase

app = Flask(__name__)
app.secret_key = "5e9c1ba28a23064b4efe77ce9e7b7ae232739ebee96de578c347eb4fba2ac772"

CHAPTER_FILENAME_RE = re.compile(r"^(\d+)chapS(\d+)\.md$")


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/story/<story>')
def story(story):
    return render_template('story.html', story_id=story)


@app.route('/auth')
def userauth():
    return render_template('userauth.html')


@app.route("/signup", methods=["POST"])
def signup():
    username = request.form["username"]
    email = request.form["email"]
    password = request.form["password"]

    auth = supabase.auth.sign_up({
        "email": email,
        "password": password
    })

    if auth.user is None:
        return "Failed to create account", 400

    supabase.table("users").insert({
        "id": auth.user.id,
        "username": username,
        "email": email
    }).execute()

    # log the user in immediately after signup
    session["user_id"] = auth.user.id
    session["username"] = username
    session["email"] = email

    return redirect("/?signup=true")


@app.route("/login", methods=["POST"])
def login():
    identifier = request.form["identifier"].strip()
    password = request.form["password"]

    if "@" in identifier:
        # looks like an email, use it directly
        email = identifier
    else:
        # treat it as a username and look up the matching email
        user_row = supabase.table("users").select("email").eq("username", identifier).single().execute()
        if not user_row.data:
            return "No account found with that username", 400
        email = user_row.data["email"]

    try:
        auth = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
    except Exception as e:
        return f"Login failed: {e}", 400

    if auth.user is None:
        return "Invalid username/email or password", 400

    # fetch the username to store in the session/cookie
    user_row = supabase.table("users").select("username").eq("id", auth.user.id).single().execute()
    username = user_row.data["username"] if user_row.data else email

    session["user_id"] = auth.user.id
    session["username"] = username
    session["email"] = auth.user.email

    return redirect("/?login=true")


@app.route("/api/getstories")
def getstories():
    result = (
        supabase.table("stories")
        .select("""
            id,
            title,
            description,
            created_at,
            edited_at,
            users:writer (
                username,
                nickname
            )
        """)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )

    return result.data


@app.route("/api/getstoryinfo/<story>")
def getstoryinfo(story):
    result = (
        supabase.table("stories")
        .select("""
            id,
            title,
            description,
            created_at,
            edited_at,
            users:writer (
                username,
                nickname
            )
        """)
        .eq("id", story)
        .single()
        .execute()
    )

    if not result.data:
        return jsonify({"error": "Story not found"}), 404

    return jsonify(result.data)


@app.route("/api/getstory/<story>")
def getstory(story):
    files = supabase.storage.from_("stories").list(story)

    seasons = {}

    for file in files:
        name = file["name"]

        # Skip non-chapter files
        if not name.endswith(".md") or name == "ignore.md":
            continue

        # Match e.g. 1chapS1.md, 12chapS2.md
        m = CHAPTER_FILENAME_RE.match(name)
        if not m:
            continue

        season = int(m.group(2))
        seasons[season] = seasons.get(season, 0) + 1

    # Download the CSS
    css = (
        supabase.storage
        .from_("stories")
        .download(f"{story}/chap-style.css")
        .decode("utf-8")
    )

    return jsonify({
        "seasons": seasons,
        "css": css
    })


@app.route("/api/getchapter/<story>/<int:season>/<int:num>")
def getchapter(story, season, num):
    filename = f"{num}chapS{season}.md"

    try:
        content = (
            supabase.storage
            .from_("stories")
            .download(f"{story}/{filename}")
            .decode("utf-8")
        )
    except Exception:
        return jsonify({"error": "Chapter not found"}), 404

    return jsonify({
        "season": season,
        "chapter": num,
        "content": content
    })


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)
import re
import uuid
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, session, jsonify
try:
    from api.supabase_client import supabase
except:
    from supabase_client import supabase

try:
    from postgrest.exceptions import APIError
except ImportError:
    APIError = None

app = Flask(__name__)
app.secret_key = "5e9c1ba28a23064b4efe77ce9e7b7ae232739ebee96de578c347eb4fba2ac772"

CHAPTER_FILENAME_RE = re.compile(r"^(\d+)chapS(\d+)\.md$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Shown to writers as a starting point for a story's custom stylesheet.
# They're free to replace this entirely with their own CSS.
DEFAULT_CHAPTER_CSS = """/* Default chapter styling for this story.
   Feel free to replace this entirely with your own CSS - it will be
   applied to every chapter page for this story. */

.chapter-content {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 1.05em;
    line-height: 1.7;
}

.chapter-content h1,
.chapter-content h2,
.chapter-content h3 {
    font-family: Arial, sans-serif;
}

.chapter-content blockquote {
    border-left: 3px solid #5070ff;
    margin: 1em 0;
    padding: 0.2em 1em;
    color: #555;
    font-style: italic;
}
"""


def slugify(text):
    """Turn heading text into a URL/tag-safe slug, e.g.
    'Phase 1: The Horror Begins' -> 'phase-1-the-horror-begins'."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "section"


def tag_headings(markdown_text):
    """Re-derive the [id] tags that precede each heading, e.g. turns
    '## Phase 1: The Horror Begins' into
    '[phase-1-the-horror-begins]## Phase 1: The Horror Begins'.
    This is the inverse of the stripChapterTag() logic in story.html,
    so content written in the plain markdown editor round-trips into
    the site's existing [id][md] chapter format on save."""
    lines = markdown_text.split("\n")
    seen = {}
    out = []
    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            hashes, title = m.groups()
            slug = slugify(title)
            if slug in seen:
                seen[slug] += 1
                slug = f"{slug}-{seen[slug]}"
            else:
                seen[slug] = 1
            out.append(f"[{slug}]{hashes} {title}")
        else:
            out.append(line)
    return "\n".join(out)


def get_owned_story(story_id):
    """Fetch a story and make sure the logged-in user is its writer.
    Returns (story_row, None) on success, or (None, (response, status))
    on failure - the caller can `return err` directly."""
    result = supabase.table("stories").select("id, writer").eq("id", story_id).execute()
    if not result.data:
        return None, (jsonify({"error": "Story not found"}), 404)

    story_row = result.data[0]
    if story_row["writer"] != session.get("user_id"):
        return None, (jsonify({"error": "You don't have permission to edit this story"}), 403)

    return story_row, None


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/story/<story>')
def story(story):
    return render_template('story.html', story_id=story)


@app.route('/auth')
def userauth():
    return render_template('userauth.html')


@app.route('/write')
def write_dashboard():
    if "user_id" not in session:
        return redirect("/auth")
    return render_template('write_dashboard.html')


@app.route('/write/<story>')
def write_editor(story):
    if "user_id" not in session:
        return redirect("/auth")

    story_row, err = get_owned_story(story)
    if err:
        return err

    return render_template('write_editor.html', story_id=story)


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
    session["avatar_url"] = None

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
    # (avatar_url may not exist on the users table in every environment,
    # so fall back to a select without it rather than failing login)
    try:
        user_row = supabase.table("users").select("username, avatar_url").eq("id", auth.user.id).single().execute()
        avatar_url = user_row.data["avatar_url"] if user_row.data else None
    except Exception as e:
        if "avatar_url" not in str(e):
            raise
        user_row = supabase.table("users").select("username").eq("id", auth.user.id).single().execute()
        avatar_url = None
    username = user_row.data["username"] if user_row.data else email

    session["user_id"] = auth.user.id
    session["username"] = username
    session["email"] = auth.user.email
    session["avatar_url"] = avatar_url

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

    # Download the CSS (a story may not have a custom stylesheet yet)
    try:
        css = (
            supabase.storage
            .from_("stories")
            .download(f"{story}/chap-style.css")
            .decode("utf-8")
        )
    except Exception:
        css = ""

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


@app.route("/api/mystories")
def my_stories():
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    result = (
        supabase.table("stories")
        .select("id, title, description, created_at, edited_at")
        .eq("writer", session["user_id"])
        .order("created_at", desc=True)
        .execute()
    )

    return jsonify(result.data)


@app.route("/api/createstory", methods=["POST"])
def create_story():
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    # id is a uuid column with a default in Supabase - don't set it,
    # just read back the generated id from the insert response.
    result = supabase.table("stories").insert({
        "title": title,
        "description": description,
        "writer": session["user_id"]
    }).execute()

    if not result.data:
        return jsonify({"error": "Could not create story"}), 500

    return jsonify({"id": result.data[0]["id"]})


@app.route("/api/updatestoryinfo/<story>", methods=["POST"])
def update_story_info(story):
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    story_row, err = get_owned_story(story)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    supabase.table("stories").update({
        "title": title,
        "description": description,
        "edited_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", story).execute()

    return jsonify({"success": True})


@app.route("/api/savechapter/<story>", methods=["POST"])
def save_chapter(story):
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    story_row, err = get_owned_story(story)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    content = data.get("content", "")

    try:
        season = int(data.get("season"))
        num = int(data.get("num"))
    except (TypeError, ValueError):
        return jsonify({"error": "Season and chapter number must be whole numbers"}), 400

    if season < 1 or num < 1:
        return jsonify({"error": "Season and chapter number must be positive"}), 400

    # Re-tag headings with their [id] before saving, so content written
    # in the plain markdown editor matches the site's chapter format.
    tagged = tag_headings(content)
    filename = f"{num}chapS{season}.md"

    supabase.storage.from_("stories").upload(
        f"{story}/{filename}",
        tagged.encode("utf-8"),
        {"content-type": "text/markdown", "upsert": "true"}
    )

    supabase.table("stories").update({
        "edited_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", story).execute()

    return jsonify({"success": True, "season": season, "num": num})


@app.route("/api/getstyle/<story>")
def get_style(story):
    if request.args.get("default"):
        return jsonify({"css": DEFAULT_CHAPTER_CSS})

    try:
        css = (
            supabase.storage
            .from_("stories")
            .download(f"{story}/chap-style.css")
            .decode("utf-8")
        )
    except Exception:
        css = DEFAULT_CHAPTER_CSS

    return jsonify({"css": css})


@app.route("/api/savestyle/<story>", methods=["POST"])
def save_style(story):
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    story_row, err = get_owned_story(story)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    css = data.get("css", "")

    supabase.storage.from_("stories").upload(
        f"{story}/chap-style.css",
        css.encode("utf-8"),
        {"content-type": "text/css", "upsert": "true"}
    )

    return jsonify({"success": True})


@app.route("/api/getcomments/<story>")
def get_comments(story):
    """Returns comments for a story. Pass ?chapter=N for comments on a
    specific chapter, or omit it for comments on the story as a whole
    (chapter IS NULL)."""
    chapter_param = request.args.get("chapter")

    # avatar_url may not exist on the users table in every environment
    # (e.g. it hasn't been migrated in yet). Try the full select first,
    # and fall back to a version without avatar_url if that column is
    # missing, rather than 500'ing the whole endpoint.
    select_with_avatar = """
        id,
        created_at,
        chapter,
        text,
        user,
        users:user (
            username,
            nickname,
            avatar_url
        )
    """
    select_without_avatar = """
        id,
        created_at,
        chapter,
        text,
        user,
        users:user (
            username,
            nickname
        )
    """

    def build_query(select_clause):
        query = supabase.table("comments").select(select_clause).eq("story", story)
        if chapter_param is None:
            query = query.is_("chapter", "null")
        else:
            query = query.eq("chapter", chapter_num)
        return query

    if chapter_param is not None:
        try:
            chapter_num = int(chapter_param)
        except ValueError:
            return jsonify({"error": "Chapter must be a whole number"}), 400
    else:
        chapter_num = None

    try:
        result = build_query(select_with_avatar).order("created_at", desc=False).execute()
        return jsonify(result.data)
    except Exception as e:
        missing_avatar_col = "avatar_url" in str(e) and (
            "does not exist" in str(e) or (APIError is not None and isinstance(e, APIError))
        )
        if not missing_avatar_col:
            raise

        result = build_query(select_without_avatar).order("created_at", desc=False).execute()
        data = result.data
        for row in data:
            if row.get("users") is not None:
                row["users"].setdefault("avatar_url", None)
        return jsonify(data)


@app.route("/api/postcomment/<story>", methods=["POST"])
def post_comment(story):
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    # Make sure the story actually exists before attaching a comment to it
    story_result = supabase.table("stories").select("id").eq("id", story).execute()
    if not story_result.data:
        return jsonify({"error": "Story not found"}), 404

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    chapter = data.get("chapter")

    if not text:
        return jsonify({"error": "Comment text is required"}), 400

    if chapter is not None:
        try:
            chapter = int(chapter)
        except (TypeError, ValueError):
            return jsonify({"error": "Chapter must be a whole number"}), 400
        if chapter < 1:
            return jsonify({"error": "Chapter must be positive"}), 400

    result = supabase.table("comments").insert({
        "user": session["user_id"],
        "story": story,
        "chapter": chapter,
        "text": text
    }).execute()

    if not result.data:
        return jsonify({"error": "Could not post comment"}), 500

    row = result.data[0]
    return jsonify({
        "id": row["id"],
        "created_at": row["created_at"],
        "chapter": row["chapter"],
        "text": row["text"],
        "user": session["user_id"],
        "users": {
            "username": session.get("username"),
            "nickname": None,
            "avatar_url": session.get("avatar_url")
        }
    })


@app.route("/api/deletecomment/<comment_id>", methods=["POST"])
def delete_comment(comment_id):
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    result = supabase.table("comments").select("id, user").eq("id", comment_id).execute()
    if not result.data:
        return jsonify({"error": "Comment not found"}), 404

    if result.data[0]["user"] != session["user_id"]:
        return jsonify({"error": "You don't have permission to delete this comment"}), 403

    supabase.table("comments").delete().eq("id", comment_id).execute()
    return jsonify({"success": True})


@app.route("/api/deletestory/<story>", methods=["POST"])
def delete_story(story):
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    story_row, err = get_owned_story(story)
    if err:
        return err

    # Remove every file stored for this story (chapters + custom stylesheet)
    try:
        files = supabase.storage.from_("stories").list(story)
        paths = [f"{story}/{f['name']}" for f in files]
        if paths:
            supabase.storage.from_("stories").remove(paths)
    except Exception:
        pass  # nothing uploaded yet is fine

    # Comments aren't guaranteed to cascade-delete with the story, so
    # clear them out explicitly before removing the story row itself.
    supabase.table("comments").delete().eq("story", story).execute()
    supabase.table("stories").delete().eq("id", story).execute()

    return jsonify({"success": True})


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)
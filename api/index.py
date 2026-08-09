import os
import re
import uuid
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, session, jsonify
try:
    from api.supabase_client import supabase, get_auth_client
except:
    from supabase_client import supabase, get_auth_client

try:
    from postgrest.exceptions import APIError
except ImportError:
    APIError = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or "dev-only-insecure-key-set-FLASK_SECRET_KEY-in-env"

CHAPTER_FILENAME_RE = re.compile(r"^(\d+)chapS(\d+)\.md$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Avatars are uploaded to the "pfp" storage bucket, named after the
# uploader's user id (a uuid), so each user has at most one avatar file
# and re-uploading just overwrites it (upsert=true).
ALLOWED_AVATAR_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5MB

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


def attach_like_counts(stories):
    """Adds a `likes` (int) field to each story dict, based on rows in
    likes_story. Counted in Python rather than via a related-table select,
    since a story with zero likes has no likes_story row to embed at all."""
    story_ids = [s["id"] for s in stories if s.get("id")]

    counts = {}
    if story_ids:
        likes_result = (
            supabase.table("likes_story")
            .select("story")
            .in_("story", story_ids)
            .execute()
        )
        for row in likes_result.data or []:
            story_id = row.get("story")
            counts[story_id] = counts.get(story_id, 0) + 1

    for story in stories:
        story["likes"] = counts.get(story.get("id"), 0)

    return stories


def attach_rank_info(items, id_field):
    """Adds a `rank_info` dict (see rank_for_points) to each item's nested
    `users` object, so avatars rendered from story/comment feeds can drive
    the same rank-based effect used on profile pages. `id_field` is the
    top-level column holding the writer/commenter's user id (e.g. "writer"
    on stories, "user" on comments). Rank is computed once per unique id
    and reused across every item that shares it, since compute_writer_points
    (defined below) re-queries likes/comments received per writer."""
    cache = {}
    for item in items:
        user_id = item.get(id_field)
        users = item.get("users")
        if not users or not user_id:
            continue
        if user_id not in cache:
            stats = compute_writer_points(user_id)
            cache[user_id] = rank_for_points(stats["points"], stats.get("godly", False))
        users["rank_info"] = cache[user_id]
    return items


def attach_chapter_counts(stories):
    """Adds `season` (the highest season number with a chapter) and
    `chapter_count` (total chapters across every season) to each story
    dict, by listing its chapter files in storage - the same source
    getstory() reads its `seasons` map from. A story with no chapters
    yet (or an unreadable listing) just gets season=None, chapter_count=0."""
    for story in stories:
        story_id = story.get("id")
        season = None
        chapter_count = 0
        if story_id:
            try:
                files = supabase.storage.from_("stories").list(story_id)
            except Exception:
                files = []
            for file in files or []:
                name = file.get("name", "")
                if not name.endswith(".md") or name == "ignore.md":
                    continue
                m = CHAPTER_FILENAME_RE.match(name)
                if not m:
                    continue
                season_num = int(m.group(2))
                chapter_count += 1
                if season is None or season_num > season:
                    season = season_num
        story["season"] = season
        story["chapter_count"] = chapter_count
    return stories



# Writer rank progression. Points are earned from reader engagement on a
# writer's own stories: +2 per like, +1 per comment received. Thresholds
# are intentionally uneven (steep at the top) so the higher ranks stay
# meaningful as the site grows - listed highest-to-lowest so rank_for_points()
# can walk down and stop at the first threshold the writer has cleared.
RANKS = [
    (800, "NCGS Icon"),
    (500, "Genre-Defining"),
    (300, "The Plot Bender"),
    (150, "The Specialist"),
    (100, "Wordsmith"),
    (50, "Lore Sculptor"),
    (30, "The Storyteller"),
    (10, "World Builder"),
    (1, "Aspiring Writer"),
    (0, "Newcomer"),
]

POINTS_PER_LIKE = 2
POINTS_PER_COMMENT = 1


def parse_extra_points(extra_point_text):
    """`users.extra_point` is a free-text admin column. The literal text
    "Infinity" (any case) marks a god-tier account - handled entirely
    separately from the point math, see rank_for_points - and anything
    else that parses as a whole number is added straight to that user's
    points as a manual bonus/penalty. Blank or non-numeric junk is
    treated as no bonus rather than erroring."""
    if extra_point_text is None:
        return 0, False

    text = str(extra_point_text).strip()
    if not text:
        return 0, False
    if text.lower() == "infinity":
        return 0, True

    try:
        return int(text), False
    except ValueError:
        return 0, False


def rank_for_points(points, godly=False):
    """Returns the writer's current rank plus what's needed to reach the
    next one, based on RANKS above. `godly=True` (users.extra_point ==
    "Infinity") bypasses the threshold table entirely: it's a one-off
    rank above NCGS Icon reserved for the site's creator."""
    if godly:
        return {
            "points": points,
            "rank": "Creator of NCGS",
            "rank_threshold": points,
            "next_rank": None,
            "next_rank_threshold": None,
            "points_to_next_rank": 0,
        }

    rank_index = len(RANKS) - 1
    for i, (threshold, _name) in enumerate(RANKS):
        if points >= threshold:
            rank_index = i
            break

    current_threshold, current_name = RANKS[rank_index]
    next_rank = RANKS[rank_index - 1] if rank_index > 0 else None

    return {
        "points": points,
        "rank": current_name,
        "rank_threshold": current_threshold,
        "next_rank": next_rank[1] if next_rank else None,
        "next_rank_threshold": next_rank[0] if next_rank else None,
        "points_to_next_rank": (next_rank[0] - points) if next_rank else 0,
    }


def compute_writer_points(user_id):
    """Sums up likes and comments received across every story this user
    has written, and converts that into a point total. A writer with no
    stories (or no engagement yet) simply scores 0 (plus extra_point, if
    any).

    Two rules keep engagement from being gamed:
    - The writer's own likes/comments on their own story never count.
    - Only a reader's *first* comment on a given story counts - posting
      ten comments on the same story is worth the same one point as
      posting one. Likes already can't repeat (likestory toggles a
      single row per user/story - see like_story), so no dedup is
      needed there.

    `extra_point` is a manual admin override (see parse_extra_points) -
    it's added on top of earned points, or replaces the rank entirely
    with a "Creator of NCGS" god-tier when it's literally "Infinity"."""
    user_result = supabase.table("users").select("extra_point").eq("id", user_id).execute()
    extra_point_text = user_result.data[0]["extra_point"] if user_result.data else None
    extra_points, godly = parse_extra_points(extra_point_text)

    stories_result = supabase.table("stories").select("id").eq("writer", user_id).execute()
    story_ids = [s["id"] for s in (stories_result.data or [])]

    if not story_ids:
        return {"points": extra_points, "likes": 0, "comments": 0, "godly": godly}

    likes_result = (
        supabase.table("likes_story")
        .select("story", count="exact")
        .in_("story", story_ids)
        .neq("user", user_id)
        .execute()
    )
    likes_count = likes_result.count or 0

    # Comments (including replies) have no uniqueness constraint, so
    # fetch the commenter ids and de-duplicate per-story in Python -
    # a reader who comments 5 times on one story, and once on another,
    # should only earn the writer 2 points, not 6.
    comments_result = (
        supabase.table("comments")
        .select("user, story")
        .in_("story", story_ids)
        .neq("user", user_id)
        .execute()
    )
    unique_commenters_per_story = set()
    for row in (comments_result.data or []):
        if row.get("user"):
            unique_commenters_per_story.add((row["story"], row["user"]))
    comments_count = len(unique_commenters_per_story)

    points = likes_count * POINTS_PER_LIKE + comments_count * POINTS_PER_COMMENT + extra_points
    return {"points": points, "likes": likes_count, "comments": comments_count, "godly": godly}


def sanitize_search_term(term):
    # Escape Postgres' LIKE wildcards so a search for e.g. "50%" or "a_b"
    # matches those characters literally instead of as wildcards.
    term = term.replace("\\", "").replace("%", "\\%").replace("_", "\\_")
    # Commas and parens have syntax meaning in the postgrest or_() filter
    # string below (which isn't parameterized), so drop them entirely.
    term = re.sub(r"[,()]", "", term)
    return term.strip()


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/story/<story>')
def story(story):
    return render_template('story.html', story_id=story)


@app.route('/auth')
def userauth():
    return render_template('userauth.html')


@app.route('/profile')
def profile():
    if "user_id" not in session:
        return redirect("/auth")
    return render_template('profile.html')


@app.route('/favorites')
def favorites_page():
    if "user_id" not in session:
        return redirect("/auth")
    return render_template('favorites.html')


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
    username = request.form["username"].strip()
    email = request.form["email"]
    password = request.form["password"]

    if not username:
        return "Username is required", 400

    # Failsafe: reject a taken username before creating an auth account
    # at all. Without this, sign_up() below would succeed (auth users
    # don't know about the `users` table's username column), and only
    # the insert afterward would fail - leaving a real auth account with
    # no matching `users` row, which can't log in and can't sign up
    # again with that email either.
    existing = (
        supabase.table("users")
        .select("id")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    if existing.data:
        return "That username is already taken", 400

    # Use a throwaway client for this - signing up on the shared `supabase`
    # client would leave it authenticated as this user for every other
    # request in the process until someone else logs in/signs up.
    auth_client = get_auth_client()
    auth = auth_client.auth.sign_up({
        "email": email,
        "password": password
    })

    if auth.user is None:
        return "Failed to create account", 400

    try:
        supabase.table("users").insert({
            "id": auth.user.id,
            "username": username,
            "email": email
        }).execute()
    except Exception as e:
        # Someone else claimed this exact username in the moment between
        # the availability check above and this insert. The `supabase`
        # client here only holds an anon/user-level key (see
        # supabase_client.py), so there's no admin API available to
        # clean up the now-orphaned auth account - the DB-level unique
        # constraint is the real backstop, this is just a friendlier
        # message than a raw 500 for the (rare) case it fires.
        is_duplicate = "duplicate key" in str(e).lower() or "already exists" in str(e).lower() or (
            APIError is not None and isinstance(e, APIError)
        )
        if is_duplicate:
            return "That username is already taken", 400
        raise

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
        # .single() raises (rather than returning empty data) when zero
        # rows match, so a nonexistent username has to be caught here.
        try:
            user_row = supabase.table("users").select("email").eq("username", identifier).single().execute()
        except Exception:
            return "No account found with that username", 400
        if not user_row.data:
            return "No account found with that username", 400
        email = user_row.data["email"]

    # Same reasoning as signup(): verify the password on a throwaway
    # client so the shared `supabase` client's auth state never changes.
    try:
        auth_client = get_auth_client()
        auth = auth_client.auth.sign_in_with_password({
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


STORY_SELECT_WITH_AVATAR = """
    id,
    title,
    description,
    created_at,
    edited_at,
    writer,
    users:writer (
        username,
        nickname,
        avatar_url
    )
"""
STORY_SELECT_WITHOUT_AVATAR = """
    id,
    title,
    description,
    created_at,
    edited_at,
    writer,
    users:writer (
        username,
        nickname
    )
"""


def _select_stories_with_avatar_fallback(build_query):
    """Runs `build_query(select_clause).execute()`, preferring a select
    that includes the writer's avatar_url. avatar_url may not exist on
    the users table in every environment (e.g. it hasn't been migrated
    in yet), so this falls back to a select without it - same pattern as
    get_comments below - rather than 500'ing the whole endpoint."""
    try:
        return build_query(STORY_SELECT_WITH_AVATAR).execute()
    except Exception as e:
        missing_avatar_col = "avatar_url" in str(e) and (
            "does not exist" in str(e) or (APIError is not None and isinstance(e, APIError))
        )
        if not missing_avatar_col:
            raise
        result = build_query(STORY_SELECT_WITHOUT_AVATAR).execute()
        for row in result.data or []:
            if row.get("users") is not None:
                row["users"].setdefault("avatar_url", None)
        return result


@app.route("/api/getstories")
def getstories():
    """Returns the story feed. Pass ?sort=likes for most-liked-first,
    or omit it (or ?sort=recent) for newest-first."""
    sort = request.args.get("sort", "recent")

    def build_query(select_clause):
        q = supabase.table("stories").select(select_clause).order("created_at", desc=True)
        # PostgREST can't order by a related table's aggregate count, so
        # for "most liked" a larger recent pool is pulled and ranked in
        # Python instead of just the latest 10.
        return q.limit(200 if sort == "likes" else 10)

    result = _select_stories_with_avatar_fallback(build_query)
    stories = attach_like_counts(result.data or [])
    stories = attach_rank_info(stories, "writer")

    if sort == "likes":
        stories.sort(key=lambda s: (s["likes"], s["created_at"]), reverse=True)
        stories = stories[:10]

    stories = attach_chapter_counts(stories)
    return jsonify(stories)


@app.route("/api/searchstories")
def search_stories():
    """Search stories. A query starting with '#' matches that exact
    hashtag in the title/description (e.g. '#fantasy'). Anything else is
    a plain substring match against the title or description. Either way,
    results come back most-liked-first."""
    raw_query = (request.args.get("q") or "").strip()
    if not raw_query:
        return jsonify({"error": "Search query is required"}), 400

    if raw_query.startswith("#"):
        # Only keep hashtag-safe characters (word chars + hyphen) - this
        # also keeps the tag safe to interpolate into the postgrest or_()
        # filter string below, since that string isn't parameterized.
        tag = re.sub(r"[^\w-]", "", raw_query.lstrip("#"))
        if not tag:
            return jsonify({"error": "Search query is required"}), 400
        pattern = f"%#{tag}%"
    else:
        term = sanitize_search_term(raw_query)
        if not term:
            return jsonify({"error": "Search query is required"}), 400
        pattern = f"%{term}%"

    def build_query(select_clause):
        return (
            supabase.table("stories")
            .select(select_clause)
            .or_(f"title.ilike.{pattern},description.ilike.{pattern}")
            .order("created_at", desc=True)
            .limit(50)
        )

    result = _select_stories_with_avatar_fallback(build_query)

    stories = attach_like_counts(result.data or [])
    stories = attach_rank_info(stories, "writer")
    stories.sort(key=lambda s: (s["likes"], s["created_at"]), reverse=True)
    stories = attach_chapter_counts(stories)
    return jsonify(stories)


@app.route("/api/getstoryinfo/<story>")
def getstoryinfo(story):
    # .single() raises (rather than returning empty data) when zero rows
    # match, so a nonexistent story id has to be caught here rather than
    # relying on `if not result.data` below.
    try:
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
    except Exception:
        return jsonify({"error": "Story not found"}), 404

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


@app.route("/api/getprofile")
def get_profile():
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    # hide_mild_warnings may not exist on the users table in every
    # environment, so fall back to a select without it rather than
    # failing the whole profile load (same pattern as avatar_url below).
    try:
        result = (
            supabase.table("users")
            .select("username, email, nickname, bio, avatar_url, hide_mild_warnings")
            .eq("id", session["user_id"])
            .single()
            .execute()
        )
    except Exception as e:
        msg = str(e)
        if "hide_mild_warnings" not in msg:
            not_found = "0 rows" in msg or "contains 0 rows" in msg or "PGRST116" in msg
            if not_found:
                return jsonify({"error": "Profile not found"}), 404
            return jsonify({"error": f"Could not load profile: {msg}"}), 500
        try:
            result = (
                supabase.table("users")
                .select("username, email, nickname, bio, avatar_url")
                .eq("id", session["user_id"])
                .single()
                .execute()
            )
        except Exception as e2:
            msg2 = str(e2)
            not_found = "0 rows" in msg2 or "contains 0 rows" in msg2 or "PGRST116" in msg2
            if not_found:
                return jsonify({"error": "Profile not found"}), 404
            return jsonify({"error": f"Could not load profile: {msg2}"}), 500

    if not result.data:
        return jsonify({"error": "Profile not found"}), 404

    result.data.setdefault("hide_mild_warnings", False)

    stats = compute_writer_points(session["user_id"])
    result.data["rank_info"] = rank_for_points(stats["points"], stats.get("godly", False))
    result.data["rank_info"]["likes_received"] = stats["likes"]
    result.data["rank_info"]["comments_received"] = stats["comments"]

    return jsonify(result.data)


@app.route("/profile/<username>")
def public_profile(username):
    return render_template('public_profile.html', username=username)


@app.route("/api/getuserprofile/<username>")
def get_user_profile(username):
    try:
        user_result = (
            supabase.table("users")
            .select("id, username, nickname, bio, avatar_url")
            .eq("username", username)
            .single()
            .execute()
        )
    except Exception as e:
        msg = str(e)
        not_found = "0 rows" in msg or "contains 0 rows" in msg or "PGRST116" in msg
        if not_found:
            return jsonify({"error": "User not found"}), 404
        return jsonify({"error": f"Could not load profile: {msg}"}), 500

    if not user_result.data:
        return jsonify({"error": "User not found"}), 404

    user_row = user_result.data

    stories_result = (
        supabase.table("stories")
        .select("id, title, description, created_at, edited_at")
        .eq("writer", user_row["id"])
        .order("created_at", desc=True)
        .execute()
    )

    stats = compute_writer_points(user_row["id"])
    rank_info = rank_for_points(stats["points"], stats.get("godly", False))
    rank_info["likes_received"] = stats["likes"]
    rank_info["comments_received"] = stats["comments"]

    return jsonify({
        "username": user_row["username"],
        "nickname": user_row.get("nickname"),
        "bio": user_row.get("bio"),
        "avatar_url": user_row.get("avatar_url"),
        "stories": stories_result.data or [],
        "rank_info": rank_info
    })


@app.route("/api/updateprofile", methods=["POST"])
def update_profile():
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    data = request.get_json(silent=True) or {}
    nickname = (data.get("nickname") or "").strip()
    bio = (data.get("bio") or "").strip()

    if len(bio) > 500:
        return jsonify({"error": "Bio must be 500 characters or fewer"}), 400

    update_payload = {
        "bio": bio,
        "nickname": nickname or None
    }

    result = (
        supabase.table("users")
        .update(update_payload)
        .eq("id", session["user_id"])
        .execute()
    )

    if not result.data:
        return jsonify({"error": "Could not update profile"}), 500

    return jsonify({"success": True, "nickname": update_payload["nickname"], "bio": bio})


@app.route("/api/uploadavatar", methods=["POST"])
def upload_avatar():
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    file = request.files.get("avatar")
    if not file or not file.filename:
        return jsonify({"error": "No image provided"}), 400

    # Trust the sniffed mimetype Flask/Werkzeug derives from the upload,
    # not just the file extension - matches the allow-list in the prompt.
    mime_type = (file.mimetype or "").lower()
    if mime_type not in ALLOWED_AVATAR_MIME_TYPES:
        return jsonify({"error": "Only JPEG, PNG, and WebP images are allowed"}), 400

    file_bytes = file.read()
    if not file_bytes:
        return jsonify({"error": "No image provided"}), 400
    if len(file_bytes) > MAX_AVATAR_BYTES:
        return jsonify({"error": "Image must be 5MB or smaller"}), 400

    user_id = session["user_id"]

    try:
        supabase.storage.from_("pfp").upload(
            user_id,
            file_bytes,
            {"content-type": mime_type, "upsert": "true"}
        )
    except Exception as e:
        return jsonify({"error": f"Could not upload image: {e}"}), 500

    avatar_url = supabase.storage.from_("pfp").get_public_url(user_id)
    # Cache-bust so browsers (and other users' already-loaded pages) pick
    # up the new image instead of showing a stale cached one at the same
    # path.
    separator = "&" if "?" in avatar_url else "?"
    avatar_url = f"{avatar_url}{separator}v={int(datetime.now(timezone.utc).timestamp())}"

    try:
        result = (
            supabase.table("users")
            .update({"avatar_url": avatar_url})
            .eq("id", user_id)
            .execute()
        )
    except Exception as e:
        return jsonify({"error": f"Uploaded image but could not save profile: {e}"}), 500

    if not result.data:
        return jsonify({"error": "Uploaded image but could not save profile"}), 500

    session["avatar_url"] = avatar_url

    return jsonify({"success": True, "avatar_url": avatar_url})


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
    (chapter IS NULL).

    Comments can be replies: `comment` holds the id of the top-level
    comment a reply's thread belongs to (NULL for top-level comments
    themselves), and `reply` holds the id of the specific comment/reply
    being directly replied to (also NULL for top-level comments). The
    list comes back flat, sorted oldest-first, so callers can either use
    it as-is or nest it client-side using those two fields."""
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
        comment,
        reply,
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
        comment,
        reply,
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
        return jsonify(attach_rank_info(result.data or [], "user"))
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
        return jsonify(attach_rank_info(data, "user"))


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
    reply_to = data.get("reply_to")

    if not text:
        return jsonify({"error": "Comment text is required"}), 400

    if chapter is not None:
        try:
            chapter = int(chapter)
        except (TypeError, ValueError):
            return jsonify({"error": "Chapter must be a whole number"}), 400
        if chapter < 1:
            return jsonify({"error": "Chapter must be positive"}), 400

    # If this is a reply, look up the comment being replied to and figure
    # out where it fits: `comment` always points at the root/top-level
    # comment of the thread, and `reply` points at the specific
    # comment/reply being directly answered. Replying to a top-level
    # comment sets both to that comment's id; replying to a reply
    # inherits the thread's root in `comment` and points `reply` at the
    # reply itself.
    thread_comment = None
    thread_reply = None
    if reply_to:
        target_result = (
            supabase.table("comments")
            .select("id, story, chapter, comment")
            .eq("id", reply_to)
            .execute()
        )
        if not target_result.data:
            return jsonify({"error": "The comment you're replying to no longer exists"}), 404

        target = target_result.data[0]
        if target["story"] != story or target.get("chapter") != chapter:
            return jsonify({"error": "Can't reply to a comment from a different discussion"}), 400

        thread_comment = target["comment"] or target["id"]
        thread_reply = target["id"]

    result = supabase.table("comments").insert({
        "user": session["user_id"],
        "story": story,
        "chapter": chapter,
        "text": text,
        "comment": thread_comment,
        "reply": thread_reply
    }).execute()

    if not result.data:
        return jsonify({"error": "Could not post comment"}), 500

    row = result.data[0]
    own_stats = compute_writer_points(session["user_id"])
    return jsonify({
        "id": row["id"],
        "created_at": row["created_at"],
        "chapter": row["chapter"],
        "text": row["text"],
        "user": session["user_id"],
        "comment": row.get("comment"),
        "reply": row.get("reply"),
        "users": {
            "username": session.get("username"),
            "nickname": None,
            "avatar_url": session.get("avatar_url"),
            "rank_info": rank_for_points(own_stats["points"], own_stats.get("godly", False))
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

    # Deleting a top-level comment takes its whole reply thread with it,
    # rather than leaving orphaned replies with no root to attach to.
    supabase.table("comments").delete().eq("comment", comment_id).execute()
    supabase.table("comments").delete().eq("id", comment_id).execute()
    return jsonify({"success": True})


@app.route("/api/getlikes/<story>")
def get_likes(story):
    """Returns the like count for a story, and whether the current user
    (if logged in) has liked it."""
    result = supabase.table("likes_story").select("story", count="exact").eq("story", story).execute()
    count = result.count or 0

    liked = False
    if "user_id" in session:
        liked_result = (
            supabase.table("likes_story")
            .select("story")
            .eq("story", story)
            .eq("user", session["user_id"])
            .execute()
        )
        liked = bool(liked_result.data)

    return jsonify({"count": count, "liked": liked})


@app.route("/api/likestory/<story>", methods=["POST"])
def like_story(story):
    """Toggles the current user's like on a story - likes it if they
    hadn't, unlikes it if they had."""
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    story_result = supabase.table("stories").select("id").eq("id", story).execute()
    if not story_result.data:
        return jsonify({"error": "Story not found"}), 404

    existing = (
        supabase.table("likes_story")
        .select("story")
        .eq("story", story)
        .eq("user", session["user_id"])
        .execute()
    )

    if existing.data:
        supabase.table("likes_story").delete().eq("story", story).eq("user", session["user_id"]).execute()
        liked = False
    else:
        supabase.table("likes_story").insert({
            "user": session["user_id"],
            "story": story
        }).execute()
        liked = True

    count_result = supabase.table("likes_story").select("story", count="exact").eq("story", story).execute()
    count = count_result.count or 0

    return jsonify({"liked": liked, "count": count})


@app.route("/api/getfavorite/<story>")
def get_favorite_status(story):
    """Whether the current user has favorited this story, plus the
    total favorite count. Logged-out users just get the count."""
    count_result = supabase.table("favorites").select("story", count="exact").eq("story", story).execute()
    count = count_result.count or 0

    favorited = False
    if "user_id" in session:
        favorited_result = (
            supabase.table("favorites")
            .select("story")
            .eq("story", story)
            .eq("user", session["user_id"])
            .execute()
        )
        favorited = bool(favorited_result.data)

    return jsonify({"count": count, "favorited": favorited})


@app.route("/api/favoritestory/<story>", methods=["POST"])
def favorite_story(story):
    """Toggles the current user's favorite on a story - favorites it if
    they hadn't, unfavorites it if they had. Mirrors like_story."""
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    story_result = supabase.table("stories").select("id").eq("id", story).execute()
    if not story_result.data:
        return jsonify({"error": "Story not found"}), 404

    existing = (
        supabase.table("favorites")
        .select("story")
        .eq("story", story)
        .eq("user", session["user_id"])
        .execute()
    )

    if existing.data:
        supabase.table("favorites").delete().eq("story", story).eq("user", session["user_id"]).execute()
        favorited = False
    else:
        supabase.table("favorites").insert({
            "user": session["user_id"],
            "story": story
        }).execute()
        favorited = True

    count_result = supabase.table("favorites").select("story", count="exact").eq("story", story).execute()
    count = count_result.count or 0

    return jsonify({"favorited": favorited, "count": count})


@app.route("/api/myfavorites")
def my_favorites():
    """Lists the current user's favorited stories, most recently
    favorited first - for a 'My Favorites' page."""
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    favorites_result = (
        supabase.table("favorites")
        .select("story, created_at")
        .eq("user", session["user_id"])
        .order("created_at", desc=True)
        .execute()
    )
    favorite_rows = favorites_result.data or []
    story_ids = [row["story"] for row in favorite_rows]

    if not story_ids:
        return jsonify({"stories": []})

    stories_result = supabase.table("stories").select("*").in_("id", story_ids).execute()
    stories_by_id = {s["id"]: s for s in (stories_result.data or [])}
    stories = attach_like_counts([stories_by_id[sid] for sid in story_ids if sid in stories_by_id])

    return jsonify({"stories": stories})


@app.route("/api/getbookmark/<story>")
def get_bookmark_status(story):
    """Whether the current user has bookmarked a specific chapter of
    this story. Pass ?chapter=N (required)."""
    if "user_id" not in session:
        return jsonify({"bookmarked": False})

    chapter = request.args.get("chapter")
    try:
        chapter = int(chapter)
    except (TypeError, ValueError):
        return jsonify({"error": "Chapter must be a whole number"}), 400

    result = (
        supabase.table("bookmarks")
        .select("story")
        .eq("story", story)
        .eq("chapter", chapter)
        .eq("user", session["user_id"])
        .execute()
    )
    return jsonify({"bookmarked": bool(result.data)})


@app.route("/api/getstorybookmarks/<story>")
def get_story_bookmarks(story):
    """All of the current user's bookmarked chapter numbers within this
    one story, sorted ascending - for the bookmarked-chapters list on
    the story page. Lighter than /api/mybookmarks, which joins in full
    story data across every story."""
    if "user_id" not in session:
        return jsonify({"chapters": []})

    result = (
        supabase.table("bookmarks")
        .select("chapter")
        .eq("story", story)
        .eq("user", session["user_id"])
        .execute()
    )
    chapters = sorted({row["chapter"] for row in (result.data or [])})
    return jsonify({"chapters": chapters})


@app.route("/api/togglebookmark/<story>", methods=["POST"])
def toggle_bookmark(story):
    """Toggles the current user's bookmark on a specific chapter -
    bookmarks it if they hadn't, un-bookmarks it if they had. Unlike
    favorites/likes, a user can hold several bookmarks per story since
    each one targets a different chapter."""
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    data = request.get_json(silent=True) or {}
    chapter = data.get("chapter")
    try:
        chapter = int(chapter)
    except (TypeError, ValueError):
        return jsonify({"error": "Chapter must be a whole number"}), 400
    if chapter < 1:
        return jsonify({"error": "Chapter must be positive"}), 400

    story_result = supabase.table("stories").select("id").eq("id", story).execute()
    if not story_result.data:
        return jsonify({"error": "Story not found"}), 404

    existing = (
        supabase.table("bookmarks")
        .select("story")
        .eq("story", story)
        .eq("chapter", chapter)
        .eq("user", session["user_id"])
        .execute()
    )

    if existing.data:
        (
            supabase.table("bookmarks")
            .delete()
            .eq("story", story)
            .eq("chapter", chapter)
            .eq("user", session["user_id"])
            .execute()
        )
        bookmarked = False
    else:
        supabase.table("bookmarks").insert({
            "user": session["user_id"],
            "story": story,
            "chapter": chapter
        }).execute()
        bookmarked = True

    return jsonify({"bookmarked": bookmarked})


@app.route("/api/mybookmarks")
def my_bookmarks():
    """Lists the current user's bookmarked chapters, most recently
    bookmarked first - for a 'My Bookmarks' page. A story can appear
    more than once if multiple chapters of it are bookmarked."""
    if "user_id" not in session:
        return jsonify({"error": "Must be logged in"}), 401

    bookmarks_result = (
        supabase.table("bookmarks")
        .select("story, chapter, created_at")
        .eq("user", session["user_id"])
        .order("created_at", desc=True)
        .execute()
    )
    bookmark_rows = bookmarks_result.data or []

    story_ids = list({row["story"] for row in bookmark_rows})
    stories_by_id = {}
    if story_ids:
        stories_result = supabase.table("stories").select("*").in_("id", story_ids).execute()
        stories_by_id = {s["id"]: s for s in (stories_result.data or [])}

    bookmarks = []
    for row in bookmark_rows:
        story_row = stories_by_id.get(row["story"])
        if not story_row:
            continue
        bookmarks.append({
            "chapter": row["chapter"],
            "created_at": row["created_at"],
            "story": story_row
        })

    return jsonify({"bookmarks": bookmarks})


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

    # Comments and likes aren't guaranteed to cascade-delete with the
    # story, so clear them out explicitly before removing the story row.
    supabase.table("comments").delete().eq("story", story).execute()
    supabase.table("likes_story").delete().eq("story", story).execute()
    supabase.table("stories").delete().eq("id", story).execute()

    return jsonify({"success": True})


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)
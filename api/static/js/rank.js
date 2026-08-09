// Renders the writer rank badge shown on profile pages. Mirrors the
// point/threshold logic that lives server-side in api/index.py
// (RANKS / rank_for_points / compute_writer_points) - this file only
// handles turning the `rank_info` an API response already computed
// into markup, it doesn't calculate points itself.

const RANK_COLORS = {
    "Newcomer": "#9e9e9e",
    "Aspiring Writer": "#8d6e63",
    "World Builder": "#43a047",
    "The Storyteller": "#1e88e5",
    "Lore Sculptor": "#5e35b1",
    "Wordsmith": "#00897b",
    "The Specialist": "#f4511e",
    "The Plot Bender": "#d81b60",
    "Genre-Defining": "#3949ab",
    "NCGS Icon": "linear-gradient(135deg, #ffd54f, #ff8f00)"
};

// Avatar special effect per rank - escalates with rank, matching the CSS
// classes defined in style.css. Aspiring Writer (and the implicit
// Newcomer tier below it) intentionally get no effect.
const RANK_EFFECTS = {
    "Newcomer": "rank-fx-none",
    "Aspiring Writer": "rank-fx-none",
    "World Builder": "rank-fx-ring",
    "The Storyteller": "rank-fx-glow",
    "Lore Sculptor": "rank-fx-pulse",
    "Wordsmith": "rank-fx-spin-ring",
    "The Specialist": "rank-fx-sparkle",
    "The Plot Bender": "rank-fx-aurora",
    "Genre-Defining": "rank-fx-radiant",
    "NCGS Icon": "rank-fx-heavenly"
};

const ORBIT_LETTERS = ["N", "C", "G", "S"];
const ORBIT_PERIOD_SECONDS = 7;

// Ink-stroke glyphs that "write themselves" around a Wordsmith avatar,
// paired with the rank-transcribe keyframe in style.css.
const TRANSCRIBE_GLYPHS = ["\u270E", "\u2727", "\u2E3B", "\u00B7"];

// Generic placeholder avatar (a plain grey head-and-shoulders icon),
// used whenever a writer has no avatar_url set or their image 404s.
const DEFAULT_AVATAR =
    "data:image/svg+xml;utf8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">' +
        '<circle cx="20" cy="20" r="20" fill="%23ccc"/>' +
        '<circle cx="20" cy="16" r="7" fill="%23fff"/>' +
        '<path d="M6 36c0-9 6-14 14-14s14 5 14 14" fill="%23fff"/>' +
        '</svg>'
    );

function escapeRankHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
}

/**
 * Renders a rank badge + progress-to-next-rank bar into `container`.
 * `rankInfo` is the `rank_info` object returned by /api/getprofile or
 * /api/getuserprofile/<username>:
 *   { points, rank, rank_threshold, next_rank, next_rank_threshold,
 *     points_to_next_rank, likes_received, comments_received }
 */
function renderRankBadge(container, rankInfo) {
    if (!container || !rankInfo) return;

    const color = RANK_COLORS[rankInfo.rank] || "#9e9e9e";
    const atTopRank = !rankInfo.next_rank;

    const span = Math.max(1, rankInfo.next_rank_threshold - rankInfo.rank_threshold);
    const earnedInSpan = rankInfo.points - rankInfo.rank_threshold;
    const progressPct = atTopRank
        ? 100
        : Math.max(0, Math.min(100, (earnedInSpan / span) * 100));

    const progressLabel = atTopRank
        ? `${rankInfo.points} pts &middot; top rank reached`
        : `${rankInfo.points} / ${rankInfo.next_rank_threshold} pts to ${escapeRankHtml(rankInfo.next_rank)}`;

    container.innerHTML = `
        <div class="rank-badge">
            <span class="rank-pill" style="background:${color}" title="${rankInfo.points} points">${escapeRankHtml(rankInfo.rank)}</span>
            <div class="rank-progress-wrap">
                <div class="rank-progress-track">
                    <div class="rank-progress-fill" style="width:${progressPct}%; background:${color}"></div>
                </div>
                <div class="rank-progress-label">${progressLabel}</div>
            </div>
        </div>
    `;
}

/**
 * Appends `count` small particle elements to `wrapEl`, evenly spaced
 * around the circle (with a slight per-particle offset so fields don't
 * look mechanically uniform) and staggered in time. Every particle gets
 * the shared "rank-fx-gen" marker class (for easy cleanup) plus
 * `className`, and CSS custom properties (--fx-angle, --fx-delay,
 * --fx-radius, --fx-size) that the corresponding style.css rules read.
 */
function spawnParticles(wrapEl, count, className, opts) {
    opts = opts || {};
    const radius = opts.radius || "-34px";
    const size = opts.size || null;
    const period = opts.period || 2.4;
    const angleJitter = opts.angleJitter || 6;
    const text = opts.text || null;

    for (let i = 0; i < count; i++) {
        const el = document.createElement("span");
        el.className = `rank-fx-gen ${className}`;
        const baseAngle = (360 / count) * i;
        const jitter = Math.sin(i * 2.4) * angleJitter;
        el.style.setProperty("--fx-angle", `${(baseAngle + jitter).toFixed(1)}deg`);
        el.style.setProperty("--fx-radius", radius);
        el.style.setProperty("--fx-delay", `${((i / count) * period).toFixed(2)}s`);
        if (size) el.style.setProperty("--fx-size", size);
        if (typeof opts.length === "string") el.style.setProperty("--fx-length", opts.length);
        if (text) el.textContent = Array.isArray(text) ? text[i % text.length] : text;
        wrapEl.appendChild(el);
    }
}

/**
 * Applies the rank-appropriate special effect to an avatar. `wrapEl` is
 * the `.avatar-fx-wrap` element surrounding the `<img>` (or fallback
 * circle). Clears any previously-applied effect/particles first, so this
 * is safe to call again if the rank changes or the page re-renders.
 *
 * Effects escalate in detail with rank: World Builder's slim ring is the
 * simplest, each subsequent rank layers on more (facets, orbiting
 * glyphs, sparks, motes, rays), and NCGS Icon - the top rank - combines
 * a deeply blurred multi-stop halo, a rotating glow ring, a full field
 * of radiant rays, drifting gold stardust, and "NCGS" orbiting overhead.
 */
function applyAvatarEffect(wrapEl, rankName) {
    if (!wrapEl) return;

    Object.values(RANK_EFFECTS).forEach(cls => wrapEl.classList.remove(cls));
    wrapEl.querySelectorAll(".rank-fx-gen, .orbit-letter").forEach(el => el.remove());

    const effectClass = RANK_EFFECTS[rankName] || "rank-fx-none";
    wrapEl.classList.add(effectClass);

    switch (effectClass) {
        case "rank-fx-pulse":
            // Lore Sculptor - chisel marks standing around the carved ring.
            spawnParticles(wrapEl, 6, "fx-carve", { radius: "-36px", period: 2.6, angleJitter: 4 });
            break;

        case "rank-fx-spin-ring":
            // Wordsmith - ink glyphs orbiting as though being transcribed.
            spawnParticles(wrapEl, 4, "fx-glyph", { radius: "30px", period: 6.4, angleJitter: 0, text: TRANSCRIBE_GLYPHS });
            break;

        case "rank-fx-sparkle":
            // The Specialist - a small twinkling field beyond the base spark.
            spawnParticles(wrapEl, 4, "fx-spark", { radius: "-34px", period: 1.8, size: "4px" });
            break;

        case "rank-fx-aurora":
            // The Plot Bender - drifting motes caught in the aurora's pull.
            spawnParticles(wrapEl, 3, "fx-mote fx-mote--bender", { radius: "-30px", period: 3.6, size: "4px" });
            break;

        case "rank-fx-radiant": {
            // Genre-Defining - a slowly turning field of light rays plus stardust.
            const rayField = document.createElement("div");
            rayField.className = "rank-fx-gen fx-ray-field";
            wrapEl.appendChild(rayField);
            spawnParticles(rayField, 6, "fx-ray fx-ray--radiant", { period: 3, angleJitter: 0, length: "24px" });
            spawnParticles(wrapEl, 4, "fx-mote fx-mote--radiant", { radius: "-30px", period: 3.6, size: "4px" });
            break;
        }

        case "rank-fx-heavenly": {
            // NCGS Icon - every layer above, at full intensity, plus a
            // dedicated blurred core for genuine "heavenly" softness.
            const halo = document.createElement("span");
            halo.className = "rank-fx-gen fx-halo-core";
            wrapEl.appendChild(halo);

            const rayField = document.createElement("div");
            rayField.className = "rank-fx-gen fx-ray-field";
            wrapEl.appendChild(rayField);
            spawnParticles(rayField, 8, "fx-ray fx-ray--heavenly", { period: 3, angleJitter: 0, length: "30px" });

            spawnParticles(wrapEl, 6, "fx-mote fx-mote--heavenly", { radius: "-38px", period: 3.6, size: "4px" });

            ORBIT_LETTERS.forEach((letter, i) => {
                const span = document.createElement("span");
                span.className = "rank-fx-gen orbit-letter";
                span.textContent = letter;
                span.style.animationDelay = `-${(ORBIT_PERIOD_SECONDS / ORBIT_LETTERS.length) * i}s`;
                wrapEl.appendChild(span);
            });
            break;
        }
    }
}

/**
 * Builds a ready-to-insert avatar element - `.avatar-fx-slot` wrapping
 * `.avatar-fx-wrap` wrapping an `<img>` - with the rank effect for
 * `writer.rank_info.rank` already applied. This is the shared building
 * block behind avatars on story cards and comments, so both get a real
 * avatar_url image plus the same escalating effect used on profile
 * pages, instead of each template reinventing its own markup.
 *
 * `writer` - any object with `avatar_url` and (ideally) a `rank_info`
 *   object like the one /api/getstories, /api/searchstories, and
 *   /api/getcomments now attach (falls back to no effect if absent).
 * `opts.sizeClass` - a compact-size modifier from style.css, e.g.
 *   "avatar-fx-slot--card" or "avatar-fx-slot--comment". Omit for the
 *   full 64px size used on profile pages.
 * `opts.imgClass` - CSS class for the `<img>` itself (defaults to
 *   "avatar-fx-img"); pass the page's existing avatar class to inherit
 *   its object-fit/background styling.
 */
function buildAvatarFx(writer, opts) {
    opts = opts || {};
    writer = writer || {};

    const slot = document.createElement("div");
    slot.className = "avatar-fx-slot" + (opts.sizeClass ? ` ${opts.sizeClass}` : "");

    const wrap = document.createElement("div");
    wrap.className = "avatar-fx-wrap";
    slot.appendChild(wrap);

    const img = document.createElement("img");
    img.className = opts.imgClass || "avatar-fx-img";
    img.alt = "";
    img.src = writer.avatar_url || DEFAULT_AVATAR;
    // Falls back if avatar_url is set but broken/unreachable.
    img.addEventListener("error", () => {
        img.src = DEFAULT_AVATAR;
    }, { once: true });
    wrap.appendChild(img);

    applyAvatarEffect(wrap, (writer.rank_info && writer.rank_info.rank) || "Newcomer");

    return slot;
}
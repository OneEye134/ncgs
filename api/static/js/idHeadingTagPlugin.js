/**
 * idHeadingTagPlugin - a Toast UI Editor plugin that keeps chapter
 * headings compatible with this site's [id][md] tag format, e.g.
 *
 *   [phase-1-the-horror-begins]## Phase 1: The Horror Begins
 *
 * Writers never see or type the [id] tag themselves - it's derived
 * automatically from each heading's own text, the same way the server
 * derives it on save (see tag_headings()/slugify() in index.py - the
 * slug logic here is kept in sync with that).
 *
 * WHAT THIS PLUGIN DOES:
 *   toMarkdownRenderers.heading fires whenever the editor converts its
 *   WYSIWYG (rich text) document back into markdown text - which is
 *   what happens when a writer types headings in WYSIWYG mode, then
 *   the app calls editor.getMarkdown() to save. Without this plugin,
 *   headings written in WYSIWYG mode would save with no [id] tag at
 *   all (unlike Markdown mode, which is caught by the server's
 *   tag_headings() as a fallback either way).
 *
 * WHAT THIS PLUGIN DOES NOT DO:
 *   Toast UI's Markdown mode edits raw text directly through CodeMirror
 *   and never runs toMarkdownRenderers - so for Markdown-mode editing,
 *   tagging still happens the way it already did: the server re-derives
 *   every tag from scratch on save. That's intentional and stays the
 *   final source of truth either way, so a slug mismatch between the
 *   client and server slugify() can never produce a broken/duplicate
 *   tag - the server's tag always wins in the saved file.
 */
(function (global) {
    function slugify(text) {
        return (text || '')
            .trim()
            .toLowerCase()
            .replace(/[^\w\s-]/g, '')
            .replace(/[\s_]+/g, '-')
            .replace(/-+/g, '-')
            .replace(/^-|-$/g, '') || 'section';
    }

    function idHeadingTagPlugin() {
        return {
            toMarkdownRenderers: {
                heading(nodeInfo) {
                    const level = (nodeInfo.node.attrs && nodeInfo.node.attrs.level) || 1;
                    const text = (nodeInfo.node.textContent || '').trim();
                    const slug = slugify(text);
                    return { delim: `[${slug}]${'#'.repeat(level)} ` };
                }
            }
        };
    }

    global.idHeadingTagPlugin = idHeadingTagPlugin;
    global.idHeadingTagSlugify = slugify;
})(window);

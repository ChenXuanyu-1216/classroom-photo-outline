(() => {
    // The server can reject files and renumber accepted pages. Thumbnail array
    // indexes are therefore never a source-page identifier.
    function thumbnailForPage(thumbnails, page, pages) {
        if (!Array.isArray(thumbnails) || !Array.isArray(pages) || typeof page?.filename !== 'string') return null;
        if (pages.filter(item => item.filename === page.filename).length !== 1) return null;
        const matches = thumbnails.filter(item => item?.filename === page.filename);
        if (matches.length !== 1 || typeof matches[0].src !== 'string' || !matches[0].src.startsWith('data:image/jpeg;')) return null;
        return matches[0];
    }
    globalThis.ClassroomBookSources = {thumbnailForPage};
})();

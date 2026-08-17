# foliate-js vendored runtime

- Repository: https://github.com/johnfactotum/foliate-js
- Commit: `78914aefbb1351545fe60e4cdbabcce514d1f201`
- Snapshot date: 2026-06-30
- License: LGPL-3.0-or-later (`LICENSE`)

Only the browser runtime required for EPUB/KEPUB, FB2/FBZ, MOBI/AZW/AZW3, and CBZ is vendored. The PDF branch was removed from `view.js`, publication iframes drop `allow-scripts`, and paginated grid rows fill the viewport instead of vertically centring short sections; Calibre-Web NextGen continues to use its existing PDF viewer. Do not update this directory without recording a new exact upstream commit and rerunning the reader acceptance suite.

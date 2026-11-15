/* Ouranòs-Pro — projector screen.
 *
 * Receives state snapshots over WebSocket and renders the live verse:
 *  - auto-fits the font size to the text,
 *  - splits very long passages into pages,
 *  - fades between verses,
 *  - reconnects by itself if the connection drops.
 *
 * URL options:
 *   /display?bg=transparent   transparent background (OBS browser source)
 *   /display?bg=green         green-screen background (chroma key)
 *   /display?preview=1        used by the operator's small live preview
 */
(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const isPreview = params.has("preview");
  const bgMode = params.get("bg");
  if (bgMode === "transparent" || bgMode === "green") document.body.dataset.bg = bgMode;

  const stage = document.getElementById("stage");
  const stageContent = stage.querySelector(".content");
  const measure = document.getElementById("measure");
  const measureContent = measure.querySelector(".content");
  const pager = document.getElementById("pager");
  const FADE_MS = 380;

  let state = null;        // last snapshot from the server
  let layout = null;       // { key, pages: [[verseNo,...]], fontPx }
  let shownKey = "";       // what is currently painted (to avoid needless fades)
  let renderToken = 0;
  let socket = null;

  // ---------------------------------------------------------------- building
  /** Build the DOM for the given verse numbers (one column per translation). */
  function build(target, panels, verseNumbers, showNumbers) {
    target.textContent = "";
    target.classList.toggle("two", panels.length > 1);
    const wanted = new Set(verseNumbers);
    const multi = verseNumbers.length > 1;
    for (const panel of panels) {
      const verses = panel.verses.filter((v) => wanted.has(v.verse));
      if (!verses.length) continue;
      const box = document.createElement("div");
      box.className = "panel";
      box.lang = panel.language;

      const text = document.createElement("div");
      text.className = "verse-text";
      verses.forEach((v, i) => {
        if (showNumbers && multi) {
          const sup = document.createElement("sup");
          sup.textContent = v.verse;
          text.appendChild(sup);
        }
        text.appendChild(document.createTextNode(v.text + (i < verses.length - 1 ? " " : "")));
      });

      // Reference for exactly the verses on this page, e.g. "John 3:16-17 — KJV"
      const base = panel.reference.replace(/:\d+(-\d+)?$/, "");
      const first = verses[0].verse, last = verses[verses.length - 1].verse;
      const ref = document.createElement("div");
      ref.className = "reference";
      ref.appendChild(document.createTextNode(`${base}:${first}${last > first ? "-" + last : ""}`));
      const tr = document.createElement("span");
      tr.className = "translation";
      tr.textContent = ` — ${panel.translation}`;
      ref.appendChild(tr);

      box.append(text, ref);
      target.appendChild(box);
    }
  }

  // ----------------------------------------------------------------- fitting
  function availableHeight() {
    const cs = getComputedStyle(measure);
    return measure.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
  }

  function fits(panels, verseNumbers, showNumbers, fontPx) {
    build(measureContent, panels, verseNumbers, showNumbers);
    measureContent.style.fontSize = fontPx + "px";
    return measureContent.scrollHeight <= availableHeight() + 1 &&
           measureContent.scrollWidth <= measureContent.clientWidth + 1;
  }

  /** Largest font size in [lo, hi] at which the verses fit (or lo if none do). */
  function bestFont(panels, verseNumbers, showNumbers, lo, hi) {
    if (fits(panels, verseNumbers, showNumbers, hi)) return hi;
    for (let i = 0; i < 9; i++) {
      const mid = (lo + hi) / 2;
      if (fits(panels, verseNumbers, showNumbers, mid)) lo = mid; else hi = mid;
    }
    return lo;
  }

  /** Decide pages and font size for the whole passage. */
  function computeLayout(panels, settings) {
    const vh = window.innerHeight / 100;
    const scale = settings.font_scale || 1;
    const maxFont = 8.6 * vh * scale;   // short verses: big
    const minFont = 4.3 * vh * scale;   // below this we start a new page instead
    const floorFont = 2.3 * vh;         // a single huge verse may shrink down to this
    const showNumbers = settings.show_verse_numbers;

    const numbers = [...new Set(panels.flatMap((p) => p.verses.map((v) => v.verse)))].sort((a, b) => a - b);
    if (!numbers.length) return { pages: [], fontPx: maxFont };

    // Everything on one page?
    if (fits(panels, numbers, showNumbers, minFont)) {
      return { pages: [numbers], fontPx: bestFont(panels, numbers, showNumbers, minFont, maxFont) };
    }
    // Otherwise fill pages verse by verse at the minimum readable size.
    const pages = [];
    let current = [];
    for (const n of numbers) {
      if (current.length && !fits(panels, [...current, n], showNumbers, minFont)) {
        pages.push(current);
        current = [];
      }
      current.push(n);
    }
    if (current.length) pages.push(current);
    // One common size for all pages so the text does not jump between pages.
    let fontPx = maxFont;
    for (const page of pages) {
      const low = fits(panels, page, showNumbers, minFont) ? minFont : floorFont;
      fontPx = Math.min(fontPx, bestFont(panels, page, showNumbers, low, maxFont));
    }
    return { pages, fontPx };
  }

  // --------------------------------------------------------------- rendering
  function contentKey(s) {
    return JSON.stringify([
      s.panels.map((p) => [p.translation, p.reference]),
      s.settings.font_scale, s.settings.show_verse_numbers,
      window.innerWidth, window.innerHeight,
    ]);
  }

  function render() {
    if (!state) return;
    const s = state;
    document.body.dataset.theme = s.settings.theme;
    const image = s.settings.background_image;
    if (image) {
        document.body.dataset.image = "1";
        document.body.style.setProperty("--image", `url("/backgrounds/${encodeURIComponent(image)}")`);
    } else {
        delete document.body.dataset.image;
        document.body.style.removeProperty("--image");
    }
    document.body.classList.toggle("is-black", s.screen === "black");

    const showing = s.screen !== "clear" && s.panels.length > 0;
    if (!showing) {
      layout = null;
      shownKey = "";
      renderToken++;
      stage.classList.add("is-hidden");
      pager.textContent = "";
      return;
    }

    // (Re)compute the layout only when the passage or the screen size changed.
    const key = contentKey(s);
    if (!layout || layout.key !== key) {
      layout = { key, ...computeLayout(s.panels, s.settings) };
      if (!isPreview) send({ type: "pages", count: layout.pages.length });
    }
    const pageIndex = Math.min(s.page, layout.pages.length - 1);
    const paintKey = key + "#" + pageIndex;
    if (paintKey === shownKey) { stage.classList.remove("is-hidden"); return; }

    const paint = () => {
      build(stageContent, s.panels, layout.pages[pageIndex], s.settings.show_verse_numbers);
      stageContent.style.fontSize = layout.fontPx + "px";
      pager.textContent = layout.pages.length > 1 ? `${pageIndex + 1} / ${layout.pages.length}` : "";
      shownKey = paintKey;
      stage.classList.remove("is-hidden");
    };

    const token = ++renderToken;
    if (stage.classList.contains("is-hidden") && !shownKey) {
      paint();                                  // nothing on screen: just fade in
    } else {
      stage.classList.add("is-hidden");         // fade out, swap, fade in
      setTimeout(() => { if (token === renderToken) paint(); }, FADE_MS);
    }
  }

  // --------------------------------------------------------------- websocket
  function send(message) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws`);
    socket.onopen = () => {
      send({ type: "hello", role: isPreview ? "preview" : "display" });
      if (layout && !isPreview) send({ type: "pages", count: layout.pages.length });
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "state") { state = message; render(); }
    };
    // Keep the last verse on screen and retry quietly until the server is back.
    socket.onclose = () => setTimeout(connect, 1500);
    socket.onerror = () => socket.close();
  }

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { layout = null; shownKey = ""; render(); }, 150);
  });

  // Double-click toggles full screen (handy when not launched in kiosk mode).
  if (!isPreview) {
    document.addEventListener("dblclick", () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen().catch(() => {});
    });
  }

  connect();
})();

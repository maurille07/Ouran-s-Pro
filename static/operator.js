/* Ouranòs-Pro — operator control panel.
 *
 * Talks to the server with small REST calls (search / show / listen ...) and
 * listens on a WebSocket for:
 *   "state"       full shared state (projector, detections queue, settings, listening status)
 *   "transcript"  one line of live transcript (partial or final)
 *   "level"       microphone level, ~10 times a second
 *   "listen"      listening status changes
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    search: $("search"), results: $("results"), selected: $("selected"),
    show: $("btn-show"), prev: $("btn-prev"), next: $("btn-next"),
    clear: $("btn-clear"), black: $("btn-black"),
    serverPill: $("server-pill"), displayPill: $("display-pill"), banner: $("banner"),
    liveBadge: $("live-badge"), liveRef: $("live-ref"),
    pager: $("pager"), pageLabel: $("page-label"), pagePrev: $("btn-page-prev"), pageNext: $("btn-page-next"),
    segTranslation: $("seg-translation"), segTheme: $("seg-theme"),
    fontScale: $("font-scale"), fontScaleLabel: $("font-scale-label"), verseNumbers: $("verse-numbers"),
    toast: $("toast"),
    tabs: $("tabs"), tabTranscript: $("tab-transcript"), tabDetect: $("tab-detect"),
    searchBox: $("searchbox"), searchPop: $("search-pop"),
    listenLabel: $("btn-listen-label"), listenIcon: $("btn-listen-icon"), showLabel: $("btn-show-label"),
    segUi: $("seg-ui"),
    detectText: $("detect-text"), detectButton: $("btn-detect"),
    detectMarked: $("detect-marked"), detections: $("detections"),
    // listening
    listen: $("btn-listen"), listenBadge: $("listen-badge"), listenMessage: $("listen-message"),
    mic: $("mic"), level: $("level"), transcript: $("transcript"),
    // queue
    queue: $("queue"), queueCount: $("queue-count"), queueClear: $("btn-queue-clear"),
    // settings
    settings: $("settings"), settingsButton: $("btn-settings"),
    segModel: $("seg-model"), segLanguage: $("seg-language"), modelHint: $("model-hint"),
    dedupe: $("dedupe"), dedupeLabel: $("dedupe-label"),
    quotedOn: $("quoted-on"), quotedThreshold: $("quoted-threshold"),
    quotedThresholdLabel: $("quoted-threshold-label"), quotedHint: $("quoted-hint"),
    segMode: $("seg-mode"), autoNote: $("auto-note"),
    background: $("background"), backgroundAdd: $("btn-background-add"), backgroundFile: $("background-file"),
  };

  const DEFAULT_LISTEN_HINT = "Choose the microphone (or the mixer's USB input), then start.";

  let state = null;          // last snapshot from the server
  let results = [];          // current search results
  let activeIndex = -1;      // highlighted search result
  let selected = null;       // { book_id, chapter, verse_start, verse_end, panels, queueId? }
  let selectedAuto = false;  // true when the selection was made automatically from a new detection
  let searchSeq = 0;
  let newestSeenId = 0;      // highest detection id already displayed
  let models = {};           // { base: true, small: false, ... } downloaded speech models
  let slowWarned = false;

  // -------------------------------------------------------------------- utils
  function toast(message) {
    el.toast.textContent = message;
    el.toast.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.toast.hidden = true; }, 4500);
  }

  /** Call the API. Returns parsed JSON, or null after showing the error. */
  async function api(path, body) {
    try {
      const response = await fetch(path, body === undefined ? {} : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        toast(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
        return null;
      }
      return data;
    } catch (error) {
      toast("Cannot reach Ouranòs-Pro. Is it still running?");
      return null;
    }
  }

  const refOf = (x) => ({
    book_id: x.book_id, chapter: x.chapter, verse_start: x.verse_start, verse_end: x.verse_end,
  });
  const sameRef = (a, b) => !!a && !!b && a.book_id === b.book_id && a.chapter === b.chapter &&
    a.verse_start === b.verse_start && a.verse_end === b.verse_end;
  const isLive = () => !!state && state.screen === "verse" && sameRef(state.ref, selected);
  const queueItem = (id) => (state && id != null ? state.queue.find((q) => q.id === id) : undefined);

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /** An icon from the SVG sprite in operator.html, e.g. icon("left"). */
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  /** Fill a node with text where the given [start, end] spans are highlighted. */
  function fillMarked(node, text, spans) {
    node.textContent = "";
    let position = 0;
    for (const [start, end] of spans) {
      if (start < position) continue;
      node.appendChild(document.createTextNode(text.slice(position, start)));
      node.appendChild(make("mark", "", text.slice(start, end)));
      position = end;
    }
    node.appendChild(document.createTextNode(text.slice(position)));
  }

  // ------------------------------------------------------------------- search
  let searchTimer = 0;
  el.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 160);
  });

  async function runSearch() {
    const query = el.search.value.trim();
    const seq = ++searchSeq;
    if (!query) { results = []; activeIndex = -1; renderResults(""); return; }
    const data = await api("/api/search?q=" + encodeURIComponent(query));
    if (seq !== searchSeq || !data) return;   // a newer search is already running
    results = data.results;
    activeIndex = results.length ? 0 : -1;
    renderResults(query);
  }

  function renderResults(query) {
    el.results.textContent = "";
    el.searchPop.hidden = !query;              // the pop-up is open while there is something typed
    if (!results.length) {
      if (query) el.results.appendChild(make("li", "none", `No verse found for “${query}”.`));
      return;
    }
    results.forEach((r, i) => {
      const li = make("li", i === activeIndex ? "active" : "");
      const ref = make("div", "r-ref", r.reference);
      ref.appendChild(make("span", "r-tr", r.translation));
      li.append(ref, make("div", "r-text", r.text));
      // mousedown, not click: it must win against the blur that closes the pop-up
      li.addEventListener("mousedown", (event) => {
        event.preventDefault();
        activeIndex = i;
        select(r);
        closeSearch();
      });
      el.results.appendChild(li);
    });
  }

  function closeSearch() {
    el.searchPop.hidden = true;
    el.search.blur();
  }
  el.search.addEventListener("focus", () => { if (el.search.value.trim()) el.searchPop.hidden = false; });
  // Close when clicking anywhere else (not on blur: scrolling the results must not close them).
  document.addEventListener("mousedown", (event) => {
    if (!el.searchBox.contains(event.target)) el.searchPop.hidden = true;
  });

  function highlightActive() {
    [...el.results.children].forEach((li, i) => {
      li.classList.toggle("active", i === activeIndex);
      if (i === activeIndex) li.scrollIntoView({ block: "nearest" });
    });
  }

  el.search.addEventListener("keydown", async (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!results.length) return;
      event.preventDefault();
      activeIndex = (activeIndex + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
      highlightActive();
      select(results[activeIndex]);
    } else if (event.key === "Enter") {
      event.preventDefault();
      clearTimeout(searchTimer);
      await runSearch();                      // make sure results match what was typed
      if (activeIndex >= 0) { await select(results[activeIndex]); await showSelected(); closeSearch(); }
    } else if (event.key === "Escape") {
      closeSearch();
    }
  });

  // ------------------------------------------- test detection (paste text box)
  el.tabs.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tab]");
    if (!button) return;
    const detect = button.dataset.tab === "detect";
    for (const b of el.tabs.querySelectorAll("button")) {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-selected", String(b === button));
    }
    el.tabTranscript.hidden = detect;
    el.tabDetect.hidden = !detect;
    if (detect) el.detectText.focus();
  });

  /** Send the pasted text to the reference detector and list what it found. */
  async function runDetect() {
    const text = el.detectText.value;
    if (!text.trim()) { toast("Paste or type some text first."); return; }
    const data = await api("/api/detect", { text });
    if (!data) return;
    const found = data.detections;

    fillMarked(el.detectMarked, text, found.map((d) => [d.start, d.end]));
    el.detectMarked.hidden = false;

    el.detections.textContent = "";
    if (!found.length) {
      el.detections.appendChild(make("li", "none", "No Bible reference detected in this text."));
      return;
    }
    for (const d of found) {
      const li = make("li");
      const kind = make("span", "r-kind", d.kind === "followup" ? "follow-up"
        : d.kind === "quoted" ? `quoted ${Math.round(d.confidence * 100)}%` : "direct");
      const ref = make("div", "r-ref", d.reference);
      ref.appendChild(make("span", "r-tr", d.translation + (d.whole_chapter ? " · whole chapter, starts at verse 1" : "")));
      li.append(kind, ref, make("div", "r-heard", "“" + d.matched + "”"), make("div", "r-text", d.text));
      li.addEventListener("click", () => {
        for (const other of el.detections.children) other.classList.toggle("active", other === li);
        select(d);
      });
      li.addEventListener("dblclick", () => select(d).then(showSelected));
      el.detections.appendChild(li);
    }
  }

  el.detectButton.addEventListener("click", runDetect);
  el.detectText.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); runDetect(); }
    else if (event.key === "Escape") el.detectText.blur();
  });

  // ----------------------------------------------------------------- selected
  /** Load a reference into the "Selected verse" preview. */
  async function select(ref, options = {}) {
    const data = await api("/api/passage", refOf(ref));
    if (data) {
      selected = data;
      selected.queueId = options.queueId;
      selectedAuto = !!options.auto;
      renderSelected();
      renderQueue();
    }
    return data;
  }

  function renderSelected() {
    el.selected.textContent = "";
    el.selected.classList.toggle("empty", !selected);
    el.selected.classList.toggle("is-live", isLive());
    el.showLabel.textContent = isLive() ? "On screen" : "Show";
    if (!selected) {
      el.selected.appendChild(make("p", "placeholder", "Click a detection or a search result to preview it here."));
      return;
    }
    for (const panel of selected.panels) {
      const box = make("div", "s-panel");
      const ref = make("div", "s-ref", panel.reference);
      ref.appendChild(make("small", "", panel.translation));
      const text = make("div", "s-text");
      text.lang = panel.language;
      panel.verses.forEach((v) => {
        if (panel.verses.length > 1) text.appendChild(make("sup", "", v.verse));
        text.appendChild(document.createTextNode(v.text + " "));
      });
      box.append(ref, text);
      el.selected.appendChild(box);
    }
  }

  // ------------------------------------------------------------------ actions
  async function showSelected() {
    if (!selected) { toast("Select a verse first."); return; }
    const item = queueItem(selected.queueId);
    if (item && sameRef(item, selected)) await api(`/api/queue/${item.id}/show`, {});
    else await api("/api/show", refOf(selected));
    selectedAuto = false;
  }

  /** Move to the next / previous verse. If a verse is on screen, the screen follows. */
  async function step(delta) {
    const base = selected || (state && state.ref);
    if (!base) { toast("Select a verse first."); return; }
    const followLive = !!state && state.screen === "verse";
    const data = await api(`/api/neighbor?delta=${delta}`, refOf(base));
    if (!data) return;
    selected = data;           // stepping detaches the selection from its queue card
    selectedAuto = false;
    renderSelected();
    renderQueue();
    if (followLive) await api("/api/show", refOf(selected));
  }

  const clearScreen = () => api("/api/clear", {});
  const blackScreen = () => api("/api/black", {});
  const turnPage = (delta) => api("/api/page", { delta });
  const saveSetting = (changes) => api("/api/settings", changes);

  el.show.addEventListener("click", showSelected);
  el.prev.addEventListener("click", () => step(-1));
  el.next.addEventListener("click", () => step(1));
  el.clear.addEventListener("click", clearScreen);
  el.black.addEventListener("click", blackScreen);
  el.pagePrev.addEventListener("click", () => turnPage(-1));
  el.pageNext.addEventListener("click", () => turnPage(1));

  // --------------------------------------------------------- detections queue
  function renderQueue() {
    if (!state) return;
    const queue = state.queue;
    const waiting = queue.filter((q) => !q.shown).length;
    el.queueCount.textContent = waiting ? `${waiting} new` : "";
    el.queueClear.hidden = !queue.length;
    el.queue.textContent = "";
    if (!queue.length) {
      el.queue.appendChild(make("li", "none",
        "Verses the preacher mentions will wait here. Press Show to put one on the projector."));
      return;
    }
    for (const item of queue) {
      const li = make("li", "q-item");
      li.classList.toggle("active", !!selected && selected.queueId === item.id);
      li.classList.toggle("shown", item.shown);
      li.classList.toggle("fresh", item.id > newestSeenId);

      const head = make("div", "q-head");
      head.append(make("span", "q-ref", item.reference), make("span", "q-tr", item.translation));
      const tags = make("span", "q-tags");
      if (item.shown) tags.appendChild(make("span", "tag shown", "shown"));
      const kind = make("span", item.kind === "quoted" ? "tag quoted" : "tag",
        item.kind === "quoted" ? `quoted ${Math.round(item.confidence * 100)}%` : item.followup ? "follow-up" : "direct");
      kind.title = item.kind === "quoted" ? "The verse was quoted or paraphrased"
        : item.followup ? "No book was named: it uses the chapter mentioned just before"
        : "The reference was spoken";
      tags.append(kind);
      head.appendChild(tags);

      const actions = make("div", "q-actions");
      const button = (content, title, className, handler) => {
        const b = make("button", "btn " + className);
        b.append(content);
        b.title = title;
        b.setAttribute("aria-label", title);
        b.addEventListener("click", (event) => { event.stopPropagation(); handler(); });
        actions.appendChild(b);
      };
      button("Show", "Show this verse on the projector", item.shown ? "" : "primary", async () => {
        await api(`/api/queue/${item.id}/show`, {});
        select(item, { queueId: item.id });
      });
      button(icon("left"), "Previous verse", "subtle icon", () => api(`/api/queue/${item.id}/step`, { delta: -1 }));
      button(icon("right"), "Next verse", "subtle icon", () => api(`/api/queue/${item.id}/step`, { delta: 1 }));
      actions.appendChild(make("span", "spacer"));
      button(icon("close"), "Dismiss", "subtle icon", () => {
        if (selected && selected.queueId === item.id) { selected = null; renderSelected(); }
        api(`/api/queue/${item.id}/dismiss`, {});
      });

      li.append(head, make("div", "q-heard", "Heard “" + item.heard + "”"),
        make("div", "q-text", (item.whole_chapter ? "Whole chapter, starting at verse 1 — " : "") + item.text), actions);
      li.addEventListener("click", () => select(item, { queueId: item.id }));
      el.queue.appendChild(li);
    }
  }

  /** After a new snapshot: follow new detections and keep the selection in step with its card. */
  function syncQueueSelection() {
    const queue = state.queue;
    // Auto mode put a new verse on the projector: follow it, so the arrow keys continue from there.
    const autoShown = queue.find((q) => q.id > newestSeenId && q.shown && sameRef(q, state.ref));
    if (autoShown) select(autoShown, { queueId: autoShown.id, auto: true });
    const newest = autoShown ? undefined : queue.find((q) => q.id > newestSeenId && !q.shown);
    // A new detection becomes the selection (ready for Space) unless the operator
    // picked something by hand and has not shown it yet.
    if (newest && (!selected || selectedAuto || isLive())) {
      select(newest, { queueId: newest.id, auto: true });
    } else if (selected && selected.queueId != null) {
      const item = queueItem(selected.queueId);
      if (!item) { selected.queueId = undefined; }
      else if (!sameRef(item, selected)) select(item, { queueId: item.id, auto: selectedAuto });  // ◀ ▶ on the card
    }
    renderQueue();
    for (const q of queue) newestSeenId = Math.max(newestSeenId, q.id);
  }

  el.queueClear.addEventListener("click", () => api("/api/queue/clear", {}));

  // ---------------------------------------------------------------- listening
  const isListening = () => !!state && ["loading", "listening"].includes(state.listen.status);

  async function loadDevices() {
    const data = await api("/api/audio/devices");
    if (!data) return;
    models = data.models;
    const wanted = data.selected;
    el.mic.textContent = "";
    el.mic.appendChild(new Option("System default microphone", ""));
    for (const device of data.devices) el.mic.appendChild(new Option(device.name, device.name));
    if (wanted && !data.devices.some((d) => d.name === wanted)) {
      el.mic.appendChild(new Option(wanted + " (not connected)", wanted));
    }
    el.mic.value = wanted;
    renderModels();
  }

  function renderModels() {
    if (!state) return;
    for (const button of el.segModel.querySelectorAll("button")) {
      button.disabled = models[button.dataset.value] === false;
    }
    const missing = Object.keys(models).filter((size) => !models[size]);
    el.modelHint.textContent = missing.length
      ? `Not downloaded: ${missing.join(", ")}. To add one, connect to the internet and run: python scripts/download_models.py --whisper ${missing[0]}`
      : "";
  }

  function renderListen() {
    const listen = state.listen;
    const on = isListening();
    el.listenLabel.textContent = on ? "Stop listening" : "Start listening";
    el.listenIcon.setAttribute("href", on ? "#i-stop" : "#i-mic");
    el.listen.classList.toggle("listening", on);
    el.listen.classList.toggle("primary", !on);
    el.mic.disabled = on;
    const labels = { stopped: "Stopped", loading: "Loading…", listening: "Listening", error: "Problem" };
    el.listenBadge.textContent = labels[listen.status] || listen.status;
    el.listenBadge.className = "badge " + listen.status;
    el.listenMessage.classList.toggle("error", listen.status === "error");
    if (listen.status === "error" || listen.status === "loading") el.listenMessage.textContent = listen.message;
    else if (listen.status === "listening" && !slowWarned) {
      el.listenMessage.textContent = `${listen.message} · model “${listen.model}”`;
    } else if (listen.status === "stopped") el.listenMessage.textContent = DEFAULT_LISTEN_HINT;
    if (!on) el.level.style.width = "0%";
  }

  el.listen.addEventListener("click", async () => {
    el.listen.disabled = true;
    if (isListening()) await api("/api/listen/stop", {});
    else { slowWarned = false; await api("/api/listen/start", { device: el.mic.value }); }
    el.listen.disabled = false;
  });
  el.mic.addEventListener("change", () => saveSetting({ input_device: el.mic.value }));

  // ------------------------------------------------------------ live transcript
  const lines = new Map();   // line id -> <p>

  function showLine(line) {
    const placeholder = el.transcript.querySelector(".placeholder");
    if (placeholder) placeholder.remove();
    const nearBottom = el.transcript.scrollHeight - el.transcript.scrollTop - el.transcript.clientHeight < 60;
    let p = lines.get(line.id);
    if (!p) {
      p = make("p");
      lines.set(line.id, p);
      el.transcript.appendChild(p);
      while (el.transcript.children.length > 80) {          // keep the panel light
        const first = el.transcript.firstElementChild;
        for (const [id, node] of lines) if (node === first) lines.delete(id);
        first.remove();
      }
    }
    p.classList.toggle("partial", !line.final);
    fillMarked(p, line.text, line.spans);
    if (nearBottom) el.transcript.scrollTop = el.transcript.scrollHeight;

    // Tell the operator when this computer is too slow for the chosen model.
    if (line.pass_seconds > 2.2 && !slowWarned && state && state.listen.status === "listening") {
      slowWarned = true;
      el.listenMessage.textContent = `This computer needs ${line.pass_seconds.toFixed(1)} s per pass with the ` +
        `“${state.listen.model}” model, so verses appear late. For faster detection choose a smaller model in Settings.`;
    }
  }

  // ----------------------------------------------------------------- settings
  function bindSegmented(container, key) {
    container.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-value]");
      if (button && !button.disabled) saveSetting({ [key]: button.dataset.value });
    });
  }
  function applyUi(value) {
    document.documentElement.dataset.ui = value;
    for (const b of el.segUi.querySelectorAll("button")) b.classList.toggle("active", b.dataset.value === value);
  }
  el.segUi.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    try { localStorage.setItem("ouranos-ui", button.dataset.value); } catch (error) { /* private window */ }
    applyUi(button.dataset.value);
  });
  applyUi(document.documentElement.dataset.ui || "system");

  bindSegmented(el.segTranslation, "translation_mode");
  bindSegmented(el.segTheme, "theme");
  bindSegmented(el.segModel, "whisper_model");
  bindSegmented(el.segLanguage, "language");
  el.fontScale.addEventListener("input", () => {
    el.fontScaleLabel.textContent = Math.round(el.fontScale.value * 100) + "%";
  });
  el.fontScale.addEventListener("change", () => saveSetting({ font_scale: Number(el.fontScale.value) }));
  el.verseNumbers.addEventListener("change", () => saveSetting({ show_verse_numbers: el.verseNumbers.checked }));
  el.dedupe.addEventListener("input", () => { el.dedupeLabel.textContent = el.dedupe.value + " s"; });
  el.dedupe.addEventListener("change", () => saveSetting({ dedupe_seconds: Number(el.dedupe.value) }));
  el.quotedOn.addEventListener("change", () => saveSetting({ semantic_enabled: el.quotedOn.checked }));
  el.quotedThreshold.addEventListener("input", () => {
    el.quotedThresholdLabel.textContent = Math.round(el.quotedThreshold.value * 100) + "%";
  });
  el.quotedThreshold.addEventListener("change",
    () => saveSetting({ semantic_threshold: Number(el.quotedThreshold.value) }));
  el.settingsButton.addEventListener("click", () => { loadDevices(); loadBackgrounds(); el.settings.showModal(); });

  // Manual / Auto display mode
  el.segMode.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (button) saveSetting({ auto_display: button.dataset.value === "auto" });
  });

  // Background picture for the projector
  async function loadBackgrounds() {
    const data = await api("/api/backgrounds");
    if (!data) return;
    el.background.textContent = "";
    el.background.appendChild(new Option("None (use the theme)", ""));
    for (const name of data.images) el.background.appendChild(new Option(name, name));
    el.background.value = data.selected;
  }
  el.background.addEventListener("change", () => saveSetting({ background_image: el.background.value }));
  el.backgroundAdd.addEventListener("click", () => el.backgroundFile.click());
  el.backgroundFile.addEventListener("change", async () => {
    const file = el.backgroundFile.files[0];
    el.backgroundFile.value = "";
    if (!file) return;
    try {
      const response = await fetch("/api/backgrounds?name=" + encodeURIComponent(file.name), {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) { toast(typeof data.detail === "string" ? data.detail : "Could not add the picture."); return; }
      await loadBackgrounds();
    } catch (error) {
      toast("Could not add the picture.");
    }
  });
  el.settings.addEventListener("click", (event) => {       // click outside the box closes it
    if (event.target === el.settings) el.settings.close();
  });

  // ---------------------------------------------------------------- shortcuts
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const tag = target.tagName;
    const typing = (tag === "INPUT" && target.type !== "checkbox" && target.type !== "range")
      || tag === "TEXTAREA" || tag === "SELECT";
    if (typing || el.settings.open || event.ctrlKey || event.altKey || event.metaKey) return;
    switch (event.key) {
      case " ": event.preventDefault(); showSelected(); break;
      case "Escape": clearScreen(); break;
      case "ArrowRight": event.preventDefault(); step(1); break;
      case "ArrowLeft": event.preventDefault(); step(-1); break;
      case "PageDown": event.preventDefault(); turnPage(1); break;
      case "PageUp": event.preventDefault(); turnPage(-1); break;
      case "b": case "B": blackScreen(); break;
      case "/": event.preventDefault(); el.search.focus(); el.search.select(); break;
      default: return;
    }
    // Buttons keep focus after a click; drop it so Space does not "click" them again.
    if (document.activeElement && document.activeElement.tagName === "BUTTON") document.activeElement.blur();
  });

  // ------------------------------------------------------------ state updates
  function setActive(container, value) {
    for (const button of container.querySelectorAll("button")) {
      button.classList.toggle("active", button.dataset.value === value);
    }
  }

  let lastMode = null;
  function applyState(snapshot) {
    const first = state === null;
    state = snapshot;
    const s = snapshot.settings;
    if (first) for (const q of snapshot.queue) newestSeenId = Math.max(newestSeenId, q.id);

    el.banner.hidden = !snapshot.error;
    el.banner.textContent = snapshot.error || "";

    el.displayPill.classList.toggle("ok", snapshot.displays > 0);
    el.displayPill.lastElementChild.textContent = snapshot.displays > 0
      ? `Projector: connected${snapshot.displays > 1 ? " ×" + snapshot.displays : ""}`
      : "Projector: not open";

    // Live status
    const live = snapshot.screen === "verse" && snapshot.panels.length > 0;
    el.liveBadge.textContent = snapshot.screen === "black" ? "Black" : live ? "Live" : "Clear";
    el.liveBadge.className = "badge" + (snapshot.screen === "black" ? " black" : live ? " live" : "");
    el.liveRef.textContent = snapshot.panels.length && snapshot.screen !== "clear"
      ? snapshot.panels.map((p) => p.reference).join("  ·  ")
      : "Nothing showing";
    el.black.classList.toggle("on", snapshot.screen === "black");
    el.pager.hidden = !(live && snapshot.page_count > 1);
    el.pageLabel.textContent = `${snapshot.page + 1} / ${snapshot.page_count}`;

    // Settings controls
    setActive(el.segTranslation, s.translation_mode);
    setActive(el.segTheme, s.theme);
    setActive(el.segModel, s.whisper_model);
    setActive(el.segLanguage, s.language);
    if (document.activeElement !== el.fontScale) {
      el.fontScale.value = s.font_scale;
      el.fontScaleLabel.textContent = Math.round(s.font_scale * 100) + "%";
    }
    if (document.activeElement !== el.dedupe) {
      el.dedupe.value = s.dedupe_seconds;
      el.dedupeLabel.textContent = Math.round(s.dedupe_seconds) + " s";
    }
    el.verseNumbers.checked = s.show_verse_numbers;
    setActive(el.segMode, s.auto_display ? "auto" : "manual");
    el.autoNote.hidden = !s.auto_display;
    if (document.activeElement !== el.background) el.background.value = s.background_image;
    el.quotedOn.checked = s.semantic_enabled;
    if (document.activeElement !== el.quotedThreshold) {
      el.quotedThreshold.value = s.semantic_threshold;
      el.quotedThresholdLabel.textContent = Math.round(s.semantic_threshold * 100) + "%";
    }
    const quoted = snapshot.quoted || { status: "off", message: "" };
    const quotedText = {
      off: "", idle: "Starts with listening.", loading: "Loading the verse index…",
      ready: "Ready: quoted verses are being detected.",
    };
    el.quotedHint.textContent = quoted.message || quotedText[quoted.status] || "";
    el.quotedHint.classList.toggle("warn", quoted.status === "missing" || quoted.status === "error");
    renderModels();
    renderListen();

    // Translation changed: refresh the preview and the search results in the new language.
    if (lastMode !== null && lastMode !== s.translation_mode) {
      if (selected) select(selected, { queueId: selected.queueId, auto: selectedAuto });
      if (el.search.value.trim()) runSearch();
    }
    lastMode = s.translation_mode;
    renderSelected();
    syncQueueSelection();
  }

  // ---------------------------------------------------------------- websocket
  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws`);
    socket.onopen = () => {
      socket.send(JSON.stringify({ type: "hello", role: "operator" }));
      el.serverPill.className = "pill ok";
      el.serverPill.lastElementChild.textContent = "Ready";
      loadDevices();
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      switch (message.type) {
        case "state": applyState(message); break;
        case "level": el.level.style.width = Math.round(message.value * 100) + "%"; break;
        case "transcript": showLine(message); break;
        case "transcript_history": message.lines.forEach(showLine); break;
        case "listen":
          if (state) { state.listen = message; renderListen(); }
          break;
      }
    };
    socket.onclose = () => {
      el.serverPill.className = "pill bad";
      el.serverPill.lastElementChild.textContent = "Disconnected — retrying…";
      setTimeout(connect, 1500);
    };
    socket.onerror = () => socket.close();
  }

  connect();
})();

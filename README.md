# Ouranòs-Pro

**Free, offline Bible verse display for churches.**

While the pastor preaches, Ouranòs-Pro listens to the sermon, recognises the Bible
verses being mentioned or quoted, and lets you put them on the projector with one
key. It works with **no internet and no paid service**, in **English and French**.

- Bibles included: **King James Version** (English) and **Louis Segond 1910** (French).
- Understands "John 3:16", "John chapter three verse sixteen", "Jean trois seize",
  "look at verse 5", and verses that are simply quoted ("the Lord is my shepherd…").
- You stay in control: by default nothing goes on the screen until you press **Show**.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install (once)](#2-install-once)
3. [Start the program](#3-start-the-program)
4. [Connect the projector](#4-connect-the-projector)
5. [Connect the sound](#5-connect-the-sound)
6. [During the service](#6-during-the-service)
7. [Settings](#7-settings)
8. [Live streaming with OBS](#8-live-streaming-with-obs)
9. [If something goes wrong](#9-if-something-goes-wrong)
10. [Good to know](#10-good-to-know)
11. [For developers](#11-for-developers)

---

## 1. What you need

| | |
|---|---|
| Computer | A Windows 10/11 laptop (Mac and Linux also work). 8 GB of memory, 3 GB of free disk space. |
| Projector or TV | Connected to the laptop as a **second screen**. |
| Sound | The laptop microphone works for a test. For a real service, a cable from the sound desk (see step 5) is much better. |
| Internet | **Only for the installation.** After that, never again. |
| Browser | Microsoft Edge or Google Chrome (already on most computers). |

## 2. Install (once)

You need internet for this step. It downloads about 2.5 GB, takes 20–40 minutes and
needs about 6 GB of free disk space. You do **not** need to install Python or anything else.

1. **Double-click `Ouranos-Pro-Setup.exe`** (the file you were sent) and answer **Yes**.
   - If Windows shows "Windows protected your PC", click *More info* → *Run anyway*.
     This appears because the installer is not signed by a company; it is expected.
2. A black window shows six numbered steps. **Leave it open** and stay connected.
   Wait until it says **Setup complete**, then press a key to close it.
3. That is all. The **Ouranòs-Pro** icon is on your Desktop and you can disconnect the internet.

The program is installed in your user folder (`AppData\Local\Programs\Ouranos-Pro`);
no administrator password is needed.

*If you received the whole folder instead of the installer:* open it and double-click
`Setup.bat`. It does the same thing.

*Mac / Linux:* open a terminal in the folder and run `./setup.sh`.

If the setup stops with an error (usually the internet connection dropped), simply
run `Setup.bat` again: it continues where it stopped.

## 3. Start the program

- **Double-click the Ouranòs-Pro icon on the Desktop** (also in the Start menu).
  The program opens in its own window, like any other app. **Closing the window
  stops the program.**
- Or double-click **Ouranòs-Pro + projector** to also open the verse screen
  full-screen on the second monitor in one go.

If the icons are missing, run `scripts\create_shortcut.ps1` (or `Setup.bat` again).
The older launchers still work: `Start Ouranos-Pro.bat` opens the program in your
web browser with a black window that must stay open.

**Your own logo.** Save your picture (a square PNG works best) and run
`.venv\Scripts\python.exe scripts\make_icon.py path\to\your-logo.png`, then
`scripts\create_shortcut.ps1`. The title bar, the window and the Desktop icon all use it.

*Mac / Linux:* `./run.sh` or `./run.sh --kiosk`.

## 4. Connect the projector

1. Plug the projector (or TV) into the laptop.
2. Press `Windows + P` and choose **Extend**. The projector is now a second screen.
3. Start with **`Start with projector.bat`**. The verse screen fills the projector.
   To close that screen, click on it and press `Alt + F4`.

By hand instead: on the operator screen click **Open projector screen**, drag the new
window onto the projector, then double-click inside it (or press `F11`) for full screen.

The top of the operator screen says **Projector: connected** when it is ready.

## 5. Connect the sound

The program hears through the laptop's sound input.

- **For a test:** the built-in laptop microphone. Sit near the speaker.
- **For a service (recommended):** ask the sound team for a feed of the pastor's
  microphone: a cable from the mixer's *AUX / REC OUT* into a small **USB audio
  interface** plugged into the laptop, or the mixer's own USB cable if it has one.
  A clean signal makes a very large difference to accuracy.

Then, on the operator screen:

1. In **Microphone**, choose the input (the USB interface or mixer).
2. Press **Start listening**. The first time takes a few seconds ("Loading…").
3. Speak: the **green bar** must move and words must appear under **Live transcript**.
   - Bar not moving → wrong microphone selected, or the mixer is not sending sound.
   - Bar always full → the signal is too loud; ask the sound desk to lower it.

## 6. During the service

The operator screen has three columns:

| Left | Middle | Right |
|---|---|---|
| **Listening** — start/stop, microphone, live transcript | **Detections** — verses the program found. **Selected verse** — what you are about to show, with the big buttons | **On the projector** — a small copy of the projector. **Find a verse** — manual search |

### The normal routine

1. The pastor says *"Turn with me to Romans chapter 8, verse 28"*.
2. A card **Romans 8:28** appears at the top of **Detections** and is selected automatically.
3. Check it, then press **`Space`** (or click **Show**). It is on the projector.
4. Press **`→`** for the next verse, **`←`** for the previous one.
5. Press **`Esc`** to remove the verse from the screen.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Show the selected verse |
| `Esc` | Clear the screen (the background stays) |
| `→` / `←` | Next / previous verse (the projector follows) |
| `B` | Black screen. Press `B` again to come back |
| `PgDn` / `PgUp` | Next / previous page of a long passage |
| `/` | Jump to the search box |

Shortcuts do not work while you are typing in a box: press `Esc` or click outside first.

### Detection cards

Each card shows the reference, the verse text, what was heard, and these buttons:
**◀ ▶** (move one verse), **Show**, **Dismiss**.

| Tag on the card | Meaning | How much to trust it |
|---|---|---|
| **direct** | The pastor said the reference ("John 3:16"). | Very reliable. |
| **follow-up** | The pastor said only "verse 5"; the program used the chapter mentioned before. | Reliable; glance at it. |
| **quoted** + a percentage | The pastor quoted the words without the reference. | Usually right; **read the card before showing.** Similar verses can be confused. |

A card that only says a chapter ("Psalm 23") starts at verse 1; use **▶** to move on.

### Manual or Auto

At the top of **Detections**:

- **Manual** (default): nothing appears on the projector until you press **Show**.
- **Auto**: references the pastor *says* ("John 3:16", "verse 5") go straight to the
  projector. Quoted verses and whole chapters still wait for you. `Esc` and `B` always work.

Start with Manual. Switch to Auto once you trust the sound quality in your church.

### Finding a verse yourself

In **Find a verse**, type and press `Enter` (shows the first result):

- A reference: `jn 3 16`, `romains 8:28`, `1 cor 13 4-7`, `psalm 23`
- A few words: `God so loved`, `l'eternel est mon berger`

### Clearing and starting fresh

- **Clear** (top right of the live transcript) empties the transcript only. Detections stay.
- **Start fresh** (command bar) is for a new sermon or a new service: it empties the
  transcript and the detections, clears the projector, and makes the program forget
  which chapter was being read. It asks for confirmation first. Listening keeps
  running and your settings are kept.

### Translation

The buttons at the top (**KJV · LSG · Both**) choose the Bible on the projector.
**Both** shows English and French side by side.

## 7. Settings

Click **Settings** at the top right. Everything is saved automatically for next time.

**Projector**

| Setting | What it does |
|---|---|
| Theme | Dark, Light, or Blue & gold. |
| Text size | Makes the projected text bigger or smaller. Long verses still shrink to fit, and very long passages are split into pages. |
| Show verse numbers | Small numbers in front of each verse of a passage. |
| Background picture | **Add picture…** to choose a photo (.jpg, .png, .webp). It is darkened a little so the text stays readable. **None** returns to the theme. |

**Listening**

| Setting | What it does |
|---|---|
| Speech model | **Base** is the fastest. **Small** is more accurate but needs a faster computer. If the screen says the computer "needs more than 2 seconds per pass", choose **Base**. |
| Sermon language | **Auto** for sermons mixing English and French. If the pastor uses one language only, choose it: recognition improves. |
| Do not repeat the same verse within… | How long before the same verse may be suggested again. |

**Quoted verses**

| Setting | What it does |
|---|---|
| Also detect verses that are quoted | On/off switch for *quoted* cards. |
| Minimum match score | Default 65%. Too many wrong guesses → raise it. Missing obvious quotes → lower it. |

## 8. Live streaming with OBS

In OBS add a **Browser** source with one of these addresses (width 1920, height 1080):

- `http://127.0.0.1:8765/display?bg=transparent` — verse text over your camera picture
- `http://127.0.0.1:8765/display?bg=green` — green background for a chroma key

## 9. If something goes wrong

| Problem | What to do |
|---|---|
| The setup stops with a red **ERROR** line | Almost always the internet connection. Run the installer (or `Setup.bat`) again: it continues where it stopped. The reason is also saved in `logs\setup-result.txt`. |
| Windows refuses to start the installer: "An Application Control policy has blocked this file" or "Smart App Control blocked…" | That PC has **Smart App Control** switched on (some new Windows 11 PCs). It blocks every program that is not signed by a company, and there is no "Run anyway" button. The installer cannot work there unless the owner turns Smart App Control off in *Windows Security → App & browser control* — their decision, and Windows does not let it be turned back on later. |
| "Ouranos-Pro is not set up yet" | Run `Setup.bat` first. |
| The browser says "This site can't be reached" | The black window was closed. Double-click `Start Ouranos-Pro.bat` again. |
| "The speech model … is not downloaded yet" | Connect to the internet and run `Setup.bat` again. |
| "No microphone was found" / "could not open the microphone" | Plug the microphone or USB interface in, then choose it in **Microphone**. In Windows: *Settings → Privacy → Microphone* must allow desktop apps. |
| The green bar does not move | Wrong microphone selected, or it is muted in Windows sound settings. |
| Words appear, but many are wrong | The sound is the cause nine times out of ten: use a feed from the mixer instead of the laptop microphone. Then try **Sermon language** and the **Small** model. |
| Verses appear several seconds late | Choose the **Base** speech model. Close other programs. Keep the laptop plugged in. |
| A wrong verse is suggested | Press **Dismiss**. For *quoted* cards, raise **Minimum match score**. |
| The same verse keeps coming back | Increase **Do not repeat the same verse within…**. |
| The projector shows the operator screen | Press `Windows + P` → **Extend**, then use `Start with projector.bat`. |
| The projector is black | Press `B` (black screen may be on). Check **On the projector** on the operator screen. |
| "Quoted verses" says there is a problem | Stop and start listening once; if it stays, close the program and start it again. |
| The black window shows an error mentioning "address already in use" or port 8765 | The program is already running in another black window. Close the extra one. |

For help, send the file `logs/ouranos.log` to whoever supports you: it records what
the program heard and did, and every error.

## 10. Good to know

- **Privacy.** Sound is processed on this computer only. Nothing is recorded to disk
  and nothing is sent anywhere. The log file keeps the references detected and the
  few words that triggered them.
- **Accuracy depends on the sound.** A clear feed from the mixer gives good results;
  a laptop microphone at the back of a noisy hall does not.
- **Psalm numbering.** In some Psalms the French Bible counts the title as verse 1,
  so the English and French verse numbers can differ by one in **Both** mode.
- **Look-alike verses.** For *quoted* cards, verses with nearly the same words
  (John 3:15 and 3:16) can be confused. Read the card.
- **Giving it to someone else.** Send them the single file `dist\Ouranos-Pro-Setup.exe`
  (under 1 MB: by e-mail, WhatsApp or a Google Drive link). It downloads everything
  else by itself. To rebuild that file after a change:
  `powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1`.
- **Updating.** Running a newer `Ouranos-Pro-Setup.exe` on a PC that already has the
  program replaces the program files and keeps the downloaded models and settings.
- **Removing it.** Delete the folder `AppData\Local\Programs\Ouranos-Pro` and the
  Desktop icons. Nothing else is installed on the PC.
- **Adding book names in another language** (Bemba, Nyanja, Lingala…): open
  `data/books.json` in Notepad and add the names under `aliases`, for example
  `"bem": ["ukutendeka"]` next to `"en"` and `"fr"`. Names shorter than 4 letters only
  work in the search box. Restart the program afterwards. (Recognising *speech* in
  those languages depends on the speech model and is not guaranteed.)

## 11. For developers

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

| Path | Role |
|---|---|
| `app/main.py` | FastAPI app: pages, REST API, WebSocket hub, listening session |
| `app/config.py` | Paths, constants, `Settings` (saved to `data/settings.json`) |
| `app/audio.py` | Microphone list and capture (16 kHz mono), level meter |
| `app/transcriber.py` | faster-whisper wrapper and the live loop (growing window, VAD) |
| `app/reference_parser.py` | Typed and spoken reference parsing, EN + FR |
| `app/semantic_matcher.py` | Quoted-verse matching: embeddings + word overlap |
| `app/detector.py` | Merges both sources: stability, refinement, de-duplication |
| `app/state.py` | Projector state and detections queue |
| `app/bible_db.py` | SQLite access, FTS5 search, navigation |
| `static/` | Operator and projector pages (plain HTML/CSS/JS, no build step) |
| `scripts/build_bible_db.py` | KJV (scrollmapper/bible_databases) + Louis Segond 1910 (getbible.net) → `data/bible.sqlite` |
| `scripts/download_models.py` | Whisper models and the embedding model → `models/` |
| `scripts/build_embeddings.py` | One embedding per verse → `data/embeddings/` |

Launch options: `run.ps1 -Kiosk`, `-NoBrowser`, `-Port 9000`. The server listens on
`127.0.0.1` only.

Rehearse without a preacher: set the environment variable `OURANOS_TEST_WAV` to a
16-bit WAV file before starting; it appears in the microphone list as *Test file (WAV)*
and is played as if it were live. Two recordings are in `tests/data/`.

Extra speech model: `python scripts/download_models.py --whisper medium`.

### Licences

Code dependencies are open source (FastAPI, faster-whisper, sentence-transformers,
rapidfuzz, NumPy, sounddevice). The KJV and Louis Segond 1910 texts are in the public domain.

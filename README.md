# Pyxida

*Your career compass.* Pyxida (Greek for "compass") is a browser app that uses
your signed-in **Codex CLI** to infer suitable jobs from a CV, discuss
preferences, search Switzerland, draft applications, and carry each one through
interviews. **No API key or target job title is required.**

The interface has six pages: **Profile** (CV, discussion, supporting
documents, career goals, writing style, public work), **Jobs**, **Applications**,
**Pipeline**, **Growth** and **Settings** (Codex connection, job portals,
document design, access).

## Run locally

Use Python 3.10 or newer and a recent Codex CLI. Pyxida also detects the Codex
binary bundled with the VS Code extension if it is not on your PATH.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open **http://127.0.0.1:8765**. Locally Pyxida listens only on this computer and
needs no password. If Codex is not signed in, use **Settings → Sign in with
ChatGPT** (or `codex login` in a terminal). For another port, use
`--port 8766`. Scanned PDFs need text extraction before upload.

## Run with Docker or Portainer

The image bundles Python, the Codex CLI (pinned with the `CODEX_VERSION` build
argument) and the app. Profiles and the Codex sign-in are stored in the
`/data` volume, so they survive updates and restarts.

**Docker Compose**

```bash
APP_PASSWORD='choose-a-long-password' docker compose up -d --build
```

**Portainer stack**

1. Put this folder in a Git repository Portainer can reach (a private one is fine),
   then **Stacks → Add stack → Repository**, compose path `docker-compose.yml`.
   Portainer builds the image from the repository.
   *Alternatively*, build on the Docker host with `docker build -t pyxida:latest .`,
   then use **Web editor** with the contents of `docker-compose.yml` minus the
   `build: .` line.
2. Under **Environment variables**, set `APP_PASSWORD`. Optionally set
   `ALLOWED_HOSTS` (for example `pyxida.home.lan,192.168.1.20:8765`),
   `PYXIDA_PORT` (default 8765) and `CODEX_MODEL`.
3. Deploy, open `http://<host>:8765`, sign in with the password, then go to
   **Settings → Sign in with ChatGPT**. Pyxida shows an OpenAI link and a
   one-time code; approve it on any device and Pyxida connects automatically.
   This uses Codex's device-code sign-in, so it works when the browser and the
   container are on different machines.

| Variable | Purpose |
|---|---|
| `APP_PASSWORD` | Required whenever Pyxida listens on a network address. Pyxida refuses to start without it unless `ALLOW_UNAUTHENTICATED=1` (only behind a VPN or an authenticating proxy). |
| `ALLOWED_HOSTS` | Comma-separated host names (or host:port) accepted in the address bar. Leave empty to accept any; set it when exposing Pyxida through a domain. |
| `CODEX_MODEL` | Override the Codex default model. |
| `CODEX_SANDBOX` | `read-only` (default). Set `danger-full-access` only if Codex runs fail because your container runtime blocks Codex's Linux sandbox; shell tools stay disabled either way and the container still isolates the process. |
| `PYXIDA_PORT`, `TZ` | Published port and time zone (default `Europe/Zurich`). |

Behind a reverse proxy (Nginx Proxy Manager, Traefik, Caddy), forward the
`Host` header and `X-Forwarded-Proto`; sign-in cookies are marked `Secure` on
HTTPS. Long Codex operations (searches, reviewed drafts) run in the background
and the browser polls for the result, so proxy timeouts do not interrupt them.
Health checks use `/healthz`.

If OpenAI's page does not accept device-code sign-in for your account, sign in
with `codex login` on another computer and copy its `~/.codex/auth.json` into
the volume at `/data/codex/auth.json` (owned by UID 1000), then restart the
stack. Treat that file like a password.

## Workflow

1. Upload a CV or paste its text. Codex suggests role families and seniority,
   citing exact CV evidence. The default sector focus is AML, KYC, financial
   crime, finance and banking; relevant adjacent roles are included.
2. Answer the brief questions or correct the profile in the discussion.
   Optional preferences cover Swiss locations, work arrangement, salary,
   work authorization (Swiss citizen, C/B/G permit, EU/EFTA, sponsorship) and
   deal-breakers. Clear the sector focus to explore other supported fields.
   Add **supporting documents** (a LinkedIn data export ZIP, reference letters,
   diplomas, past applications). Their text becomes citable evidence, and Codex
   flags conflicting titles, dates or degrees against your CV, which you
   resolve with one click, plus facts your CV leaves out. Fill in **career goals
   and working style** (goals, energizing and draining work, environment,
   behavioral assessment) to sharpen culture and career scores, motivation in
   letters and interview prep. Pyxida can **learn your writing style** from
   past applications so drafts and follow-ups sound like you.
   Optionally, **enrich the profile** from public work: Codex opens the GitHub,
   portfolio or publication links in your CV and proposes competencies with the
   page that shows each one. Only the ones you confirm are added, as your own
   discussion message, so they become citable evidence.
3. Find suitable jobs. Codex uses live web search across the **job portals** you
   enable (jobs.ch, jobup.ch, Job-Room, LinkedIn, Indeed, eFinancialCareers and
   JobScout24 by default; add your own) plus employer career pages, and reads
   each listing. Every listing gets four 0–100 scores (skills 30%, experience
   25%, culture 15%, career 30%), a weighted overall score, and three gates:
   language (a required language you have not declared fails; a higher level
   is flagged), work authorization (silence is "unverified", never permission)
   and location. Listings that fail a gate are listed separately, not silently
   dropped. **Deep search** runs one Codex search per few portals plus one for
   employer career pages and ranks up to 25 listings; the same role reposted on
   several portals or by recruiters appears once. Listings show deadlines with urgency, keywords, questions worth
   asking the employer, and a link to find people at the company. Dismissed and
   applied listings are skipped in later searches, new listings are marked, and
   each portal shows whether it returned anything. Past outcomes calibrate the
   ranking. You can also **evaluate one posting** from a URL or pasted text.
   Pasted text first passes a deterministic integrity check that strips hidden
   HTML and invisible characters and quotes any instruction-shaped lines aimed
   at AI tools. Search coverage is limited to accessible pages and is not an
   exhaustive survey of all Swiss vacancies.
4. Select listings and prepare applications in English, German, French or
   Italian. Codex checks each listing again and creates a tailored CV,
   cover letter, email draft and review notes. With **Independent review**
   (on by default), Codex researches the employer (cached for 30 days and shared
   across profiles), critiques the drafts in a separate run (grounding, targeting,
   keywords, consistency) and revises them. A revision that loses valid evidence is
   discarded in favour of the first grounded draft. If the rendered CV runs past
   two pages or the letter past one, Codex shortens them with relevance-weighted
   cutting (least relevant lines first, regardless of age) for up to two passes.
   Each package gets **quality checks** on the
   rendered PDFs: CV at most two pages, letter one page, a clean text layer, email
   and telephone present as literal text for applicant tracking systems,
   unfilled placeholders, stock phrases and listing keyword coverage. Missing
   keywords are reported, never inserted. Paste **application form questions**
   with character limits to get grounded answers. Download a ZIP with PDF, Word and
   text versions. Applications are not submitted.
5. Track each application in the **pipeline**: drafted, applied, interviewing,
   offer, hired, rejected, no response, offer declined or withdrawn, with dated
   notes. The first time you mark it as sent, the CV, letter and email are frozen
   so later regenerations don't change your record of what you sent. Applications quiet for
   10+ days are flagged; Codex drafts a follow-up (at most two) or a thank-you
   note from what you actually sent, which you log once sent. For interviews,
   Codex builds a stage-specific prep pack (likely questions, STAR answers cited
   to your CV, what the interviewer has read, tough questions with honest
   bridges, questions to ask, verified company and interviewer facts) and runs
   a mock interview with coaching after each answer. Stats, a funnel, CSV export
   and an offline HTML report are included. Once three or more applications have
   resolved, **calibrate from outcomes** to learn which roles get traction for
   you; the patterns feed future fit scores.
6. **Skills & growth** maps recurring gaps across your shortlist and tracked
   jobs into a prioritized heatmap with a learning plan, live-searched resources
   and a study order, and shows which gaps are new or closed since the last run.

CVs and cover letters pair **Source Serif 4** names with **Source Sans 3** body
text, 19 mm A4 margins, navy rules, a clear heading hierarchy and consistent
paragraph spacing. Employer names and dates sit below role titles, with dates
aligned to the right. Proper bullets, bold emphasis, contact headers and page numbering
complete the layout. Role headings
stay with their dates and opening bullets. Cover letters use a business-letter
layout. Formatted previews and individual PDF/Word downloads are available in
each application card. Existing saved drafts use the new formatting immediately
on export; no additional Codex generation is needed. Factual placeholders are
preserved for review, and long documents flow onto extra pages rather than being
cut off. Fonts are bundled locally and embedded in PDF and Word exports.
PDF exports provide a fixed layout; Word pagination can vary between viewers.
The bundled Adobe fonts use the SIL Open Font License; licenses and source checksums
are in `assets/fonts/` ([Source Sans](https://github.com/adobe-fonts/source-sans),
[Source Serif](https://github.com/adobe-fonts/source-serif)).

In **Applications → Document design**, optionally upload a JPEG, PNG or WebP
picture (up to 5 MB and 24 megapixels). Adjust the zoom and position, choose
whether it appears on the CV, cover letter, or both, then click **Save appearance**.
The 4:5 crop prints at 35 × 43.75 mm. Pictures are saved only in the local profile,
with image metadata removed, and are never included in Codex prompts. You can
remove the picture at any time. Appearance changes update existing previews and
downloads immediately without regenerating drafts or consuming Codex usage.

Profiles, discussions, shortlists and drafts persist in `.data/profiles.sqlite3` (or `$DATA_DIR`; `/data` in Docker).
Use the profile selector to switch CVs. Updating a profile keeps previous
results and drafts but requires refreshing the shortlist before drafting again.
Deleting a profile removes its local records, including generated materials.

## Codex integration

The Python backend invokes `codex exec` with prompts on stdin, a JSON output
schema, read-only execution, and temporary output files. Search runs use live
web search; profile analysis has web search disabled. Shell tools, connectors,
plugins, hooks and subagents are disabled for these runs. No authorization is
granted to send messages or submit applications. Codex calls are ephemeral;
the app supplies the saved CV and discussion to reconstruct context.

The app requires ChatGPT authentication, strips API-key environment variables
from child processes, and does not implement direct OpenAI API calls.
Codex's built-in default model is used unless CODEX_MODEL is set. App runs do not
load your global CLI configuration; they keep your existing sign-in. These runs consume your Codex plan's usage and
require internet access; local hosting does not make model inference offline.
CV content is processed by Codex through that account. Native search events are
checked before accepting research, but page interpretation and draft quality
remain model judgments. Drafts cite numbered excerpts from the CV and candidate
discussion, which the app resolves to the original text. Unsupported references
trigger one repair attempt and are rejected if still invalid. Citation checks
do not prove that every draft sentence is accurate. Review the final materials.

Optional `.env` settings for local runs are listed in `.env.example`
(`CODEX_BIN`, `CODEX_MODEL`, and the network settings described under Docker).

Restart the app after changing these settings. Never put a credential in the
browser or this file. If the app cannot connect, check `codex login status`,
network access and your plan's usage limits. A Codex invocation can take several
minutes; an operation times out after ten minutes. Completed application
packages survive a later failure in the same selection.
Retrying preparation skips packages already completed for the current profile
and chosen language. Transient Codex connection and rate-limit failures receive
one automatic retry; authentication and exhausted usage allowances do not.

Runs that use live web search (job search, company and interviewer research,
profile enrichment, learning resources) receive a professional summary rather
than your CV text or contact details. Gmail, Notion and other connectors stay
disabled for Codex runs, so status updates are recorded by hand.

## Checks

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node --test tests/test_photo_crop.cjs
```

The automated checks cover the CV-driven workflow, state isolation and resume,
source/evidence filters, selected-job controls, exports and the Codex subprocess
contract using fake Codex responses, as well as font embedding, PDF pagination,
photo normalization, crop geometry and photo export controls. To check your actual signed-in CLI:

```bash
python3 scripts/smoke_codex.py
```

That check runs two small Codex tasks (including live search), uses your plan,
and does not use a real CV or create a job profile.

Integration references: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Codex authentication](https://learn.chatgpt.com/docs/auth), and
[CLI options](https://learn.chatgpt.com/docs/cli/reference).

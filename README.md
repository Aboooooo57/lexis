# Lexume — AI English Reader & Translator

**Lexume** is an open-source, AI-powered platform that transforms any document into an immersive English learning experience. Upload a PDF, paste a paragraph, or import from Google Drive — Lexume extracts the content with **Google Gemini**, generates a native-speaker audio track with **ElevenLabs**, and plays it back with **word-by-word karaoke highlighting**. Click any word to get its full dictionary entry, phonetics, and synonyms instantly.

This repo now contains **four** apps: the original web platform below (`backend/` + `frontend/`), a fully native, offline-capable **macOS/iPadOS app** (`macos-app/`) — on-device OCR, no server required — a **native Android app** (`android-app/`, in progress) built to the same identity (your own API keys, local data, works offline), and a **native Windows app** (`windows-app/`, just started) — WPF on modern .NET, targeting Windows 10+. See [`macos-app/README.md`](macos-app/README.md), [`android-app/README.md`](android-app/README.md), and [`windows-app/README.md`](windows-app/README.md) for their own setup, feature docs, and build status.

The macOS and iPadOS apps are one Xcode target sharing one source list, not two projects. The iPad build reads at parity with the Mac — reflowed text and Original Layout mode, dictionary, narration, translation, bookmarks, vocabulary — with two current exceptions: Google Drive backup (macOS only for now) and drag-to-select-and-copy on the page image. It needs a one-time Xcode setup step before it will build; see the Milestone 12 section of [`macos-app/README.md`](macos-app/README.md).

---

## What it does

| Step | What happens |
|---|---|
| 1 | Upload a **PDF**, **TXT**, or **MD** file, paste text, or import from **Google Drive** |
| 2 | **Gemini AI** reads the document, cleans it, and formats it as structured Markdown |
| 3 | **ElevenLabs** converts the cleaned text to a high-quality MP3 with per-character timestamps |
| 4 | The **karaoke player** highlights each word in real time as the audio plays |
| 5 | Click any word → instant **dictionary popup** (definition · phonetics · synonyms · audio) |
| 6 | Click any paragraph → Gemini identifies **key terms** and shows their full dictionary entries |

---

## Features

- **Karaoke reader** — real-time word highlighting synchronized to TTS audio
- **Instant dictionary** — click any word anywhere; stacked navigation lets you follow words within definitions
- **AI key-term extraction** — Gemini selects the most important vocabulary per paragraph
- **Phonetic pronunciation** — every dictionary entry plays its own audio clip
- **Visual PDF page selector** — thumbnail picker; process only the pages you need
- **Google Drive integration** — browse and import PDFs directly from your Drive
- **Google Sign-In** — persistent sessions and per-user reading history
- **Credit system** — per-operation credit tracking (extraction, audio generation, translation)
- **Session library** — full reading history with bookmarks and vocabulary lookups per session
- **Focus mode** — hides all chrome, leaving only the floating player
- **Customizable reader** — font size (slider + presets), font family (sans/serif/mono)
- **Multiple AI models** — swap Gemini and ElevenLabs models from the settings panel
- **Voice tuning** — stability, similarity boost, speed, and style sliders

---

## Architecture

```
lexume/
├── backend/                     # Python FastAPI backend
│   ├── server.py                # Uvicorn entry point
│   ├── requirements.txt         # Python dependencies
│   ├── Dockerfile
│   └── api/
│       ├── app.py               # FastAPI app + CORS + lifespan
│       ├── auth.py              # Google OAuth + JWT helpers
│       ├── database.py          # SQLAlchemy async SQLite setup
│       ├── models.py            # Pydantic request/response models
│       └── routes/
│           ├── extract.py       # POST /api/extract
│           ├── generate.py      # POST /api/generate, GET /api/audio/{id}
│           ├── dictionary.py    # GET  /api/dictionary/{word}, /api/key-terms
│           ├── library.py       # Session library + Google Drive integration
│           ├── users.py         # User profile & preferences
│           ├── auth.py          # Google Sign-In & Drive OAuth callbacks
│           ├── audio.py         # Audio streaming
│           ├── session.py       # Session data endpoints
│           └── pages.py         # HTML page routes (Jinja2)
│
├── frontend/                    # Next.js 16 + Tailwind v4 web app
│   ├── Dockerfile
│   ├── .env.example
│   └── src/
│       ├── app/
│       │   ├── page.tsx                    # Landing page
│       │   ├── login/page.tsx              # Google Sign-In
│       │   ├── dashboard/page.tsx          # Upload + session library
│       │   └── lesson/[sessionId]/page.tsx # Karaoke reader
│       ├── api/
│       │   ├── client.ts        # Fetch wrapper (reads NEXT_PUBLIC_API_URL)
│       │   ├── index.ts         # Typed API methods
│       │   └── types.ts         # Shared TypeScript types
│       └── components/
│           ├── DictionaryModal.tsx
│           ├── PDFPageSelector.tsx
│           └── ThemeProvider.tsx
│
├── macos-app/                    # Native SwiftUI macOS/iPadOS app (Lexume) — offline-capable, no backend required
│   └── (see macos-app/README.md for its own setup and feature docs)
│
├── android-app/                  # Native Kotlin/Compose Android app (Lexume) — in progress, same identity as macos-app
│   └── (see android-app/README.md for its own setup, feature docs, and milestone status)
│
├── windows-app/                  # Native WPF/.NET Framework 4.8 Windows app (Lexume) — just started, targets Windows 7+
│   └── (see windows-app/README.md for its own setup, feature docs, and milestone status)
│
├── docker-compose.yml           # Full-stack local dev
└── .env.example                 # Backend environment variable template
```

---

## Quick start

### Prerequisites

| Tool | Minimum version |
|---|---|
| Python | 3.11 |
| Node.js | 20 |
| npm | 10 |

### API keys you need

| Service | Purpose | Get it at |
|---|---|---|
| Google Gemini | Text extraction & key-term AI | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) |
| ElevenLabs | Text-to-speech with timestamps | [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys) |
| Google OAuth 2.0 | Sign-in + Drive integration | [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials) |

> Google OAuth is required for login. When creating credentials, add `http://localhost:8000/api/auth/google/callback` as an authorized redirect URI.

---

### 1 — Clone & configure

```bash
git clone https://github.com/Aboooooo57/lexume.git
cd lexume

# Backend secrets
cp .env.example backend/.env
# Edit backend/.env — fill in your API keys and secrets

# Frontend env
cp frontend/.env.example frontend/.env.local
# The default NEXT_PUBLIC_API_URL=http://localhost:8000 works for local dev
```

---

### 2A — Run with Docker (recommended)

```bash
docker compose up --build
```

- Frontend → [http://localhost:3000](http://localhost:3000)
- Backend API → [http://localhost:8000](http://localhost:8000)
- API docs (Swagger) → [http://localhost:8000/docs](http://localhost:8000/docs)

---

### 2B — Run manually

**Backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py
# or: uvicorn server:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

---

## API reference

The backend exposes a REST API. Interactive docs are at `/docs` (Swagger UI) and `/redoc` when the server is running.

### `POST /api/extract`

Extract and reformat text from a file or pasted text.

| Form field | Type | Description |
|---|---|---|
| `file` | File | PDF, TXT, or MD upload |
| `text` | string | Raw text (alternative to file upload) |
| `pages` | string | PDF page range, e.g. `"1-3,5"` |
| `gemini_model` | string | Gemini model ID (default: `gemini-2.5-flash`) |
| `gemini_key` | string | Overrides `GEMINI_API_KEY` env var |
| `mock_gemini` | bool | Skip Gemini and return a sample text |

**Response**

```json
{
  "session_id": "uuid",
  "extracted": "# Markdown text...",
  "paragraphs": ["paragraph 1", "paragraph 2"]
}
```

---

### `POST /api/generate`

Generate TTS audio with word-level timestamps.

```json
{
  "session_id": "uuid",
  "eleven_model": "eleven_multilingual_v2",
  "voice_id": "JBFqnCBsd6RMkjVDRZzb",
  "eleven_key": "",
  "stability": 0.5,
  "similarity_boost": 0.75,
  "speed": 1.0,
  "style": 0.0,
  "mock_eleven": false
}
```

**Response**

```json
{
  "session_id": "uuid",
  "word_timings": [
    { "word": "Asyncio", "start": 0.12, "end": 0.71 }
  ]
}
```

---

### `GET /api/audio/{session_id}`

Stream the generated MP3. Add `?download=true` for a file download.

---

### `GET /api/dictionary/{word}`

Proxy for the free [dictionaryapi.dev](https://dictionaryapi.dev/) — full entry with meanings, phonetics, examples, and synonyms.

---

### `GET /api/key-terms`

Ask Gemini to pick the most dictionary-worthy words from a paragraph.

| Query param | Description |
|---|---|
| `session_id` | Session UUID |
| `paragraph_index` | 0-based index into the paragraphs array |
| `gemini_key` | Optional API key override |
| `gemini_model` | Gemini model ID |
| `mock_gemini` | Return sample terms without calling Gemini |

---

### `GET /api/session/{session_id}`

Return full session data as JSON (used by the Next.js result page).

---

### Lexume Plus billing (`/api/billing/...`)

See "Lexume Plus subscription" below for the full picture. Quick reference:

| Endpoint | Description |
|---|---|
| `POST /api/billing/stripe/checkout` | Create a Stripe Checkout session (`{"plan": "monthly"\|"annual"}`) |
| `POST /api/billing/stripe/portal` | Stripe Billing Portal link (manage/cancel) |
| `POST /api/billing/stripe/webhook` | Stripe webhook receiver — not for direct client use |
| `POST /api/billing/play/verify` | Verify a Google Play purchase token server-side |
| `POST /api/billing/play/webhook` | Play Real-time Developer Notifications receiver — not for direct client use |
| `GET /api/billing/status` | Current user's subscription status + quota usage |

---

## Configuration

Copy `.env.example` to `backend/.env`. All keys are documented there.

### Core variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | — | Google Gemini API key |
| `ELEVENLABS_API_KEY` | Yes | — | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | No | `JBFqnCBsd6RMkjVDRZzb` | Default TTS voice |
| `GOOGLE_CLIENT_ID` | Yes | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes | — | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | Yes | — | OAuth callback URL |
| `JWT_SECRET` | Yes | — | Secret for signing user JWTs |
| `ENCRYPTION_SECRET` | Yes | — | Secret for encrypting Drive tokens |
| `ALLOWED_ORIGINS` | No | `http://localhost:3000,...` | Comma-separated CORS origins |

### Frontend variable

Set in `frontend/.env.local`:

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API base URL |

---

## Lexume Plus subscription

Every app in this repo defaults to **bring your own API keys** — free, local,
zero Lexume server dependency — and that stays true. **Lexume Plus** is an
additive paid option: an active subscriber can use Lexume's own Gemini +
ElevenLabs keys instead, without ever entering their own. The two modes are
just a per-request choice (BYOK key present → BYOK; absent → requires an
active subscription) — see `backend/api/subscription.py` for the actual
gating logic, and the business/unit-economics rationale behind the numbers
below in the subscription plan this was built from.

**Compliance note, read before enabling this for real users**: reselling
ElevenLabs access via a subscription wrapper requires becoming an
[Authorized Reseller](https://elevenlabs.io/use-policy) (or using their
Business/Enterprise commercial tier) — contact ElevenLabs before a public
launch, not after. Also use a genuine **paid-tier** Gemini key for
`GEMINI_API_KEY` here, not a free AI Studio key — free-tier terms allow
Google to train on prompts, which isn't acceptable for paying subscribers'
documents.

### How it works

- **Extraction** (Gemini): free/unmetered for both BYOK and subscribers —
  cheap enough (~$0.004/page) not to bother quota-limiting.
- **Narration** (ElevenLabs): free for BYOK. Subscribers get
  `SUBSCRIPTION_PAGES_QUOTA` (70 by default) narrated pages per billing
  period, forced to a Flash/Turbo voice model to keep unit economics sane —
  past that, the pre-existing credit-purchase flow (`/api/users/me/credits/purchase`)
  acts as a paid top-up rather than a hard cutoff.
- **Payment platforms**: Google Play Billing (Android — mandatory, it's
  Play-distributed) and Stripe (web / Mac, which stays direct-DMG
  distributed, so Apple's IAP mandate doesn't apply). Neither platform's
  client-reported purchase is ever trusted directly — Stripe changes arrive
  via signature-verified webhooks, Play purchases are verified server-side
  against the Play Developer API before a subscription is marked active.

### Setup

Fill in the `SUBSCRIPTION_*` / `STRIPE_*` / `PLAY_*` variables documented in
`.env.example` — all optional; endpoints return `503` until configured
rather than failing at startup. You'll need:
- A Stripe account with a subscription Product carrying a monthly and an
  annual Price, and a webhook endpoint (`/api/billing/stripe/webhook`)
  registered for `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted`.
- A Google Play Console service account with Android Publisher API access
  (Play Console → Setup → API access) for `PLAY_SERVICE_ACCOUNT_JSON`, and a
  Pub/Sub push subscription pointed at `/api/billing/play/webhook?secret=...`
  for Real-time Developer Notifications.

### Status

| # | Milestone | Status |
|---|---|---|
| 1 | Backend billing foundation (this repo's `backend/`) | ✅ built and smoke-tested against a real SQLite DB + FastAPI TestClient in this sandbox (subscription gating, quota exhaustion, unconfigured-platform 503s, unauthenticated 401 all verified) — **the actual Stripe/Play Billing HTTP calls themselves are unverified**, since no live credentials exist here. Verify webhook signature handling and purchase verification against real Stripe test-mode and a Play Console closed-testing track before trusting this in production. |
| 2 | Android integration | ⬜ not started |
| 3 | Mac/iPad integration | ⬜ not started |
| 4 | Compliance & launch prep (ElevenLabs reseller status, paid Gemini key, privacy policy, closed beta) | ⬜ not started |

---

## Supported AI models

### Gemini (text extraction & key terms)

| Model ID | Notes |
|---|---|
| `gemini-2.5-flash` | Best price/performance — **default** |
| `gemini-2.5-pro` | Most capable, deep reasoning |
| `gemini-2.5-flash-lite` | Fastest & cheapest |

### ElevenLabs (TTS)

| Model ID | Notes |
|---|---|
| `eleven_multilingual_v2` | High quality, 29 languages — **default** |
| `eleven_v3` | Most expressive (alpha) |
| `eleven_turbo_v2_5` | Low latency, 32 languages |
| `eleven_turbo_v2` | Low latency, English |
| `eleven_flash_v2_5` | Ultra-fast, 32 languages |

---

## Deployment

### Vercel (frontend) + Fly.io (backend)

**Backend on Fly.io**

```bash
cd backend
fly launch --name lexume-api
fly secrets set \
  GEMINI_API_KEY=... \
  ELEVENLABS_API_KEY=... \
  GOOGLE_CLIENT_ID=... \
  GOOGLE_CLIENT_SECRET=... \
  GOOGLE_REDIRECT_URI=https://lexume-api.fly.dev/api/auth/google/callback \
  JWT_SECRET=... \
  ENCRYPTION_SECRET=... \
  ALLOWED_ORIGINS=https://your-app.vercel.app
fly deploy
```

**Frontend on Vercel**

Set in the Vercel dashboard:

```
NEXT_PUBLIC_API_URL=https://lexume-api.fly.dev
```

Then deploy:

```bash
cd frontend
vercel --prod
```

---

## Development tips

### Mock mode

Both the API and the UI accept mock flags that skip external calls — free and instant for local development:

- `mock_gemini=true` — returns a hard-coded Asyncio tutorial excerpt
- `mock_eleven=true` — plays the bundled mock audio without word timestamps

### Database

The backend uses an async **SQLite** database (`backend/lexume.db`) managed by SQLAlchemy. It is created automatically on first start. For production multi-instance deployments, swap SQLite for PostgreSQL by updating `api/database.py`.

### Changing the default voice

1. Browse [elevenlabs.io/voice-library](https://elevenlabs.io/voice-library) and copy a voice ID
2. Set `ELEVENLABS_VOICE_ID=<id>` in `backend/.env`

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss the proposal.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes
4. Open a pull request

---

## License

MIT — see [LICENSE](LICENSE).

# AutoDev

AutoDev is a desktop-first autonomous coding assistant. It plans the task, writes files, installs dependencies, runs the project, and retries fixes when something breaks.

## What is in this repo

- Electron desktop shell under `desktop/`
- App frontend under `frontend/`
- Python runtime and orchestration code in the repo root
- Marketing landing page under `landing/`

## Landing Page

The landing site is deployed separately from the app runtime and is designed to stay lightweight.

- Source: `landing/`
- Production URL: [https://autodev-landing.vercel.app](https://autodev-landing.vercel.app)

Large desktop binaries are intentionally not committed to GitHub. They are hosted with the landing deployment instead of the source repository.

## Local Development

Install dependencies:

```bash
npm install
python3 -m venv .autodev_venv
source .autodev_venv/bin/activate
pip install -r requirements.txt
```

Run the desktop app:

```bash
npm run dev
```

## Deploying the Landing Site

The landing page can be deployed independently from the app:

```bash
cd landing
npx vercel --prod
```

## Notes

- `.env` and other local runtime files are excluded from version control.
- Build output and packaged downloads are excluded from GitHub.
- If you want downloadable binaries in GitHub later, the clean route is GitHub Releases rather than committing large files directly.

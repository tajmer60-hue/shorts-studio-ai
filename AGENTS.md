# Instructions for AI developers

Build Shorts Studio AI as a local Windows 11 application. The user is the customer and tester; take responsibility for implementation, installing project dependencies, meaningful tests and fixes, and saving reviewed changes to GitHub. Do not stop at examples or plans.

## Product and architecture

- Read PROJECT_SPEC.md and README.md before changing the architecture.
- Python 3.11+, PySide6 desktop UI, FFmpeg/FFprobe subprocesses, SQLite persistent job queue. Local AI models; no mandatory paid APIs.
- Target RTX 4070 Laptop 8 GB VRAM, 16 GB RAM. Process media in streams/chunks; do not load an entire film into memory. Start AI workers lazily and avoid holding multiple large models in VRAM simultaneously.
- Keep GUI responsive. Marshal worker results onto the UI thread. Run long media/AI work outside the UI thread and support cancellation.
- Make all incomplete features visible as incomplete. Do not claim semantic understanding, virality prediction, censorship accuracy or platform compliance without evidence.
- Smart Censor output always requires human review before publication.

## Data and operations

- The user requires all project dependencies, tools, media, models, data and caches on D:, keeping C: free. Do not install more project dependencies on C: as a fallback. Use D:\ShortsStudioAI\project once migration is completed and verified. If the tool sandbox denies D: writes, report that limitation instead of claiming migration succeeded. Existing system Python and other user programs must be preserved.

- Never delete the user's source media or replace existing exports. Restrict cleanup to temporary artifacts created by the current task.
- Never publish videos without explicit confirmation. Do not add automatic uploading.
- Keep video, transcripts and analysis local by default. Ask before introducing external processing or paid services.
- Never commit tokens, model weights, source videos, generated exports, local databases or machine-specific settings. Do not ask the user to paste secrets into chat.
- When dependencies are missing, install them in the project environment when permission allows. Request narrowly scoped permissions if required.
- Persist queued/running jobs and recover interrupted jobs after restart. Never silently label an interrupted task as completed.

## Validation and delivery

- Use synthetic media for integration tests. Cover cancellation, invalid ranges, existing output protection, silent video, paths with spaces, job recovery and export correctness as relevant to changes.
- Verify real FFmpeg output with FFprobe, not just command construction.
- Keep requirements pinned and update documentation when behavior changes.
- Save changes to GitHub; prefer a feature branch and draft PR for changes after initial repository bootstrap. Do not merge without explicit authorization.
- Report what works, what was tested, and what remains unavailable.

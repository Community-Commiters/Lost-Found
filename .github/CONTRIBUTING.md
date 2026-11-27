## Git Workflow & Branch Protection

To keep our codebase stable across our 5-person team, direct pushes to `main` and `develop` are disabled.

### Branch Structure
- **`develop`**: Production-ready code only.
- **`prod`**: Pre-production / integration branch where features are tested before merging up into `develop`.
- **`feature/<feature-name>`**: Individual working branches created from `develop`.

### Branch Protection Rules (`develop` & `prod`)
- **Pull Requests Required:** All changes must be submitted via a Pull Request (PR).
- **Code Reviews:** At least **1 teammate approval** is required before merging. Authors cannot approve their own PRs.
- **Stale Approvals Dismissed:** Pushing new commits to an open PR resets existing approvals so new changes are always reviewed.
- **Conversation Resolution:** All review comments must be resolved prior to merging.
- **History Protection:** Force pushes and branch deletions are disabled.

# Moved: this repo is now three

This was the admin app - backend and admin frontend in one repo. On 2026-10-11
it split, with full history, into:

| Repo | What was here |
|------|---------------|
| [**Backend**](https://github.com/Google-Developer-Group-Qassim-Unversity/Backend) | `Backend/` - the one API for every frontend |
| [**Admin**](https://github.com/Google-Developer-Group-Qassim-Unversity/Admin) | `Frontend/` and `design-options/` - the admin panel |

`score-leaderboard-app` became [**Website**](https://github.com/Google-Developer-Group-Qassim-Unversity/Website).

This repo is archived and no longer deploys. Its issues and PRs (#1-#173) stay
here as history. Branches that were open on 2026-10-11 were carried over to the
new repos under the same names.

To move a local branch that never got pushed, from a clone of this repo:

```bash
base=$(git merge-base origin/dev my-branch)
git format-patch --stdout --relative=Backend  "$base"..my-branch | git -C ../Backend am -3
git format-patch --stdout --relative=Frontend "$base"..my-branch | git -C ../Admin am -3
```

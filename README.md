# NSE Rank Tracker
1. `symbols.txt` me apne symbols likho.
2. Repo Settings → Pages → Source: `main` / `/docs`.
3. GitHub token (fine-grained, Actions: Read & write) banao.
4. cronjob.org: POST `https://api.github.com/repos/USER/REPO/actions/workflows/track.yml/dispatches`
   Headers: `Authorization: Bearer TOKEN`, `Accept: application/vnd.github+json`, Body: `{"ref":"main"}`
   Schedule: every 5 min, Mon–Fri, 09:15–15:30 IST.
5. Dashboard: `https://USER.github.io/REPO/`

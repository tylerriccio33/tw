Simple skill - how to do CI. If I'm asking you to call this, I want you to strictly follow the below steps, no need to do anything else.

1. Branch
2. Run pre-commit
3. `git add .` && `git commit -m ...`
4. `git push` to remote
5. Use the gh CLI to open a PR
6. Merge the PR yourself
7. `git checkout main && git pull` to go back to main and get latest.

If this skill is called, you DO NOT NEED my approval to merge the PR; merge it yourself! The goal of this skill is to make it easy to merge PRs without waiting for approval.
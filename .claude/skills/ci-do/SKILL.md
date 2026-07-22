Simple skill - how to do CI.

1. Create a branch describing the change (no required for chores).
2. Your first commit should be a failing test related to the change you are making (again, not always required for chores).
3. The following commits should be nice boundaries.
4. Each commit runs `uvx prek` (py-test, py-sim, cpp-test, cargo clippy) via
   the installed git hook — this is the CI gate for this repo, since there is
   no remote CI pipeline. If a hook fails, fix it before committing; don't
   bypass with `--no-verify`.
5. Push the branch to the remote repository.
6. Create a pull request for the branch.
7. Merge the PR yourself.
8. git checkout main && git pull

If this skill is called, you DO NOT NEED my approval to merge the PR; merge it yourself! The goal of this skill is to make it easy to merge PRs without waiting for approval.
# A secret was committed: what to do

1. Treat it as leaked the moment it reached a remote, even briefly. **Revoke or rotate the credential first.**
2. Find where it is used and replace it from the secret store, not from the repository.
3. Remove it from the working tree and add a scanner rule or allowlist entry only if it was a false positive.
4. Rewriting history (filter-repo) hides it from new clones but not from forks, caches or clones already made; do it only after step 1.
5. Record the incident: what, where, how long exposed, who rotated it.

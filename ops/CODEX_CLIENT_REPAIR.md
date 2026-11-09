# Fixed Codex client repair

The dedicated deployment path leaves the dirty host checkout and .env untouched.
It builds a small derived image from the running image, with no RUN network access,
and applies only the reviewed, hash-pinned client/adapter patch. Compose environment,
bind mounts and host networking must match the running container before cutover.

A PID-1 timer restores the original image alias and recreates Jarvis after 180s
unless explicit confirmation succeeds. Confirmation checks the installed source,
container health and isolated adapter generation/resume. It does not claim DingTalk
acceptance. The original image stays available; only Jarvis is recreated.

The existing destructive `upgrade_ops_extension` entry exposes an explicit
`component="codex-client"`, `operation="apply|confirm|rollback"`, with a change ID
for the latter operations. It accepts only the currently installed committed Ops
release and its exact package digest, never a caller path, URL, shell or Dockerfile.

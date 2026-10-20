# t507-arm64 example

Copy `.ferry.yaml` to your project root and adapt the node aliases, paths and
toolchain probe. The scripts illustrate the supported stage locations; they are
intentionally safe to run repeatedly.

## Manual acceptance checklist (requires two real Linux nodes)

Prepare `~/.ssh/config` aliases for `buildvm` and `target-t507`, then:

1. `ferry up --ssh buildvm --target target-t507 --name t507_arm64 ... --yes`
   - verifies SSH reachability and writable directories
   - installs the probe and prints a config diff
2. `ferry plan t507_arm64`
   - shows `build -> target` routing, the `XB_*` environment, and all steps
3. `ferry run t507_arm64`
   - syncs the workspace without rsync
   - streams build output line by line
   - pushes artifacts from build to target directly
   - runs target scripts with `sh` and cleans up the temp script
4. `ferry status`
   - every stage is `ok`; log path exists
5. Interruption:
   - start a long build and press Ctrl-C
   - confirm the marker `/work/<project>/.ferry/<target>.marker` remains
   - rerun `ferry run`: output shows `[recovery] previous build was interrupted`
     and the build directory is cleaned once
6. Concurrency:
   - start one run, start a second run in another terminal
   - the second reports the build lock is busy and does not kill the first
   - `ferry run <target> --cancel` sends SIGTERM; the build gets 15 seconds to exit
7. Host key rotation:
   - recreate the VM / container and rerun `ferry up --reset`
   - the failure message points to `ssh-keygen -R`

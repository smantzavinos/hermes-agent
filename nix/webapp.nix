# nix/webapp.nix — the browser-hosted Desktop renderer (apps/desktop/dist-webapp)
#
# The gateway serves this bundle when a home's config sets `webapp.enabled`
# (upstream PR #141864). A source install builds it on demand with `hermes
# webapp`, which runs npm inside a private workspace mirrored beside the
# checkout; the Nix store is read-only and carries no `apps/` tree, so the
# bundle is built here instead and deployments point the backend at it with
# HERMES_WEBAPP_DIST (hermes_cli/webapp.py reads it; a deployment-provided
# bundle matches the backend it ships with and is never rebuilt or marked
# stale). Build with the runtime flow's own mirror (`mirrorWebappInputs`) and
# compiler (`buildWebapp`); the sandbox's offline root `node_modules`
# (installed by the npm hook from the same lock the runtime flow uses) stands
# in for the private workspace's `npm ci` — the sandbox tree is disposable, so
# pruning the native desktop install cannot matter here.
{ hermesNpmLib, ... }:
hermesNpmLib.buildNpmPackage {
  dirs = [
    "apps/desktop"
    "apps/shared"
    ".npmrc"
    "pm/lock.json"
    "scripts/build/webapp.mjs"
    "scripts/build/node-deps.mjs"
    "scripts/build/electron-runtime.mjs"
    "scripts/build/frontend-common.mjs"
    "scripts/build/freshness.mjs"
  ];

  pname = "hermes-webapp-renderer";

  doCheck = false;

  buildPhase = ''
    runHook preBuild

    node --input-type=module -e '
      import { existsSync, mkdirSync, symlinkSync } from "node:fs";
      import { join } from "node:path";
      import { mirrorWebappInputs } from "./apps/desktop/scripts/build-webapp.mjs";
      import { buildWebapp } from "./scripts/build/webapp.mjs";

      const source = process.cwd();
      const workspace = join(source, ".build/webapp-workspace");
      mkdirSync(workspace, { recursive: true });
      mirrorWebappInputs(source, workspace);
      symlinkSync(join(source, "node_modules"), join(workspace, "node_modules"));
      // Version-conflicted deps nest under apps/desktop in the lock; the
      // workspace resolves them through the prepared install as well.
      const nested = join(source, "apps/desktop/node_modules");
      if (existsSync(nested)) symlinkSync(nested, join(workspace, "apps/desktop/node_modules"));
      const result = await buildWebapp({
        source,
        workspace,
        out: join(source, "apps/desktop/dist-webapp"),
      });
      console.log("built " + result.index);
    '

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p $out
    cp -r apps/desktop/dist-webapp/. $out/
    runHook postInstall
  '';
}

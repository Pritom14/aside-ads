# Releasing

A release is a `v*` tag. Pushing it runs [`.github/workflows/release.yml`](.github/workflows/release.yml),
which publishes:

| Artifact | Where | Credential |
| --- | --- | --- |
| `aside-ads` (server, sdist and wheel) | PyPI | Trusted Publishing, GitHub environment `pypi` |
| `aside-ads-client` (Python SDK) | PyPI | Trusted Publishing, GitHub environment `pypi-client` |
| `aside-ads` (JS SDK) | npm, with provenance | `NPM_TOKEN` repository secret |
| Server image | `ghcr.io/pritom14/aside-ads` | `GITHUB_TOKEN` (automatic) |

If a credential is missing, that publish job is skipped with a notice in the run summary and the others
still go out. After adding the credential, re-run the workflow (Re-run all jobs): versions that are
already on PyPI or npm are skipped, so only the missing piece is published.

## Versions

Each package has exactly one version string:

- `aside-ads`: `__version__` in [`src/aside_ads/__init__.py`](src/aside_ads/__init__.py)
- `aside-ads-client`: `__version__` in [`sdks/python/src/aside_ads_client/__init__.py`](sdks/python/src/aside_ads_client/__init__.py)
- npm `aside-ads`: `version` in [`sdks/js/package.json`](sdks/js/package.json) (use `npm version` so
  `package-lock.json` follows)

The packages are released together, so all three must equal the tag without its `v`. The workflow
stops before publishing anything if they differ. All three are `0.1.0` today.

## One-time setup (repository owner)

The release must run from the public repository: npm provenance is only accepted for public sources.

1. **PyPI pending publishers.** Sign in at <https://pypi.org/manage/account/publishing/> and add two
   GitHub pending publishers:

   | PyPI project name | Owner | Repository name | Workflow name | Environment name |
   | --- | --- | --- | --- | --- |
   | `aside-ads` | `Pritom14` | `aside-ads` | `release.yml` | `pypi` |
   | `aside-ads-client` | `Pritom14` | `aside-ads` | `release.yml` | `pypi-client` |

   The environments differ on purpose: PyPI refuses a second pending publisher with the same owner,
   repository, workflow and environment, even for a different project name. The first successful
   upload turns each pending publisher into a normal trusted publisher for its project.

2. **GitHub environments.** In the repository, Settings > Environments > New environment, create `pypi`
   and `pypi-client`. Optional but recommended: under Deployment branches and tags, allow only tags
   matching `v*`, and add yourself as a required reviewer so every upload waits for a click.

3. **npm token.** On npmjs.com, Access Tokens > Generate New Token > Granular Access Token, with
   Packages and scopes: Read and write, All packages (the package does not exist yet), and Bypass
   two-factor authentication enabled so CI can publish. Add it in GitHub under Settings > Secrets and
   variables > Actions > New repository secret, named `NPM_TOKEN`. npm write tokens expire, so renew
   it before a later release (or switch the job to npm trusted publishing once the package exists).

4. **Tag the release.** From an up-to-date `main` with the CHANGELOG entry merged:

   ```bash
   git tag v0.1.0 && git push --tags
   ```

GHCR needs no setup. After the first push, check that the `aside-ads` package under your GitHub profile
is public (Package settings > Change visibility) so `docker pull ghcr.io/pritom14/aside-ads` works
without logging in.

## Later releases

1. Bump the three versions and add a CHANGELOG section in a pull request.
2. Merge it, then tag `main`: `git tag vX.Y.Z && git push --tags`.
3. Watch the Release workflow. Check the run summary for skip notices.

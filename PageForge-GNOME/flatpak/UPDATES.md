# Flatpak updates through GitHub Pages

The GNOME edition is published as a signed Flatpak repository on GitHub Pages:

- Install page: <https://acknar.github.io/PageForge/>
- One-click install: <https://acknar.github.io/PageForge/pageforge.flatpakref>
- Repository: <https://acknar.github.io/PageForge/repo/>

GNOME Software checks this repository for updates. Every version tag (`v*`) rebuilds
the Flatpak for x86_64 and aarch64, signs it and replaces the repository contents.

The `.flatpak` files attached to releases also point at this repository, so a bundle
installed by double-click updates the same way.

## One-time setup

### 1. Create the signing key

On your own machine (Fedora shown; any Linux with GnuPG works). The key has no
passphrase because CI uses it unattended, so keep it only in the GitHub secret and
in a backup.

```sh
export GNUPGHOME="$(mktemp -d)"
gpg --batch --passphrase '' --quick-gen-key "PageForge Flatpak repository" rsa4096 sign never
gpg --armor --export-secret-keys "PageForge Flatpak repository" > pageforge-flatpak-signing-key.asc
```

Store `pageforge-flatpak-signing-key.asc` somewhere safe (a password manager works).
Losing it means existing installs stop accepting updates until people reinstall.

### 2. Add it as a repository secret

GitHub → **Acknar/PageForge** → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**:

- Name: `FLATPAK_GPG_PRIVATE_KEY`
- Secret: the whole content of `pageforge-flatpak-signing-key.asc`, including the
  `BEGIN` and `END` lines.

Or with the GitHub CLI: `gh secret set FLATPAK_GPG_PRIVATE_KEY --repo Acknar/PageForge < pageforge-flatpak-signing-key.asc`

Then remove the temporary key folder: `rm -rf "$GNUPGHOME"`.

### 3. Turn on GitHub Pages

1. **Settings** → **Pages** → **Build and deployment** → **Source**: **GitHub Actions**.
2. **Settings** → **Environments** → **github-pages** → **Deployment branches and tags**:
   add a rule for tags matching `v*`. Pages only accepts deployments from `main` by
   default, and the repository is published from version tags.

The next version tag publishes the repository. The Actions run shows a **publish-repo**
job, and the install page appears at the address above.

## Without the key

Builds work as before when the secret is missing: the Flatpak is built and attached to
the release, but it carries no update repository and nothing is published to Pages.

## Moving from io.local.PageForge (1.1.0)

Version 1.1.0 used the app ID `io.local.PageForge`. From 1.2.0 the ID is
`io.github.acknar.PageForge`, and Flatpak treats it as a different app. On first launch
the new app copies the old settings, scripts folder and installed tool libraries. After
checking that everything is there, remove the old one:

```sh
flatpak uninstall io.local.PageForge
```

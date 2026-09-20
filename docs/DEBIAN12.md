# Agent 8 on Debian 12: build and installation instructions

## 1. Read this first

These instructions reconstruct the configuration working on Larry's Debian 12
amd64 computer with KDE/X11. **They have not been tested end to end on a clean
Debian installation.** The dependency list may need adjustment on another host.
We chose to publish that limitation rather than delay useful instructions for
an additional clean-machine build.

What was verified here: the patched Wine runtime, a fresh Agent MSI installation
in an empty prefix, graphical startup, canceling the setup wizard, navigating
the default folders, and normal exit. The fresh-profile test was isolated from
providers and contained no real accounts or mail. The older established profile
also works, as described in the [experience report](EXPERIENCE.md).

The two shell recipes supplied below use those build/install settings. They have
been syntax-checked and reviewed, but **these exact new wrappers have not been
run through a full rebuild and installation**. They are readable conveniences
around Git, curl, configure, make and Wine—not a turnkey installer or a support
guarantee. Read them before running them. Stop on failure rather than deleting
files and blindly retrying; preserve the logs.

You need your own legitimately obtained Agent 8 installer and license. Neither
is supplied. These basic recipes do **not activate** the custom POP bridge or its
no-delete enforcement. The [portable POP source](../pop-bridge/USAGE.md) is included
elsewhere in this repository as a separate experimental component. These recipes
do not block networking or protect server mail from deletion if you later
configure accounts. A Wine prefix is not a security sandbox.

## 2. Before starting

1. On your Linux desktop, as your ordinary user, verify that this is **Debian 12
   amd64 with an X11 session**. Stop if it is not; Mint and Wayland have not been
   tested by this project. Do not replace your system Wine or migrate your live
   archive as part of this first installation.
2. In your browser, open the [GitHub repository](https://github.com/LDighera/forte-agent-wine),
   choose **Code → Download ZIP**, and extract into a new directory (or clone it).
   On your Debian desktop, open a terminal in the extracted repository's **debian/**
   subdirectory. Expect debian12-packages.txt, build-wine-debian12.sh and
   agent-debian12.sh there; this guide is in the sibling docs/ directory. Stop
   if the directory layout differs or private data/binaries are present.
3. On the same desktop, review the scripts and package list in a text editor.
   The Wine build downloads source, compiles it and installs below a new directory
   you choose. It does not run sudo. The Agent helper installs your MSI into a
   separate empty prefix or opens that prefix. It does not import accounts.
4. Allow several GiB for source and build products, additional space for the
   installed runtime, and roughly 1.2 GiB for the fresh prefix observed here.
   Build space is not a measured maximum; monitor free space. The default build
   uses four parallel jobs and may take considerable time. Stop on disk errors.

## 3. Install Debian prerequisites

On your Debian 12 computer, in the terminal opened in the repository's debian/ directory,
refresh Debian's package metadata. This is an administrator operation through
sudo; it is not a Wine invocation. Expected result: metadata refresh completes.
Stop on repository or signature errors. Copy this command into that terminal:

> sudo apt-get update

Then install the packages named in the supplied text file. Review apt's proposed
changes before confirming; stop if it proposes unexpected removals or a release
upgrade. Copy this command into the same Debian terminal:

> sudo xargs -r -a debian12-packages.txt apt-get install --no-install-recommends

The list covers compilers, LLVM 14, parser tools, library discovery, X11, fonts,
TLS and basic OpenGL. It is reconstructed from the working host, not a proven
minimum for every installation. It does not add an i386 apt architecture: the
tested build uses Wine's new dual-architecture WoW64 configuration.

Sound and printing are outside this recipe's tested scope. The configure check
reported missing ALSA/Pulse and CUPS development support. Do not interpret a
successful Agent launch as proof that notifications, printing, HTML content or
multimedia work. Wine Gecko was present in the established installation, but
we did not qualify fresh-profile HTML display. No Microsoft .NET installation
was needed for the functions tested here.

## 4. Build the separate Wine installation

On your Debian desktop, as your ordinary user in the repository's debian/ directory, use the
following command. It creates a NEW directory named agent-wine-debian12 in your
home directory. Stop if that directory already exists; the recipe refuses to
overwrite or reuse it. **Do not use sudo.** Copy into that terminal:

> bash build-wine-debian12.sh "$HOME/agent-wine-debian12"

The recipe selects the Wine 11.14 release, checks its recorded Git commit,
applies the upstream raw-input fix, verifies the modified file, configures in
a separate build directory, and builds/installs the private runtime. It puts
LLVM 14's tools first in PATH for this process only. It leaves system Wine alone.

Expected result: "Wine build complete" with a wine-install path. Stop if any
command fails or if relevant configure warnings indicate missing X11, font or
TLS support. A failure leaves the new build area for inspection; do not simply
rerun the recipe or remove that directory. It has no automatic repair/retry mode.

Build records are configure-output.txt, build-output.txt and install-output.txt
inside the new build directory, together with Wine's config.log. Review logs
before sharing; they can contain your local paths even though no account data
is needed for the build. The build does not create an Agent prefix.

This pins the combination tested here, not a recommendation that all newer
Wine versions require this patch. Both patched and unpatched builds can report
wine-11.14. The source check matters; a version string alone is insufficient.

## 5. Install your own Agent copy

On your Debian desktop, place your own original **agentenu800-1272.msi** in the
repository's debian/ directory. Review the applicable license terms before
using the silent installation command. Do not obtain the installer from an
untrusted substitute. The installer is not supplied by this repository; never
commit or upload it. The repository's ignore patterns are not a privacy guarantee.

As your ordinary user in that folder, run the following command **without sudo**.
It uses the private Wine build and creates a fresh agent-prefix beneath the build
area. It refuses an existing prefix or install log. Copy into that terminal:

> bash agent-debian12.sh install "$HOME/agent-wine-debian12" ./agentenu800-1272.msi

Expected result: "Agent installed; no account or mail archive imported."
Stop on an error or unexpected prompt; inspect agent-install.log in the build
area. No automatic MSI retry is included. Wine prefix initialization can take
a little time, so absence of a visible installer progress window is normal.

The tested MSI installs Agent beneath C:\Program Files (x86)\Agent. The recipe
sets WINEARCH=wow64 and disables mscoree and automatic Wine menu generation for
these processes. Do not change the architecture of an existing prefix or use
this recipe to reinstall over your only working Agent environment.

## 6. First opening: no accounts or old archive

On your Debian X11 desktop, as the ordinary user in the repository's debian/ directory, launch
the new installation using the following command. Do not run it twice while
the same Agent instance is open. Copy into that terminal:

> bash agent-debian12.sh open "$HOME/agent-wine-debian12"

1. In this NEW Agent window, expect first-run welcome/setup dialogs—not your
   existing folders or messages. Stop if an old archive appears or an error
   occurs. Do not enter real server credentials, import a profile, poll, or send.
   If license information is required, handle it privately; never put a key in
   a public report. Exact welcome/license choices were not fully recorded here.
2. At **Agent Setup Wizard**, click **Cancel**. In our test the main Agent window
   remained, with Inbox, Drafts, Outbox, Sent, Trash and Junk and Offline status.
   Stop if the outcome differs; do not configure a server to work around it.
3. In that empty main window, select **Inbox**, then **Drafts**. Expect empty,
   responsive panes. Stop on a hang or error. We did not test composition in this
   fresh no-account profile; do not infer it from this navigation check.
4. With Agent responsive and no popup visible, press **Alt+F4** once to close the
   outer application. Expect the window to disappear and the terminal to report
   that Agent and its Wine processes exited normally. If hung or a popup appears,
   stop and inspect rather than force-close or launch another copy.

Our fresh GUI test created AGENT.INI below the Windows user's
AppData\Roaming\Forte\Agent directory. The username differs between installations.
That file is private and can later contain account settings; **never upload it
with a public guide or troubleshooting report**. No AGENT.INI is in this ZIP.

The recipe's networking is not restricted. Our test used a separate local
sandbox; these portable helpers do not recreate it. Offline status in Agent is
not a firewall. Start with an empty profile and no configured accounts, and
arrange independent network isolation before experimenting with imported ones.

## 7. Existing archive and online services are separate steps

Once basic operation works, use the profile-preservation checklist in the
[experience report](EXPERIENCE.md). Close the Windows source, retain a verified
backup, and work on an independent copy of the COMPLETE profile. Do not share a
writable store between Windows and Wine or hard-link it to the only backup.

This basic installation does not activate the POP bridge, duplicate tracking,
no-delete enforcement, SMTP relay or NNTP relay. After it works, the optional
[POP guide](../pop-bridge/USAGE.md) explains the separately supplied receiving
component and its additional Python/Bubblewrap prerequisites. SMTP and NNTP
relays are not packaged here. Do not poll a real mailbox assuming the custom
no-delete guarantee applies to an ordinary Agent configuration; saved filters
can override account-level retention settings.

## 8. Feedback and sources

Please report the Debian version/architecture, Agent version, failing numbered
step and a short sanitized error. Missing prerequisites on a different Debian
installation are useful feedback. Do not post profiles, credentials, license
keys, mailbox inventories or unreviewed logs.

- [Wine 11.14 release source](https://github.com/wine-mirror/wine/tree/wine-11.14)
- [Upstream raw-input correction](https://github.com/wine-mirror/wine/commit/a1bae27f21b53fa3ac6be6b08f4cbfb441360ade)
- [Debian Clang 14](https://packages.debian.org/bookworm/clang-14)
- [Debian LLVM 14](https://packages.debian.org/bookworm/llvm-14)
- [Debian pkgconf](https://packages.debian.org/bookworm/pkgconf)

Prepared September 19, 2026; repository layout updated September 20, 2026 by
Larry Dighera with OpenAI Codex. The Wine patch
is upstream Wine work. This is not an official Forté, Wine or OpenAI support
product. Clean-Debian rebuild verification was intentionally deferred.

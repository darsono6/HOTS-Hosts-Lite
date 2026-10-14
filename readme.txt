HOTS Hosts Lite v1.2
====================

A desktop application for managing the Windows hosts file.
Part of the HOTS Tools family. Free and open-source, released under GPLv3.


OVERVIEW
--------
HOTS Hosts Lite lets you view, edit, and manage the Windows "hosts" file
through a clean, modern interface, instead of manually editing it in
Notepad with administrator permissions.

Requires Windows 10 or Windows 11 (64-bit). Must be run as Administrator,
since the hosts file is write-protected by Windows. The installer /
shortcut is already configured to request this automatically.


MAIN FEATURES
-------------
- Real-time table view of all hosts entries, with add/edit/delete/toggle
- Live search, bulk paste, and column sorting
- One-click Repair: removes duplicates, invalid lines and wildcards, and
  normalizes host names
- Automatic backups before every save (15 kept on the default profile,
  10 each on profiles 2 and 3), with a Backup Manager and one-click
  restore of the default Windows hosts file
- Diff preview before writing changes to disk
- DNS cache is flushed automatically after every save
- Import/export to .txt (hosts format) or .csv
- Domain diagnostics: DNS existence check and a heuristic malware/
  phishing scanner (typosquatting, suspicious TLDs, homoglyphs, etc.)
  with an ignore list for false positives
- Block your own domains: a free-text blocklist - type in any domain
  and it's blocked at the hosts-file level
- Hosts file lock: locks the hosts file itself against changes/deletion
  by other, non-elevated programs; the app notices if the lock is removed
  from outside
- Application blocking: prevent chosen programs from reaching the
  network at all
- 3 switchable hosts profiles: keep separate configurations and swap
  between them instantly
- Internet speed test: download, upload and ping, a history of results
  and a quick rating for web browsing, gaming, streaming and live video
  calls (uses your full connection for a few seconds - avoid it on a
  metered connection)
- DNS speed test: compares how fast your DNS server answers against
  Cloudflare, Google, Quad9 and OpenDNS, with a plain-language verdict;
  one click sets a public DNS, and you can restore your previous DNS or
  automatic DNS at any time
- Light and dark themes with 5 accent colors
- Built-in update checker (via GitHub Releases); checks on startup by
  default, can be switched off in Options > About
- Optional password protection (salted hash stored machine-wide in the
  Registry)
- Raw hosts file editor with syntax highlighting
- Available in 8 languages: English, Polish, French, German, Spanish,
  Portuguese, Russian, Italian


WHAT'S NEW IN 1.2
-----------------
- Internet speed test: download, upload and ping, with a history of
  results and a rating for web browsing, gaming, streaming and live
  video calls
- DNS speed test: compares your DNS server with popular public ones
  (Cloudflare, Google, Quad9, OpenDNS) and tells you whether switching
  would help; one click sets a public DNS, and you can restore your
  previous DNS (or automatic DNS) at any time - the uninstaller can
  restore it too
- Italian language added: the interface is now available in 8 languages,
  and the app starts in your Windows language when it is supported
- Minor improvements throughout the application's code


WHAT'S NEW IN 1.1
-----------------
- Significantly lower resource usage: the app no longer depends on
  PySide6-Fluent-Widgets and runs on its own lightweight window and widgets
- Code optimization throughout the application
- A fifth accent color
- Refreshed look and a number of small UI fixes
- Uninstall wizard: instead of an all-or-nothing removal, the uninstaller
  asks exactly what to undo (domain and app blocks, saved configuration,
  your custom domain list - kept by default - and hosts file backups)
- Uninstalling no longer requires the startup password; the password still
  protects opening the app itself
- Firewall rules follow program updates: programs installed in
  version-numbered folders (e.g. Edge, Discord) are blocked at their
  current version, and the rule moves automatically after an update (when
  the app starts or you open the Features page)
- Entries with the same file name in the firewall list are shown with
  their folder, and the installed-programs picker no longer guesses an
  unrelated executable
- Reinstalling or updating over an existing install starts from a clean
  installation folder


DISCLAIMER
----------
HOTS Hosts Lite is provided in good faith but without any warranty. The
author is not responsible for any damage, data loss, system issues, or
other consequences resulting from the use of this application. Modifying
the hosts file affects system-level network resolution - use with care.
You use this software at your own risk.


SUPPORT
-------
If HOTS Hosts Lite saves you time, or you'd simply like to say thanks:

  Ko-fi:   ko-fi.com/darsono
  PayPal:  paypal.me/darsonodark
  Email:   hots.support@gmail.com
  www:     hotstools.com

No registration required. Any amount is appreciated.


LICENSE
-------
GNU General Public License v3.0
(c) 2026 Darsono

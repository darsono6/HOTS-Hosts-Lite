HOTS Hosts Lite v1.3
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
- Light and dark themes with 6 accent colors, Mica or Acrylic backdrop
- Built-in update checker (via GitHub Releases); checks on startup by
  default, can be switched off in Options > About
- Optional password protection (salted hash stored machine-wide in the
  Registry)
- Raw hosts file editor with syntax highlighting
- Available in 8 languages: English, Polish, French, German, Spanish,
  Portuguese, Russian, Italian


WHAT'S NEW IN 1.3
-----------------
- New visual effects: Acrylic material next to Mica, a glow effect with
  adjustable strength (transparency in Acrylic mode), and a switch to turn
  window effects off
- New Graphite accent color (6 accent colors in total)
- Rebuilt Options page: expandable cards for Language and Appearance, plus
  Updates (check on startup, manual check), About and Support
- License information: the About page opens the full license and the
  third-party notices
- Updates: clicking an available update now downloads the installer
  directly, instead of only opening the repository page
- Copy diagnostics: a new button on the About page copies anonymized
  system and program information to the clipboard, ready to paste into
  a bug report
- Improved blocking of your own domains: the feature has been reworked
  to work better
- Improved Internet speed test and Firewall: both features have been
  reworked to work better
- Several smaller fixes and improvements throughout the application


DISCLAIMER
----------
HOTS Hosts Lite is provided in good faith but without any warranty. The
author is not responsible for any damage, data loss, system issues, or
other consequences resulting from the use of this application. Modifying
the hosts file affects system-level network resolution - use with care.
You use this software at your own risk.


BUG REPORTS
-----------
Found a bug? In the app open About -> Copy diagnostics and paste the
result into your report (github.com/darsono6/HOTS-Hosts-Lite/issues or
email). It contains the app and Windows version and the end of the error
log; your user name and computer name are replaced. The log may include
names of programs or domains mentioned in error messages - you can review
it before pasting.


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

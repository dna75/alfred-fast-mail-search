# Fast Mail Search for Alfred

Search Apple Mail instantly from [Alfred](https://www.alfredapp.com), like Spotlight does: by name, email address, subject, text or date. The workflow reads Mail's local index (read-only), so results appear as you type.

![icon](workflow/icon.png)

## Install

Download `Fast.Mail.Search.alfredworkflow` from the [latest release](https://github.com/dna75/alfred-fast-mail-search/releases/latest) and double-click it. Requires the Alfred Powerpack.

## Usage

| Keyword | Searches | Example |
|---|---|---|
| `mail` | everything: subject, sender, recipients and message preview | `mail jane invoice` |
| `mailfrom` | sender only | `mailfrom jane` |
| `mailto` | recipient only | `mailto jane` |
| `mailsubject` | subject only | `mailsubject invoice` |
| `maildate` | by date: pick from a list or type a date, optionally with search words | `maildate`, `maildate 2026-11 jane` |

All words must match. Prefixes also work in the main search: `mail from:jane subject:invoice`.

**Dates.** Type `maildate` and pick *Today*, *Yesterday*, *Last 7 days*, *Last 30 days*, *This month*, *Last month*, *This year* or *Last year* with <kbd>↩</kbd> or <kbd>⇥</kbd>; add search words afterwards if you like. You can also use `date:`, `after:` or `before:` in any search, e.g. `mail invoice after:2026-11-01`. Accepted: `today`, `yesterday`, `week` (last 7 days), `month` (last 30 days), `2026-11-18`, `18-11-2026` (day first), `18-11` (this year), `2026-11`, `11-2026` and `2026`.

**Use your own keywords.** All keywords can be changed in the workflow configuration (Alfred Preferences → Workflows → Fast Mail Search → Configure Workflow), for example to words in your own language.

- <kbd>↩</kbd> open the message in Mail
- <kbd>⌘</kbd><kbd>↩</kbd> copy the sender's email address
- <kbd>⇧</kbd> or <kbd>⌘</kbd><kbd>Y</kbd> Quick Look: preview the message without leaving Alfred (shows the full message when its file is on this Mac, otherwise a summary)
- <kbd>⌘</kbd><kbd>L</kbd> large type with the message preview

Unread messages are marked with a dot. Junk and trash are skipped.

## Requirements

- **Full Disk Access for Alfred** (System Settings → Privacy & Security → Full Disk Access), needed to read `~/Library/Mail`. The database is opened read-only; nothing is written or sent anywhere.
- **Python 3**: Homebrew's or Apple's (part of the Command Line Tools). If it is missing, the workflow says so and <kbd>↩</kbd> starts Apple's installer.

## Limitations

- Only the message preview (first lines) is searched, not the full body.
- Developed and tested on macOS 27. Mail's internal database layout differs between macOS versions; the workflow detects the layout and adapts. Older layouts are supported on a best-effort basis (checked against reconstructed databases, not on real older systems): there, messages are opened via their message file, which can take a moment. If results look wrong, run `./mail_search.py --schema` in the workflow folder and open an issue with the output.

## Disclosure

This workflow was created with the help of an AI assistant (Claude by Anthropic) and tested by the author.

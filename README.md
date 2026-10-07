## ChatGPT capture integration (current implementation)

Capture now supports **ChatGPT / Codex**, using the installed Codex CLI's managed ChatGPT sign-in. New settings default to Codex; saved provider choices remain unchanged. In Settings, choose ChatGPT / Codex and save, then use Test connection. If needed, install/update Codex and run `codex login` in Terminal. No API key or credential extraction is used.

`codex_capture.py` invokes supported `codex exec` with user config/rules ignored, ephemeral sessions, shell and unified execution disabled, coding/browser/app/plugin hooks disabled, web search disabled, and a read-only temporary working directory. Authentication remains managed by Codex. Structured output drafts one task; saving still requires review. API-key environment overrides are removed. Old CLIs fail closed. Failed inference falls back to offline parsing with a clear message. Account sign-in and successful inference are reported separately. No chat history or memory is inherited.


# Aura Desk

A personal dashboard that opens at http://127.0.0.1:8765 in its own browser tab. No login, no hosted service, no npm packages. Python 3.10 or newer is required.

## Start on your Mac

1. Unzip the folder and move it somewhere permanent, such as `Documents/aura-desk`.
2. Open Terminal in that folder and run:

   ```bash
   python3 server.py
   ```

   Or double-click `Start-Aura-Desk.command`. If macOS refuses the downloaded launcher, use the Terminal command above. If Python is missing or older than 3.10, install a current Python release from https://www.python.org/downloads/macos/.

3. The dashboard opens in your usual browser. Bookmark it. Keep the Terminal running for background checks; closing the browser tab is fine.

Optional: start automatically when you sign into your Mac, and restart after a process crash:

```bash
python3 server.py --install-autostart
```

Run this only after moving the app to its permanent location. The agent uses the Python executable and app path at installation time. Don't move the folder afterward. Stop any manually started copy with Ctrl+C first. The background version opens at the same address; it does not pop open a browser at login.

To disable autostart:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.auradesk.plist
```

## Daily workflow

- **Quick add:** title and category are sufficient. Main URL is prominent. Add course/company, date and exact time, estimated minutes, a start-by date, one next action, resources, and notes as useful.
- **Today:** choose up to three finishable actions. Urgent commitments appear once, with urgency shown directly on selected actions. Suggestions and optional work blocks start collapsed. Upcoming deadlines remain visible in the right-hand panel (stacked on narrow screens). The estimate sums known action minutes and labels unknowns. OAs are suggested ASAP; reached start-by coursework stays visible alongside them. Nothing automatically fills your three.
- **Coursework:** Homework, Project, Reading, Quiz, Exam, and Other. Defaults to unfinished work; Completed history is an explicit filter. Filter/search by course or task.
- **Project planning:** optional inside the project editor. Enter steps forward or backward; the plan always displays execution order. Edit, move up/down, reopen, or remove/restore steps. Switching direction preserves text and progress. Existing next actions can be kept as the first step or replaced.
- **Finish action:** completes only the current step/action, keeps the parent active, and clears its Today slot. Choose whether to add the next action to Today. The last step never automatically completes the project. Use the separately labeled Complete project/Submitted action for the parent.
- **Shortcuts:** Manage Canvas links near the header; saved links survive restart and are included in SQLite backups/export. Course shortcuts and Open LeetCode open new tabs.
- **Opportunities:** refresh roles from Simplify/Coder Quad and Vansh's new-grad list. Mark Applied or Hide; Hide means archive. Add roles from your own sources. Role presence in a community list does not prove eligibility or that the employer is still accepting applications. Check the posting, especially graduation year. The Simplify source currently describes 2025/2026 as well as containing some 2027-specific roles; verify that each role matches your graduation year.
- **Daily habits on Today:** completed LeetCode, job applications, and system design show a Done today checkmark; their Log buttons disappear for the day. Small flames appear at a streak of three and larger flames at five. The five latest logged events appear below the buttons; Show more opens History. Logs have Edit and Delete controls; Delete requires confirmation and reconciles points/streaks. There is no separate Practice tab. Effort-based LeetCode streak; yesterday's streak remains visible before today's session. Attempts and reviews count. System design is tracked separately. Extra sessions do not multiply points within the same activity/day. Undo incorrect practice logs.
- **Rewards:** shared cumulative balance. Add your own treats and thresholds. Unlocked treats stay unlocked after deductions; claiming never spends points.
- **Archive:** recover any hidden role or archived task. No permanent delete button.

## OA rules

- LeetCode and job applications have independent streaks. Consecutive qualifying days earn +10, +12, +14, +16, +18, then +20 per day. Completing a day increases the next-day award by +2, capped at 20 total. Missing a day resets that activity to +10; there is no extra deduction. Meaningful attempts/reviews count for LeetCode. System design starts at +15, increases by +2 per continued streak session, and caps at +25. Sessions up to three Detroit calendar days apart keep the streak; a gap longer than three days resets the next award to +15. Awards remain once per day, and existing awards are preserved when upgrading.
- Aim for one actual job application daily. Log it from Today or mark an opportunity Applied. Applied logs today once; correct the actual date or undo erroneous logs in Recent activity on Today. Extra applications do not multiply daily points. Undo applied/Hide does not erase application history. Old Applied statuses are not fabricated into dated logs.
- Historical practice awards remain unchanged when upgrading. The new reward policy activates on first startup after upgrading; existing LeetCode days can seed the ongoing streak. Post-activation corrections/Undo recalculate affected streak awards. Pre-activation application dates are recorded as history without retroactive awards.
- OA submitted on time: +30. Submitted at least 48 hours early: +10 extra.
- Missed OA: −30, equivalent to three regular LeetCode practice days.
- At expiry, an unfinished OA is automatically marked missed. Acknowledge removes its prominent Today warning while retaining history, points, and legitimate emails. “I submitted on time” lets you record the actual submission date/time and replaces the deduction with the appropriate completion points. A genuinely late submission earns no on-time points.
- A deadline date without a time is visibly flagged and **never automatically penalized or emailed**. Enter exact times for OAs. All dates use America/Detroit, including daylight saving time.
- Archiving a missed OA keeps its penalty. Restoring it lets you correct its submission. Archived active items are excluded from deadline checks.
- Daily priorities apply to the selected day. They are not silently carried forward; unfinished tasks remain suggested the next day.

## Capture / AI

“Capture with chat” drafts a task from a pasted message and opens the editable form before saving. Without a key it uses a local parser (dates like 9/30, ISO dates, today/tomorrow/weekdays, am/pm times, URLs, and minute estimates). It is **not an LLM chatbot** in this mode. Ambiguous dates need review; absent times stay blank.

Offline capture is the default. For optional AI extraction, select Gemini as the capture provider in Settings, enter a Gemini key, and select a model available to your account. The initial model name is `gemini-3.8-flash`; change it if your account doesn't offer it. API usage may be billed by Google. Only the message you submit for extraction is sent to Google. Keys are stored server-side, never returned to the browser. This first version supports task capture, not general conversation or arbitrary automatic editing of existing records.

ChatGPT / Codex capture uses the locally installed CLI and its managed sign-in. Credentials stay on your computer. Use Test connection to check inference availability; failed capture falls back to an offline draft that requires review.

## Email setup

Emails are **off initially**. In Settings, enter your school email as recipient and an SMTP sender account with an app password. Choose SSL (usually port 465) or STARTTLS (usually 587), according to that provider's instructions. Your school's account does not have to be the sender; school-managed accounts may disable SMTP/app passwords.

Only recruiting deadlines trigger email:

- A single alert when an active OA/interview/virtual assessment enters the last 48 hours.
- A separate missed-OA alert when an active OA expires unfinished.
- No coursework emails, no push notifications, no daily digest when nothing is urgent.

Events are checked every 30 seconds; pending events are combined and delivered at most once every 15 minutes to reduce spam. The same task/deadline does not receive repeated successful alerts. Failures remain queued for retry. Correcting or completing an item cancels pending notices; already delivered emails can't be retracted. SMTP delivery has a rare duplicate window if the process crashes after sending but before recording success.

The laptop must be awake, connected to the internet, and running the service. On resume/restart, overdue OAs are caught up immediately, and notices are queued. If email is enabled after an item is already marked missed, old missed emails aren't retroactively sent.

## Persistence and recovery

Database: `~/Library/Application Support/AuraDesk/dashboard.sqlite`.

On the first productivity upgrade, a separate `pre-productivity-*.sqlite` snapshot is taken before additive schema changes; existing daily snapshots are retained. Old item records need no destructive conversion. Do not run older app code against newly edited plans: its old allowlist can strip new fields. SQLite transactions and WAL protect committed changes across crashes. App code and personal data are separate: replacing the downloaded app folder does not reset the database. A daily database snapshot is kept under `backups/` for 30 days. “Back up now” updates today's snapshot; “Export data” downloads readable JSON (no API keys/passwords).

To restore a snapshot: stop the service, copy the entire AuraDesk data folder somewhere safe, then replace `dashboard.sqlite` with your chosen backup and remove the old `dashboard.sqlite-wal` / `dashboard.sqlite-shm` files if present. Restart. Do not restore while the service is running. Settings are separate in `settings.json`; back up that file privately if needed.

Privacy: bound to loopback only. Other devices cannot connect. Local data is readable by your macOS account and administrators; this isn't encrypted protection from someone using your unlocked laptop. Requests are protected against cross-site writes and DNS rebinding.

## Sources

- https://github.com/SimplifyJobs/New-Grad-Positions
- https://github.com/vanshb03/New-Grad-2027
- https://ai.google.dev/gemini-api/docs/structured-output

Sources refresh every six hours while the service is running, or when requested. Applied/archived choices are retained. Parser errors are visible and preserve saved roles. Community lists can change format; source success is not employer-level verification.

## Optional diagnostics

```bash
python3 test_dashboard.py
node test_ui.js  # optional UI logic checks; Node required only for these tests
```

Uses temporary databases and a fake SMTP sender; never emails anyone or touches your personal data.

Validation on 2026-10-07: 36 backend tests passed; one HTTP test was skipped because the execution sandbox disallows local socket binding. JavaScript render/planner checks passed. Browser walkthrough was attempted but computer-use permissions were denied, so actual layout and new-tab interaction remain unverified here. The UI harness checks markup, links, and interaction logic, not visual rendering. Tests use temporary data; the personal database was not modified during development. Restart the existing local service to load the updated code and run its compatible upgrade.

UI follow-up: Today keeps upcoming deadlines visible in a compact right column, mutes completed habits with a checkmark, and combines practice/application history beneath the habit buttons. New log timestamps persist in SQLite meta without changing historical activity dates; older events without timestamps use their recorded dates.

History groups all logged practice, applications, completed tasks, and finished next actions by day in compact rows. Day groups start collapsed. Future whole-task completions remain logged after reopening; submission corrections update the existing completion event. Under Upcoming deadlines, Slotted for today lists every active daily priority and work block for today, including undated items.

Capture now suggests genuinely tiny starter actions through the shared `capture_prompt.py`: e.g. open the spec and read one word, or open a video and watch 10 seconds. Review the short ordered sequence before saving. Offline parsing retains explicit lists without AI decomposition.

ChatGPT/Codex capture explicitly uses GPT-6 Luna with low reasoning (the lowest supported local catalog level); it does not use the larger CLI default model or automatically escalate. Actual availability is checked through successful inference.

## GitHub privacy

Only explicitly allowlisted source, tests, and this README are tracked. New files are ignored by default; review them for private information before adding them to `.gitignore`'s allowlist. Do not use `git add -f` for local data.

Tasks, history, email settings, API keys, passwords, and backups live in `~/Library/Application Support/AuraDesk` (or your `AURADESK_DATA` directory) and must stay private. Codex manages ChatGPT authentication outside this repository. Never copy account files, database exports, screenshots of personal tasks, or credentials into tracked source. Test task examples and credentials are synthetic fixtures.

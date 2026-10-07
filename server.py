#!/usr/bin/env python3
"""Aura Desk: a single-user, loopback-only dashboard. Python 3.10+, no dependencies."""
import argparse
import codex_capture
from capture_prompt import task_prompt
import shutil
import subprocess
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
import urllib.request
import urllib.parse
import webbrowser
from email.message import EmailMessage
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('AURADESK_DATA', str(Path.home() / 'Library/Application Support/AuraDesk')))
TZ = ZoneInfo('America/Detroit')
LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(32)
PORT = 8765
KINDS = ['OA', 'Interview', 'Virtual assessment', 'Homework', 'Project', 'Reading', 'Quiz', 'Exam', 'Other']
DEFAULTS = {'email_enabled': False, 'school_email': '', 'smtp_host': '', 'smtp_port': 465,
            'smtp_user': '', 'smtp_password': '', 'smtp_mode': 'SSL', 'gemini_key': '',
            'gemini_model': 'gemini-3.8-flash', 'lc_points': 10, 'design_points': 15,
            'oa_points': 30, 'early_bonus': 10, 'miss_penalty': 30, 'sources_enabled': True,
            'capture_provider': 'codex', 'last_refresh': '', 'source_status': '', 'mail_status': 'Email not configured'}
SOURCES = [('Simplify / Coder Quad', 'https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md'),
           ('Vansh / New Grad 2027', 'https://raw.githubusercontent.com/vanshb03/New-Grad-2027/main/README.md')]

def now():
    return dt.datetime.now(TZ)

class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def connect():
    c = sqlite3.connect(DATA / 'dashboard.sqlite', timeout=30, factory=Connection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init():
    DATA.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    db = DATA / 'dashboard.sqlite'
    if db.exists():
        with connect() as c:
            migrated = c.execute("SELECT name FROM sqlite_master WHERE name='applications'").fetchone()
            if not migrated:
                folder = DATA / 'backups'; folder.mkdir(exist_ok=True)
                with closing(sqlite3.connect(folder / ('pre-productivity-' + now().strftime('%Y%m%d-%H%M%S') + '.sqlite'))) as dst:
                    c.backup(dst)
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY, label TEXT, points INTEGER, day TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS practice(id TEXT PRIMARY KEY, kind TEXT, day TEXT, minutes INTEGER, notes TEXT);
        CREATE TABLE IF NOT EXISTS rewards(id TEXT PRIMARY KEY, title TEXT, threshold INTEGER, unlocked INTEGER DEFAULT 0, claimed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY, body TEXT, status TEXT DEFAULT 'pending', created TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS course_links(id TEXT PRIMARY KEY, label TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, day TEXT NOT NULL, job_id TEXT, company TEXT, title TEXT, url TEXT, notes TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS application_job ON applications(job_id) WHERE job_id != '';
        ''')
        c.execute("INSERT OR IGNORE INTO meta VALUES('habit_activation',?)", (now().date().isoformat(),))
        if not c.execute("SELECT 1 FROM meta WHERE key='design_streak_activation'").fetchone():
            c.execute("INSERT INTO meta VALUES('design_streak_activation',?)", (now().date().isoformat(),))
            c.execute("INSERT OR IGNORE INTO meta SELECT 'design-frozen:' || id, CAST(points AS TEXT) FROM ledger WHERE id LIKE 'practice:System design:%'")
    if not (DATA / 'settings.json').exists():
        save_settings(DEFAULTS)
    backup()

def settings():
    return DEFAULTS | json.loads((DATA / 'settings.json').read_text())

def save_settings(s):
    p = DATA / 'settings.json'
    tmp = DATA / 'settings.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(s, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(p)
    os.chmod(p, 0o600)

def backup(force=False):
    folder = DATA / 'backups'
    folder.mkdir(exist_ok=True)
    p = folder / ('dashboard-' + now().strftime('%Y-%m-%d') + '.sqlite')
    if force or not p.exists():
        with connect() as src, closing(sqlite3.connect(p)) as dst:
            src.backup(dst)
    # Keep daily snapshots for 30 days; never remove the active database.
    for old in sorted(folder.glob('dashboard-*.sqlite'))[:-30]:
        old.unlink()

def objects(c, table):
    return [json.loads(r['body']) for r in c.execute('SELECT body FROM ' + table)]

def put(c, table, obj):
    c.execute('INSERT OR REPLACE INTO ' + table + '(id,body) VALUES (?,?)', (obj['id'], json.dumps(obj)))

def parse_due(item):
    if not item.get('due') or not item.get('due_time'):
        return None
    return dt.datetime.fromisoformat(item['due'] + 'T' + item['due_time']).replace(tzinfo=TZ)

def ledger(c, key, label, points):
    c.execute('INSERT OR IGNORE INTO ledger VALUES(?,?,?,?,?)', (key, label, points, now().date().isoformat(), now().isoformat()))

def reconcile_points(c, item):
    s = settings()
    key = 'item:' + item['id']
    c.execute('DELETE FROM ledger WHERE id=?', (key,))
    deadline = parse_due(item)
    if item['kind'] != 'OA':
        return
    if item['status'] == 'missed':
        ledger(c, key, 'Missed OA: ' + item['title'], -s['miss_penalty'])
    elif item['status'] == 'done':
        submitted = item.get('submitted_at')
        stamp = dt.datetime.fromisoformat(submitted) if submitted else None
        if deadline and stamp and stamp > deadline:
            ledger(c, key, 'Late OA: ' + item['title'], 0)
        else:
            points = s['oa_points']
            if deadline and stamp and (deadline - stamp).total_seconds() >= 48 * 3600:
                points += s['early_bonus']
            ledger(c, key, 'Submitted OA: ' + item['title'], points)

def unlock(c):
    points = c.execute('SELECT COALESCE(SUM(points),0) FROM ledger').fetchone()[0]
    c.execute('UPDATE rewards SET unlocked=1 WHERE threshold<=?', (points,))

def notice(c, key, subject, text):
    if settings()['email_enabled']:
        c.execute("INSERT INTO notices VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='pending',body=excluded.body WHERE notices.status='cancelled'",
                  (key, json.dumps({'subject': subject, 'text': text}), 'pending', now().isoformat()))

def deadlines():
    with LOCK, connect() as c:
        for item in objects(c, 'items'):
            if item['status'] != 'active':
                continue
            deadline = parse_due(item)
            if not deadline:
                continue
            remaining = (deadline - now()).total_seconds()
            recruiting = item['kind'] in ['OA', 'Interview', 'Virtual assessment']
            suffix = '\n' + item['url'] if item.get('url') else ''
            generation = item['id'] + ':' + item['due'] + ':' + item['due_time']
            if remaining <= 0 and item['kind'] == 'OA':
                item['status'] = 'missed'
                item['revision'] = item.get('revision', 0) + 1
                put(c, 'items', item)
                reconcile_points(c, item)
                c.execute("UPDATE notices SET status='cancelled' WHERE id=? AND status='pending'", ('urgent:' + generation,))
                notice(c, 'missed:' + generation, 'Missed OA: ' + item['title'],
                       item['title'] + ' deadline passed. −' + str(settings()['miss_penalty']) + ' aura.\nAlready submitted? Correct it in Aura Desk.' + suffix)
            elif recruiting and 0 < remaining <= 48 * 3600:
                minutes = str(item.get('minutes', 0)) + ' min · ' if item.get('minutes') else ''
                notice(c, 'urgent:' + generation, 'Due soon: ' + item['title'],
                       item['title'] + ' · ' + minutes + 'due ' + deadline.strftime('%b %d, %I:%M %p %Z') + suffix)
        unlock(c)

def mail_once():
    s = settings()
    if not s['email_enabled']:
        return
    if not all(s[k] for k in ['school_email', 'smtp_host', 'smtp_user', 'smtp_password']):
        return
    with LOCK, connect() as c:
        rows = c.execute("SELECT * FROM notices WHERE status='pending' ORDER BY created LIMIT 20").fetchall()
    if not rows:
        return
    messages = [json.loads(r['body']) for r in rows]
    msg = EmailMessage()
    msg['From'] = s['smtp_user']
    msg['To'] = s['school_email']
    msg['Subject'] = messages[0]['subject'] if len(messages) == 1 else 'Aura Desk: ' + str(len(messages)) + ' deadlines need attention'
    msg.set_content('\n\n'.join(m['text'] for m in messages))
    try:
        if s['smtp_mode'] == 'SSL':
            client = smtplib.SMTP_SSL(s['smtp_host'], int(s['smtp_port']), timeout=20, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(s['smtp_host'], int(s['smtp_port']), timeout=20)
            client.starttls(context=ssl.create_default_context())
        with client:
            client.login(s['smtp_user'], s['smtp_password'])
            client.send_message(msg)
        with LOCK, connect() as c:
            c.executemany("UPDATE notices SET status='sent' WHERE id=?", [(r['id'],) for r in rows])
            s = settings()
            s['mail_status'] = 'Last email sent ' + now().strftime('%b %d %I:%M %p')
            save_settings(s)
    except Exception:
        with LOCK:
            s = settings()
            s['mail_status'] = 'Email failed. Check SMTP settings; pending messages will retry.'
            save_settings(s)

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'AuraDesk/1.0'})
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read(8_000_000).decode('utf-8')

class JobTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cells, self.links, self.text = [], [], [], []
        self.cell = False

    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.cells = []
        if tag == 'td': self.cell, self.text, self.links = True, [], []
        if self.cell and tag == 'a':
            self.links.append(dict(attrs).get('href', ''))

    def handle_data(self, data):
        if self.cell: self.text.append(data)

    def handle_endtag(self, tag):
        if tag == 'td':
            self.cells.append((' '.join(''.join(self.text).split()), self.links))
            self.cell = False
        if tag == 'tr' and len(self.cells) >= 4: self.rows.append(self.cells)

def parse_jobs(content, source):
    parser = JobTable()
    parser.feed(content)
    rows = parser.rows
    if not rows:
        for line in content.splitlines():
            if line.startswith('|'):
                cells = line.strip('|').split('|')
                if len(cells) >= 4:
                    rows.append([(re.sub(r'<[^>]+>|\[([^]]+)\]\([^)]+\)', lambda m: m.group(1) or '', cell).strip(),
                                  re.findall(r'(?:href=["\']|\]\()(https?://[^"\')\s]+)', cell)) for cell in cells])
    result, company = [], ''
    for row in rows:
        if row[0][0] not in ['↳', '']:
            company = row[0][0].strip('* ')
        title, location = row[1][0], row[2][0]
        links = row[3][1]
        if not company or not links or '🔒' in row[3][0]: continue
        url = next((x for x in links if x.startswith('https://') and 'simplify.jobs' not in x), links[0])
        if not url.startswith('https://'): continue
        if not re.search(r'engineer|software|developer|quant|machine learning|data scien|AI', title, re.I): continue
        if '🎓' in title or re.search(r'\b(phd|master[’\']?s)\b', title, re.I): continue
        parsed = urllib.parse.urlsplit(html.unescape(url))
        query = urllib.parse.parse_qsl(parsed.query)
        url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode([(k,v) for k,v in query if not k.startswith('utm_') and k != 'ref'])))
        result.append({'id': hashlib.sha256(url.encode()).hexdigest()[:24], 'company': company,
                       'title': title, 'location': location, 'url': url, 'source': source,
                       'status': 'new', 'first_seen': now().isoformat(), 'last_seen': now().isoformat(), 'source_present': True})
    return result

def refresh_jobs():
    reports = []
    for name, url in SOURCES:
        try:
            found = parse_jobs(fetch(url), name)
            if not found: raise ValueError('No recognized roles; source format may have changed')
            with LOCK, connect() as c:
                existing = {x['id']: x for x in objects(c, 'jobs')}
                for old in existing.values():
                    if old['source'] == name:
                        old['source_present'] = False
                        put(c, 'jobs', old)
                count = 0
                for job in found:
                    if job['id'] in existing:
                        old = existing[job['id']]
                        job['status'], job['first_seen'] = old['status'], old['first_seen']
                    else: count += 1
                    put(c, 'jobs', job)
            reports.append(name + ': ' + str(count) + ' new, ' + str(len(found)) + ' listed')
        except Exception:
            reports.append(name + ': could not refresh; saved roles preserved')
    with LOCK:
        s = settings()
        s['last_refresh'], s['source_status'] = now().isoformat(), ' · '.join(reports)
        save_settings(s)
    return reports

def validate_item(raw):
    item = {**raw, **{k: raw.get(k, '') for k in ['title', 'kind', 'group', 'due', 'due_time', 'start_by', 'next_action', 'url', 'notes', 'submitted_at', 'block_date', 'block_time']}}
    item['id'] = raw.get('id') or secrets.token_hex(12)
    if not isinstance(item['title'], str) or not item['title'].strip(): raise ValueError('Add a title')
    item['title'] = item['title'].strip()[:300]
    if item['kind'] not in KINDS: raise ValueError('Choose a valid category')
    item['status'] = raw.get('status', 'active')
    if item['status'] not in ['active', 'done', 'missed', 'archived']: raise ValueError('Invalid status')
    item['previous_status'] = raw.get('previous_status', 'active')
    item['resources'] = raw.get('resources', [])
    item['minutes'] = max(0, min(100000, int(raw.get('minutes') or 0)))
    item['priority_day'] = raw.get('priority_day', '')
    for k in ['due', 'start_by', 'block_date']:
        if item[k]: dt.date.fromisoformat(item[k])
    for k in ['due_time', 'block_time']:
        if item[k]: dt.time.fromisoformat(item[k])
    if item['submitted_at']:
        stamp = dt.datetime.fromisoformat(item['submitted_at'])
        if stamp.tzinfo is None: raise ValueError('Submission timestamp needs a timezone')
    if item['url']: validate_url(item['url'])
    if not isinstance(item['resources'], list): raise ValueError('Resources must be a list')
    for resource in item['resources']:
        if not isinstance(resource, dict): raise ValueError('Invalid resource')
        validate_url(resource.get('url', ''))
    for key in ['title', 'group', 'next_action', 'notes']:
        if not isinstance(item[key], str): raise ValueError('Text fields must be text')
    item['action_minutes'] = max(0, min(100000, int(raw.get('action_minutes') or 0)))
    steps = raw.get('plan_steps', [])
    if not isinstance(steps, list) or len(steps) > 200: raise ValueError('Invalid project plan')
    ids = set()
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('text'), str) or not step['text'].strip(): raise ValueError('Each step needs text')
        step['id'] = step.get('id') or secrets.token_hex(12)
        if not isinstance(step['id'], str) or step['id'] in ids: raise ValueError('Steps need unique IDs')
        ids.add(step['id'])
        step['text'] = step['text'].strip()[:1000]
        step['minutes'] = max(0, min(100000, int(step.get('minutes') or 0)))
        if step.get('completed_at'): dt.datetime.fromisoformat(step['completed_at'])
    item['plan_steps'] = steps
    if any(not step.get('completed_at') for step in steps):
        item['next_action'] = ''; item['next_action_id'] = ''; item['action_minutes'] = 0
    item['plan_entry_mode'] = raw.get('plan_entry_mode', 'forward')
    if item['plan_entry_mode'] not in ['forward', 'backward']: raise ValueError('Invalid planning mode')
    return item

def event_key(item):
    return item['id'] + ':' + item.get('due', '') + ':' + item.get('due_time', '')

def current_action(item):
    step = next((x for x in item.get('plan_steps', []) if not x.get('completed_at')), None)
    if step: return {'id':step['id'], 'text':step['text'], 'minutes':step.get('minutes', 0)}
    if item.get('next_action'):
        return {'id':item.get('next_action_id') or 'legacy:' + item['id'], 'text':item['next_action'], 'minutes':item.get('action_minutes', 0)}
    return None

def classify(item):
    today = now().date(); due = parse_due(item)
    hours = (due - now()).total_seconds() / 3600 if due else None
    date = dt.date.fromisoformat(item['due']) if item.get('due') else None
    reached = bool(item.get('start_by') and item['start_by'] <= today.isoformat())
    effort = bool(date and date <= today + dt.timedelta(days=7) and item.get('minutes', 0) >= 180)
    attention = bool((hours is not None and hours <= 48) or (date and not due and date <= today + dt.timedelta(days=1)))
    if attention:
        reason = ('Overdue' if hours <= 0 else ('OA expires' if item['kind']=='OA' else 'Due') + ' in ' + str(round(hours)) + ' hours') if due else 'Due ' + item['due'] + ' · time unspecified'
        tier = 0
    elif reached or effort:
        tier = 1; reason = 'Start-by date reached' if reached else str(item['minutes']) + ' min estimated · due this week'
    elif item['kind']=='OA' or (date and date <= today + dt.timedelta(days=7)):
        tier = 2; reason = 'Attempt ASAP' if item['kind']=='OA' else 'Due this week'
    else:
        tier = 3; reason = 'Inspect the spec and estimate the work.' if item['kind']=='Project' and not item.get('minutes') and not current_action(item) and not item.get('action_history') else 'Choose a next action'
    return {'attention':attention, 'tier':tier, 'reason':reason, 'key':(tier, item.get('due') or '9999', item.get('due_time') or '', item.get('start_by') or '9999', -item.get('minutes',0), item['id'])}

def suggestions(items):
    candidates = [i for i in items if i['status']=='active' and i.get('priority_day') != now().date().isoformat() and not classify(i)['attention']]
    candidates.sort(key=lambda i:classify(i)['key'])
    result = candidates[:5]; reserved = []
    for predicate in [lambda i:i['kind']=='OA', lambda i:i['kind'] not in ['OA','Interview','Virtual assessment'] and classify(i)['tier']==1]:
        first = next((i for i in candidates if predicate(i)), None)
        if first:
            reserved.append(first['id'])
            if first not in result:
                replace = next((i for i in reversed(result) if i['id'] not in reserved), None)
                if replace: result.remove(replace)
                result.append(first)
    return [i['id'] for i in sorted(result, key=lambda i:classify(i)['key'])]

def habit_days(c, kind):
    if kind=='Applications': return {r[0] for r in c.execute('SELECT DISTINCT day FROM applications')}
    return {r[0] for r in c.execute('SELECT DISTINCT day FROM practice WHERE kind=?',(kind,))}

def run_before(days, day):
    n = 0; day -= dt.timedelta(days=1)
    while day.isoformat() in days:
        n += 1; day -= dt.timedelta(days=1)
    return n

def design_run_before(days, day):
    count = 0
    for previous in sorted((dt.date.fromisoformat(d) for d in days if d < day.isoformat()), reverse=True):
        if (day - previous).days > 3: break
        count += 1; day = previous
    return count

def habits(c):
    result = {}
    for kind in ['LeetCode', 'Applications']:
        days = habit_days(c, kind); today = now().date(); done = today.isoformat() in days
        end = today if done else today - dt.timedelta(days=1)
        streak = 1 + run_before(days, end) if end.isoformat() in days else 0
        result[kind] = {'done':done, 'streak':streak, 'points':min(20, 10 + 2 * run_before(days, today))}
    days = habit_days(c,'System design'); today = now().date(); done = today.isoformat() in days
    prior = design_run_before(days,today)
    result['System design'] = {'done':done,'streak':prior + int(done),'points':min(25,15 + 2 * prior)}
    return result

def reconcile_habits(c):
    activation = c.execute("SELECT value FROM meta WHERE key='habit_activation'").fetchone()[0]
    for kind, prefix in [('LeetCode','practice:LeetCode:'), ('Applications','application:'), ('System design','practice:System design:')]:
        policy_date = c.execute("SELECT value FROM meta WHERE key='design_streak_activation'").fetchone()[0] if kind=='System design' else activation
        frozen = {r[0].removeprefix('design-frozen:'):int(r[1]) for r in c.execute("SELECT key,value FROM meta WHERE key LIKE 'design-frozen:%'")} if kind=='System design' else {}
        days = habit_days(c, kind)
        for row in c.execute('SELECT id,day FROM ledger WHERE id LIKE ?', (prefix+'%',)).fetchall():
            if row['day'] >= policy_date and row['day'] not in days: c.execute('DELETE FROM ledger WHERE id=?', (row['id'],))
        for day in sorted(days):
            if prefix+day in frozen: points = frozen[prefix+day]
            elif day < policy_date: continue
            else: points = min(25,15 + 2 * design_run_before(days,dt.date.fromisoformat(day))) if kind=='System design' else min(20, 10 + 2 * run_before(days, dt.date.fromisoformat(day)))
            c.execute('INSERT INTO ledger VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET points=excluded.points', (prefix+day, kind+' effort · '+day, points, day, now().isoformat()))

def log_application(c, body):
    day = body.get('day') or now().date().isoformat()
    if dt.date.fromisoformat(day) > now().date(): raise ValueError('Applications cannot be in the future')
    url = body.get('url', '')
    if url: validate_url(url)
    id = body.get('id') or secrets.token_hex(12)
    c.execute('INSERT INTO applications VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET day=excluded.day,company=excluded.company,title=excluded.title,url=excluded.url,notes=excluded.notes',
              (id, day, body.get('job_id',''), str(body.get('company',''))[:300], str(body.get('title',''))[:300], url, str(body.get('notes',''))[:2000]))
    c.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('log-created:applications:'+id,now().isoformat()))
    reconcile_habits(c)

def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password: raise ValueError('Use an http:// or https:// URL with a host')

def draft(text):
    if not isinstance(text,str) or len(text)>20000: raise ValueError('Capture text must be at most 20000 characters')
    s = settings()
    if s.get('capture_provider')=='codex':
        return codex_capture.extract(text,now().isoformat(),KINDS), 'ChatGPT / Codex draft — review before saving'
    if s.get('capture_provider')=='gemini' and s['gemini_key']:
        schema = {'type': 'object', 'properties': {k: {'type':'string'} for k in ['title','kind','group','due','due_time','next_action','url','notes']}}
        schema['properties']['kind']['enum'] = KINDS
        schema['properties']['minutes'] = {'type':'integer'}
        schema['properties']['next_actions'] = {'type':'array','items':{'type':'string'},'maxItems':200}
        schema['required'] = ['title','kind']
        prompt = task_prompt(text,now().isoformat())
        payload = {'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'responseMimeType':'application/json','responseJsonSchema':schema}}
        endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + urllib.parse.quote(s['gemini_model'], safe='-._') + ':generateContent'
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','x-goog-api-key':s['gemini_key']})
        with urllib.request.urlopen(req, timeout=45) as response:
            data = json.load(response)
        return json.loads(''.join(p.get('text','') for p in data['candidates'][0]['content']['parts'])), 'Gemini draft — review before saving'
    return offline_draft(text)

def offline_draft(text):
    # Offline quick capture. Keep ambiguous fields blank for review.
    if not isinstance(text,str) or len(text)>20000: raise ValueError('Capture text must be at most 20000 characters')
    link = re.search(r'https?://\S+', text)
    date = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', text)
    slash = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b', text)
    due = date.group(1) if date else ''
    if slash and not due: due = f'{int(slash.group(3) or now().year):04d}-{int(slash.group(1)):02d}-{int(slash.group(2)):02d}'
    if re.search(r'\btomorrow\b', text, re.I): due = (now().date()+dt.timedelta(days=1)).isoformat()
    elif re.search(r'\btoday\b', text, re.I): due = now().date().isoformat()
    for idx, day in enumerate(['monday','tuesday','wednesday','thursday','friday','saturday','sunday']):
        if not due and re.search(r'\b' + day + r'\b', text, re.I):
            due = (now().date()+dt.timedelta(days=(idx-now().weekday())%7)).isoformat()
    clock = re.search(r'\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', text, re.I)
    due_time = ''
    if clock:
        hour = int(clock.group(1))%12 + (12 if clock.group(3).lower() == 'pm' else 0)
        due_time = f'{hour:02d}:{int(clock.group(2) or 0):02d}'
    mins = re.search(r'(\d+)\s*(?:min|minutes)\b',text,re.I)
    kind = 'Other'
    aliases = [('OA',r'\boa\b|hackerrank|codesignal'), ('Interview',r'interview'), ('Virtual assessment',r'virtual assessment'),
               ('Homework',r'\bhw\b|homework|assignment'),('Project',r'project'),('Reading',r'reading'),('Quiz',r'quiz'),('Exam',r'exam|midterm|final')]
    for k, pattern in aliases:
        if re.search(pattern,text,re.I): kind=k; break
    title = re.sub(r'^(?:yo\s+)?(?:add|create)(?:\s+this)?\s+', '', text.strip(), flags=re.I)
    title = re.split(r'\bdue\b|https?://',title,flags=re.I)[0].strip(' .,')
    action_section = re.search(r'\b(?:next actions|actions|steps)\s*:\s*(.*)',text,re.I|re.S)
    actions = []
    if action_section:
        entries = action_section.group(1).splitlines()
        if len(entries)==1: entries = re.split(r'\s*(?:→|->|;)\s*|,\s+',entries[0])
        actions = [re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*','',line).strip() for line in entries if line.strip()]
        title = re.split(r'\b(?:next actions|actions|steps)\s*:',title,flags=re.I)[0].split('\n')[0].strip(' .')
    return {'next_actions':actions, 'title':title or text[:80], 'kind':kind, 'due':due, 'due_time':due_time, 'minutes':int(mins.group(1)) if mins else 0, 'url':link.group(0).rstrip('.,)') if link else '', 'notes':text}, 'Local capture — review dates and fill missing fields. No external provider was used.'

def snapshot():
    deadlines()
    with LOCK, connect() as c:
        s = settings()
        s['gemini_configured'], s['smtp_configured'] = bool(s.pop('gemini_key')), bool(s.pop('smtp_password'))
        practice = [dict(r) for r in c.execute('SELECT * FROM practice ORDER BY day DESC,rowid DESC')]
        days = set(r['day'] for r in practice if r['kind']=='LeetCode')
        day, streak = now().date(), 0
        if day.isoformat() not in days: day -= dt.timedelta(days=1)
        while day.isoformat() in days: streak += 1; day -= dt.timedelta(days=1)
        applications = [dict(r) for r in c.execute('SELECT * FROM applications ORDER BY day DESC,rowid DESC')]
        created = dict(c.execute("SELECT key,value FROM meta WHERE key LIKE 'log-created:%'").fetchall())
        for table, logs in [('practice',practice),('applications',applications)]:
            for log in logs: log['created'] = created.get('log-created:'+table+':'+log['id'], '')
        items = objects(c,'items')
        for item in items:
            item['current_action'] = current_action(item)
            item['guidance'] = {k:v for k,v in classify(item).items() if k != 'key'}
            item['miss_acknowledged'] = event_key(item) in item.get('miss_acknowledgments', {})
        return {'applications':applications,
                'course_links':[dict(r) for r in c.execute('SELECT * FROM course_links ORDER BY position,id')],
                'habits':habits(c), 'suggestions':suggestions(items), 'items':items, 'jobs':objects(c,'jobs'), 'practice':practice,
                'ledger':[dict(r) for r in c.execute('SELECT * FROM ledger ORDER BY created DESC')],
                'rewards':[dict(r) for r in c.execute('SELECT * FROM rewards ORDER BY threshold')],
                'settings':s, 'today':now().date().isoformat(), 'now':now().isoformat(), 'streak':streak,
                'data_path':str(DATA), 'points':c.execute('SELECT COALESCE(SUM(points),0) FROM ledger').fetchone()[0]}

def action(path, body):
    if path == '/api/draft':
        try:
            result, message = draft(body['text'])
        except Exception as error:
            result, message = offline_draft(body['text'])
            message = (str(error)+' ' if isinstance(error,codex_capture.CaptureError) else 'AI capture failed. ')+'Offline draft — review all fields.'
        actions = result.pop('next_actions', [])
        if actions:
            if not isinstance(actions,list) or len(actions)>200 or any(not isinstance(t,str) or not t.strip() or len(t)>1000 for t in actions): raise ValueError('Invalid drafted actions')
            result['plan_steps'] = [{'id':secrets.token_hex(12),'text':t.strip(),'minutes':0,'completed_at':''} for t in actions]
            result['next_action'] = ''
            result['plan_entry_mode'] = 'forward'
        return {'draft':result, 'message':message}
    if path == '/api/capture-status':
        return {'status':codex_capture.status()}
    if path == '/api/capture-test':
        try:
            codex_capture.extract('Read chapter 2 for History; deadline unknown.',now().isoformat(),KINDS)
            return {'status':codex_capture.status()}
        except codex_capture.CaptureError as error:
            return {'status':str(error)+' Offline capture remains available.'}
    if path == '/api/refresh': return {'reports':refresh_jobs()}
    with LOCK, connect() as c:
        if path == '/api/item':
            old = next((i for i in objects(c,'items') if i['id']==body.get('id')), None)
            if old and 'revision' in body and int(body['revision']) != old.get('revision',0): raise ValueError('This task changed. Reopen it before saving.')
            raw = {**(old or {}), **{k:v for k,v in body.items() if k not in ['current_action','guidance','miss_acknowledged','action_history','miss_acknowledgments','completion_events']}}
            if body.get('submitted_date') and body.get('submitted_time'):
                stamp = dt.datetime.fromisoformat(body['submitted_date']+'T'+body['submitted_time']).replace(tzinfo=TZ)
                if stamp.astimezone(dt.timezone.utc).astimezone(TZ).replace(tzinfo=None) != stamp.replace(tzinfo=None): raise ValueError('That time does not exist in Detroit because of daylight saving. Enter the actual time.')
                raw['submitted_at'] = stamp.isoformat()
            raw.pop('submitted_date',None); raw.pop('submitted_time',None)
            item = validate_item(raw)
            if old and old['status']=='missed' and item['status']=='missed' and event_key(old)!=event_key(item): item['status']='active'
            if old:
                new_ids = {x['id'] for x in item['plan_steps']}
                removed = item.setdefault('removed_plan_steps',[])
                for step in old.get('plan_steps',[]):
                    if step['id'] not in new_ids and not any(x['id']==step['id'] for x in removed): removed.append(step)
                item['removed_plan_steps'] = [x for x in removed if x['id'] not in new_ids]
                reopened = {x['id'] for x in item['plan_steps'] if not x.get('completed_at')}
                for h in item.get('action_history',[]):
                    if h['id'] in reopened: h['undone']=True
            if old and old.get('next_action') != item.get('next_action'): item['next_action_id'] = secrets.token_hex(12)
            elif item.get('next_action') and not item.get('next_action_id'): item['next_action_id'] = 'legacy:' + item['id']
            if item.get('priority_day') and not current_action(item) and item['kind']=='Project' and not item.get('minutes') and not item.get('plan_steps') and not item.get('action_history'):
                item['next_action'] = 'Inspect the spec and estimate the work.'; item['next_action_id'] = secrets.token_hex(12)
            if old and old.get('priority_day') and old.get('priority_day')==item.get('priority_day') and (current_action(old) or {}).get('id','') != (current_action(item) or {}).get('id',''): item['priority_day']=''
            if item.get('priority_day'): item['priority_action_id'] = (current_action(item) or {}).get('id', '')
            item['revision'] = (old or {}).get('revision',0) + 1
            if item.get('priority_day') and item['status']=='active':
                count = sum(i.get('priority_day')==item['priority_day'] and i['id']!=item['id'] and i['status']=='active' for i in objects(c,'items'))
                if count >= 3: raise ValueError('Today already has three priorities. Remove one first.')
            if item['status']=='done':
                if not item['submitted_at']: item['submitted_at']=now().isoformat()
                events=item.setdefault('completion_events',[])
                was_done=old and (old['status']=='done' or old['status']=='archived' and old.get('previous_status')=='done')
                entry={'id':events[-1]['id'] if was_done and events else secrets.token_hex(12),'completed_at':item['submitted_at'],'title':item['title'],'kind':item['kind']}
                if was_done and events: events[-1]=entry
                else: events.append(entry)
            if old and item['status']=='archived': item['previous_status']=old['status']
            put(c,'items',item)
            # Archiving is organization, not a way to earn/erase points.
            if item['status']!='archived': reconcile_points(c,item)
            if item['status'] in ['done','archived'] or (old and event_key(old) != event_key(item)):
                c.execute("UPDATE notices SET status='cancelled' WHERE id LIKE ? AND status='pending'", ('%'+item['id']+':%',))
        elif path in ['/api/action-complete', '/api/action-undo', '/api/miss-acknowledge']:
            item = next((i for i in objects(c,'items') if i['id']==body.get('id')), None)
            if not item: raise ValueError('Task not found')
            history = item.setdefault('action_history', [])
            if path == '/api/miss-acknowledge':
                if item['status'] != 'missed': raise ValueError('This OA is not missed')
                item.setdefault('miss_acknowledgments', {})[event_key(item)] = now().isoformat()
            elif path == '/api/action-complete':
                aid = body.get('action_id')
                if any(h['id']==aid and not h.get('undone') for h in history): return {'ok':True}
                if 'revision' in body and body['revision'] != item.get('revision',0): raise ValueError('This action changed. Reload before completing it.')
                action_now = current_action(item)
                if item['status'] != 'active' or not action_now or action_now['id'] != aid: raise ValueError('This action changed. Reload before completing it.')
                history.append({**action_now, 'completed_at':now().isoformat(), 'priority_day':item.get('priority_day','')})
                step = next((x for x in item.get('plan_steps',[]) if x['id']==aid), None)
                if step: step['completed_at'] = now().isoformat()
                else: item['next_action'] = ''; item['next_action_id'] = ''; item['action_minutes'] = 0
                item['priority_day'] = ''; item['priority_action_id'] = ''
            else:
                h = next((h for h in reversed(history) if h['id']==body.get('action_id') and not h.get('undone')), None)
                if not h: return {'ok':True}
                step = next((x for x in item.get('plan_steps',[]) if x['id']==h['id']), None)
                if step: step['completed_at'] = ''
                else:
                    if current_action(item): raise ValueError('Clear the new action before undoing the previous one')
                    item.update(next_action=h['text'], next_action_id=h['id'], action_minutes=h.get('minutes',0))
                h['undone'] = True
                day = h.get('priority_day','')
                count = sum(i['id']!=item['id'] and i['status']=='active' and i.get('priority_day')==day for i in objects(c,'items'))
                if day == now().date().isoformat() and count < 3: item['priority_day'] = day
            item['revision'] = item.get('revision',0) + 1
            put(c,'items',item)
        elif path == '/api/course-link':
            label = str(body.get('label','')).strip()[:80]; url = body.get('url',''); validate_url(url)
            if not label: raise ValueError('Add a label')
            id = body.get('id') or secrets.token_hex(12)
            c.execute('INSERT INTO course_links VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,url=excluded.url', (id,label,url,c.execute('SELECT COUNT(*) FROM course_links').fetchone()[0]))
        elif path == '/api/course-link-remove':
            c.execute('DELETE FROM course_links WHERE id=?', (body['id'],))
        elif path == '/api/application':
            log_application(c, body)
        elif path == '/api/application-delete':
            c.execute('DELETE FROM applications WHERE id=?', (body['id'],)); reconcile_habits(c)
        elif path == '/api/job':
            job=next((j for j in objects(c,'jobs') if j['id']==body['id']),None)
            if not job: raise ValueError('Role not found')
            if body['status'] not in ['new','applied','archived']: raise ValueError('Invalid role status')
            if body['status']=='applied' and job['status']!='applied' and not c.execute('SELECT 1 FROM applications WHERE job_id=?',(job['id'],)).fetchone():
                log_application(c, {**job, 'id':secrets.token_hex(12), 'job_id':job['id'], 'day':now().date().isoformat()})
            job['status']=body['status']; put(c,'jobs',job)
        elif path == '/api/manual-job':
            if not body.get('company') or not body.get('title') or not body.get('url','').startswith('https://'): raise ValueError('Add company, role, and an https URL')
            job={**body,'id':hashlib.sha256(body['url'].encode()).hexdigest()[:24], 'status':'new','source':'Your sources','first_seen':now().isoformat(),'last_seen':now().isoformat(),'source_present':True}
            existing=next((j for j in objects(c,'jobs') if j['id']==job['id']),None)
            if existing: raise ValueError('That role is already saved (check Archive too)')
            put(c,'jobs',job)
        elif path == '/api/practice':
            kind=body['kind']; minutes=int(body['minutes']); day=body.get('day',now().date().isoformat())
            if kind not in ['LeetCode','System design'] or not 1<=minutes<=1440: raise ValueError('Log 1–1440 minutes of effort')
            if dt.date.fromisoformat(day)>now().date(): raise ValueError('Practice cannot be logged in the future')
            id=secrets.token_hex(12)
            c.execute('INSERT INTO practice VALUES(?,?,?,?,?)',(id,kind,day,minutes,body.get('notes','')))
            c.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('log-created:practice:'+id,now().isoformat()))
            activation = c.execute("SELECT value FROM meta WHERE key='habit_activation'").fetchone()[0]
            if kind=='System design': activation = c.execute("SELECT value FROM meta WHERE key='design_streak_activation'").fetchone()[0]
            if day < activation:
                ledger(c,'practice:'+kind+':'+day,kind+' effort · '+day,settings()['lc_points' if kind=='LeetCode' else 'design_points'])
                c.execute('UPDATE ledger SET day=? WHERE id=?',(day,'practice:'+kind+':'+day))
            reconcile_habits(c)
        elif path == '/api/practice-edit':
            old = c.execute('SELECT * FROM practice WHERE id=?',(body['id'],)).fetchone()
            if not old: raise ValueError('Log not found')
            day = body.get('day',old['day']); minutes = int(body.get('minutes',old['minutes']))
            if dt.date.fromisoformat(day)>now().date() or not 1<=minutes<=1440: raise ValueError('Enter a past/current day and 1–1440 minutes')
            notes = body.get('notes',old['notes'])
            if not isinstance(notes,str): raise ValueError('Notes must be text')
            c.execute('UPDATE practice SET day=?,minutes=?,notes=? WHERE id=?',(day,minutes,notes[:2000],old['id']))
            if old['day']!=day and not c.execute('SELECT 1 FROM practice WHERE day=? AND kind=?',(old['day'],old['kind'])).fetchone():
                c.execute('DELETE FROM ledger WHERE id=?',('practice:'+old['kind']+':'+old['day'],))
            key = 'design_streak_activation' if old['kind']=='System design' else 'habit_activation'
            activation = c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()[0]
            if day<activation:
                ledger(c,'practice:'+old['kind']+':'+day,old['kind']+' effort · '+day,settings()['design_points' if old['kind']=='System design' else 'lc_points'])
                c.execute('UPDATE ledger SET day=? WHERE id=?',(day,'practice:'+old['kind']+':'+day))
            reconcile_habits(c)
        elif path == '/api/practice-delete':
            row=c.execute('SELECT * FROM practice WHERE id=?',(body['id'],)).fetchone()
            if row:
                c.execute('DELETE FROM practice WHERE id=?',(row['id'],))
                if not c.execute('SELECT 1 FROM practice WHERE kind=? AND day=?',(row['kind'],row['day'])).fetchone():
                    c.execute('DELETE FROM ledger WHERE id=?',('practice:'+row['kind']+':'+row['day'],))
            reconcile_habits(c)
        elif path == '/api/reward':
            threshold=int(body.get('threshold',0))
            if threshold<=0 or not body.get('title'): raise ValueError('Add a reward name and positive milestone')
            c.execute('INSERT INTO rewards(id,title,threshold) VALUES(?,?,?)',(secrets.token_hex(12),body['title'],threshold))
        elif path == '/api/claim':
            c.execute('UPDATE rewards SET claimed=1 WHERE id=? AND unlocked=1',(body['id'],))
        elif path == '/api/settings':
            s=settings()
            for key in DEFAULTS:
                if key in body and key not in ['last_refresh','source_status','mail_status']:
                    if key in ['gemini_key','smtp_password'] and not body[key]: continue
                    s[key]=body[key]
            if s.get('capture_provider') not in ['local','gemini','codex']: raise ValueError('Choose ChatGPT / Codex, Local or Gemini capture')
            if s['email_enabled'] and not all(s[k] for k in ['school_email','smtp_host','smtp_user','smtp_password']): raise ValueError('Email needs an address and complete SMTP settings')
            save_settings(s)
        elif path == '/api/backup':
            backup(force=True)
        else: raise ValueError('Unknown action')
        unlock(c)
    return {'ok':True}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def send(self,data,status=200,mime='application/json'):
        if isinstance(data,(dict,list)): data=json.dumps(data).encode()
        elif isinstance(data,str): data=data.encode()
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers(); self.wfile.write(data)

    def valid_host(self):
        return self.headers.get('Host') in [f'127.0.0.1:{PORT}',f'localhost:{PORT}']

    def do_GET(self):
        if not self.valid_host(): return self.send({'error':'Invalid host'},403)
        path=urllib.parse.urlsplit(self.path).path
        try:
            if path=='/api/state': return self.send(snapshot())
            if path=='/api/session': return self.send({'token':TOKEN})
            if path=='/api/export': return self.send(snapshot())
            files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css'),'/favicon.svg':('favicon.svg','image/svg+xml')}
            if path in files:
                file,mime=files[path]; return self.send((ROOT/file).read_bytes(),mime=mime)
            self.send({'error':'Not found'},404)
        except Exception: self.send({'error':'Could not load data. Your database has not been reset.'},500)

    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Aura-Token')!=TOKEN: return self.send({'error':'Reload the dashboard to reconnect'},403)
        origin=self.headers.get('Origin')
        if origin and origin not in [f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}']: return self.send({'error':'Invalid origin'},403)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n>1_000_000: raise ValueError('Input too large')
            body=json.loads(self.rfile.read(n))
            self.send(action(self.path,body))
        except (ValueError,KeyError,TypeError) as e: self.send({'error':str(e)},400)
        except Exception: self.send({'error':'Operation failed. Check settings or connection; existing data is preserved.'},500)

def worker():
    last_sources, last_mail = 0, 0
    while True:
        try:
            deadlines(); backup()
            if time.time()-last_mail>=900:
                mail_once(); last_mail=time.time()
            if settings()['sources_enabled'] and time.time()-last_sources>=6*3600:
                refresh_jobs(); last_sources=time.time()
        except Exception:
            pass
        time.sleep(30)

def autostart():
    import plistlib
    import subprocess
    import sys
    folder=Path.home()/'Library/LaunchAgents'; folder.mkdir(parents=True,exist_ok=True)
    path=folder/'local.auradesk.plist'
    spec={'Label':'local.auradesk','ProgramArguments':[sys.executable,str(ROOT/'server.py'),'--no-browser'],
          'RunAtLoad':True,'KeepAlive':True,'WorkingDirectory':str(ROOT),
          'StandardOutPath':str(DATA/'service.log'),'StandardErrorPath':str(DATA/'service.log')}
    with path.open('wb') as f: plistlib.dump(spec,f)
    subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=False)
    print('Start at login installed. Keep this app folder in its current location.')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--install-autostart',action='store_true')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args(); PORT=args.port
    init()
    if args.install_autostart: autostart(); raise SystemExit()
    try: httpd=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    except OSError:
        print('Port is occupied. If Aura Desk is already running, open http://127.0.0.1:'+str(PORT))
        raise SystemExit(1)
    threading.Thread(target=worker,daemon=True).start()
    print('Aura Desk: http://127.0.0.1:'+str(PORT)+'\nData: '+str(DATA)+'\nKeep running for deadline checks and email. Ctrl+C to stop.')
    if not args.no_browser: webbrowser.open('http://127.0.0.1:'+str(PORT))
    try: httpd.serve_forever()
    except KeyboardInterrupt: backup(force=True); httpd.server_close()

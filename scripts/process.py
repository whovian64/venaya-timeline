import json, os, re, subprocess, sys, math, time

DRY = os.environ.get('DRY') == '1'
EVENT = json.load(open(os.environ['GITHUB_EVENT_PATH']))
NAME = os.environ.get('GITHUB_EVENT_NAME', '')
REPO = os.environ.get('GITHUB_REPOSITORY', 'owner/repo')
OWNER = REPO.split('/')[0].lower()
DATA = os.environ.get('ENTRIES_FILE', 'entries.json')
MARK = '<!--venaya-entry-->'

BOOKS = {
    'Arcane Ascension': ['Sufficiently Advanced Magic', 'On the Shoulders of Titans', 'The Torch that Ignites the Stars', 'The Silence of Unworthy Gods', 'When Wizards Follow Fools', 'A Brief History of Chronomancy'],
    'War of Broken Mirrors': ['Forging Divinity', 'Stealing Sorcery', 'Defying Destiny'],
    'Weapons and Wielders': ['Six Sacred Swords', 'Diamantine', 'Soulbrand', 'Flowbreaker'],
    'Lost Edge': ['Edge of the Woods', 'Edge of the Dream'],
}
RESERVED_NAMES = {'whovian', 'andrew', 'andrew rowe', 'kester', 'book', 'book text', 'derived', 'community', 'claude', 'admin', 'owner', 'anonymous'}
RESERVED_COLORS = [('Book text', '#7b4fbf'), ('Book text', '#b79af0'), ('Andrew', '#b4690a'), ('Andrew', '#eaaa4a'), ('Kester', '#0b8a7a'), ('Kester', '#3fcbb9'), ('Derived', '#5d7a57'), ('Derived', '#a1b894'), ('Whovian', '#1f6feb'), ('Whovian', '#5aa2ff'), ('Whovian', '#2563eb'), ('the page background', '#ffffff'), ('the page background', '#0c1620')]
NEAR = 20
LANES = {'history', 'people', 'books', 'theory'}
CATS = {'magic', 'place', 'faction', 'other'}
LIMIT = 40


class Reject(Exception):
    pass


def lab(h):
    h = h.lstrip('#')
    c = []
    for i in (0, 2, 4):
        v = int(h[i:i + 2], 16) / 255
        c.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    x = (c[0] * 0.4124 + c[1] * 0.3576 + c[2] * 0.1805) / 0.95047
    y = c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722
    z = (c[0] * 0.0193 + c[1] * 0.1192 + c[2] * 0.9505) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))


def de(a, b):
    return math.dist(lab(a), lab(b))


def clean(v, n):
    v = '' if v is None else str(v)
    v = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', v).strip()
    return v[:n]


def num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e6:
        raise Reject('A year is not a valid number.')
    return v


def https(u):
    u = clean(u, 400)
    if not re.fullmatch(r'https://[^\s]+', u):
        raise Reject('A link must start with https://.')
    return u


def refs_of(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 5:
        raise Reject('Add between 1 and 5 sources.')
    out = []
    for r in raw:
        if not isinstance(r, dict):
            raise Reject('A source is malformed.')
        t = r.get('type')
        o = {'type': t}
        if t == 'book':
            series, book = clean(r.get('series'), 60), clean(r.get('book'), 80)
            if book not in BOOKS.get(series, []):
                raise Reject('A book source names a book that is not in the list.')
            ch = clean(r.get('ch'), 40)
            if not ch:
                raise Reject('Each book source needs a chapter.')
            o.update(series=series, book=book, ch=ch)
            if clean(r.get('page'), 40):
                o['page'] = clean(r.get('page'), 40)
        elif t == 'reddit':
            o['url'] = https(r.get('url'))
            for k, n in (('author', 60), ('date', 40)):
                if clean(r.get(k), n):
                    o[k] = clean(r.get(k), n)
        elif t == 'discord':
            if not clean(r.get('server'), 120):
                raise Reject('A Discord source needs the server and channel.')
            o['server'] = clean(r.get('server'), 120)
            for k, n in (('author', 60), ('date', 40)):
                if clean(r.get(k), n):
                    o[k] = clean(r.get(k), n)
        elif t == 'other':
            if not clean(r.get('desc'), 160):
                raise Reject('An Other source needs a description.')
            o['desc'] = clean(r.get('desc'), 160)
            if r.get('url'):
                o['url'] = https(r.get('url'))
        else:
            raise Reject('Unknown source type.')
        if clean(r.get('quote'), 400):
            o['quote'] = clean(r.get('quote'), 400)
        out.append(o)
    return out


def event_item(it):
    if not isinstance(it, dict):
        raise Reject('The entry is malformed.')
    title, text = clean(it.get('title'), 120), clean(it.get('text'), 1200)
    if len(title) < 3 or len(text) < 10:
        raise Reject('An event needs a title and a description.')
    lane = it.get('lane')
    if lane not in LANES:
        raise Reject('Unknown kind.')
    afr = num(it.get('afr'))
    out = {'title': title, 'text': text, 'lane': lane, 'afr': afr, 'refs': refs_of(it.get('refs'))}
    if it.get('end') is not None:
        end = num(it['end'])
        if end <= afr:
            raise Reject('The last year has to be after the first year.')
        out['end'] = end
    if it.get('ar') is not None:
        out['ar'] = num(it['ar'])
    if it.get('sys') in ('afr', 'ar', 'af'):
        out['sys'] = it['sys']
    return out


def lore_item(it):
    if not isinstance(it, dict):
        raise Reject('The entry is malformed.')
    name, summary = clean(it.get('name'), 100), clean(it.get('summary'), 800)
    if len(name) < 2 or len(summary) < 10:
        raise Reject('A lore item needs a name and a summary.')
    cat = it.get('cat')
    if cat not in CATS:
        raise Reject('Unknown section.')
    facts = it.get('facts') if isinstance(it.get('facts'), list) else []
    facts = [clean(f, 240) for f in facts if clean(f, 240)][:12]
    return {'name': name, 'summary': summary, 'cat': cat, 'kind': clean(it.get('kind'), 60), 'where': clean(it.get('where'), 100), 'facts': facts, 'refs': refs_of(it.get('refs'))}


def profile_for(data, login, payload):
    prof = data['profiles'].get(login)
    if prof:
        return prof, False
    name = re.sub(r'\s+', ' ', clean(payload.get('name'), 24))
    color = str(payload.get('color', '')).lower()
    if len(name) < 2:
        raise Reject('Your name has to be 2 to 24 characters.')
    if name.lower() in RESERVED_NAMES:
        raise Reject('That name is reserved.')
    if any(p['name'].lower() == name.lower() for p in data['profiles'].values()):
        raise Reject('Another contributor already uses that name.')
    if not re.fullmatch(r'#[0-9a-f]{6}', color):
        raise Reject('The color is not valid.')
    for label, hexv in RESERVED_COLORS:
        if de(color, hexv) < NEAR:
            raise Reject('Your color is too close to ' + label + '.')
    for p in data['profiles'].values():
        if de(color, p['color']) < NEAR:
            raise Reject("Your color is too close to " + p['name'] + "'s color.")
    return {'name': name, 'color': color}, True


def sh(*cmd, check=False):
    if DRY:
        print('DRY', ' '.join(cmd))
        return
    subprocess.run(cmd, check=check)


def say(num_, text):
    sh('gh', 'issue', 'comment', str(num_), '--body', text)


def finish(num_, label, text):
    sh('gh', 'label', 'create', label, '--force')
    say(num_, text)
    sh('gh', 'issue', 'edit', str(num_), '--add-label', label)
    sh('gh', 'issue', 'close', str(num_))


def save_and_push(data, msg):
    with open(DATA, 'w') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    sh('git', 'config', 'user.name', 'entries-bot')
    sh('git', 'config', 'user.email', 'entries-bot@users.noreply.github.com')
    sh('git', 'add', DATA)
    sh('git', 'commit', '-m', msg)
    sh('git', 'push', check=True)


def load():
    d = json.load(open(DATA))
    for k, v in (('profiles', {}), ('events', []), ('lore', []), ('blocked', [])):
        d.setdefault(k, v)
    return d


def handle_new(issue):
    body = issue.get('body') or ''
    if MARK not in body:
        print('not an entry issue')
        return
    num_ = issue['number']
    login = issue['user']['login']
    data = load()
    try:
        if login.lower() in [b.lower() for b in data['blocked']]:
            raise Reject('This account cannot add entries.')
        m = re.search(r'```json\s*(\{.*\})\s*```', body, re.S)
        if not m:
            raise Reject('No entry code was found in the issue.')
        try:
            payload = json.loads(m.group(1))
        except ValueError:
            raise Reject('The entry code is not valid.')
        if not isinstance(payload, dict) or payload.get('v') != 1:
            raise Reject('The entry code is not valid.')
        kind = 'lore' if payload.get('kind') == 'lore' else 'event'
        item = lore_item(payload.get('lore')) if kind == 'lore' else event_item(payload.get('item'))
        prof, new = profile_for(data, login, payload)
        mine = sum(1 for k in ('events', 'lore') for x in data[k] if x.get('login') == login)
        if mine >= LIMIT:
            raise Reject('Limit reached: ' + str(LIMIT) + ' entries per contributor.')
    except Reject as e:
        finish(num_, 'rejected', 'Not added: ' + str(e) + ' Fix it in the form and submit again.')
        return
    if new:
        data['profiles'][login] = prof
    item.update(id='i' + str(num_), issue=num_, login=login, created=int(time.time()))
    data['events' if kind == 'event' else 'lore'].append(item)
    save_and_push(data, 'Add ' + kind + ' from issue #' + str(num_))
    finish(num_, 'accepted', 'Added as ' + prof['name'] + '. It shows on the site within a few minutes (the site caches for about five). To have it removed, the owner comments /remove here.')


def handle_comment(issue, comment):
    text = (comment.get('body') or '').strip()
    who = comment['user']['login'].lower()
    if who != OWNER:
        return
    data = load()
    num_ = issue['number']
    if text.startswith('/remove'):
        before = len(data['events']) + len(data['lore'])
        data['events'] = [x for x in data['events'] if x.get('issue') != num_]
        data['lore'] = [x for x in data['lore'] if x.get('issue') != num_]
        if len(data['events']) + len(data['lore']) == before:
            say(num_, 'Nothing to remove for this issue.')
            return
        save_and_push(data, 'Remove entry from issue #' + str(num_))
        say(num_, 'Removed.')
    elif text.startswith('/block'):
        target = issue['user']['login']
        if target.lower() not in [b.lower() for b in data['blocked']]:
            data['blocked'].append(target)
        data['events'] = [x for x in data['events'] if x.get('login') != target]
        data['lore'] = [x for x in data['lore'] if x.get('login') != target]
        data['profiles'].pop(target, None)
        save_and_push(data, 'Block ' + target)
        say(num_, 'Blocked ' + target + ' and removed their entries.')


if NAME == 'issues' and EVENT.get('action') == 'opened':
    handle_new(EVENT['issue'])
elif NAME == 'issue_comment' and EVENT.get('action') == 'created':
    handle_comment(EVENT['issue'], EVENT['comment'])

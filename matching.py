import math
import re
import unicodedata
from collections import Counter, defaultdict

WORD = re.compile(r'[^\W_]+(?:[’\'-][^\W_]+)*', re.UNICODE)
# Include combining marks used by Bangla, Hindi and Arabic in the same token.
WORD = re.compile(WORD.pattern.replace('[^\\W_]+', r'(?:[^\W_][\u0300-\u036f\u0591-\u05c7\u0610-\u061a\u064b-\u065f\u0900-\u0903\u093a-\u094f\u0951-\u0957\u0962-\u0963\u0981-\u0983\u09bc-\u09cd\u09d7\u09e2-\u09e3]*)+'))
DEMO = 'Supercapacitors store electrical energy through the accumulation of ions at the interface between an electrode and an electrolyte. Activated carbon provides a porous framework with a large surface area for ion adsorption.'

def tokens(text):
    return list(WORD.finditer(text))

def normalize(token):
    return unicodedata.normalize('NFC', token).casefold()

def stream(text, exclusions):
    stopped = len(text)
    if 'bibliography' in exclusions:
        match = re.search(r'^\s*(references|bibliography|works cited)\s*$', text, re.I | re.M)
        if match:
            stopped = match.start()
    ranges = []
    if 'quotes' in exclusions:
        ranges.extend((m.start(), m.end()) for m in re.finditer(r'"[^"\n]+"|“[^”]+”|«[^»]+»', text[:stopped]))
    if 'citations' in exclusions:
        ranges.extend((m.start(), m.end()) for m in re.finditer(r'\[\s*\d[\d,; –—-]*\]|\([^()\n]*\b(?:19|20)\d{2}[a-z]?[^()\n]*\)', text[:stopped]))
    ranges.sort()
    active_end = 0
    index = 0
    group = []
    for word in tokens(text[:stopped]):
        while index < len(ranges) and ranges[index][0] <= word.start():
            active_end = max(active_end, ranges[index][1])
            index += 1
        if word.start() < active_end:
            if group:
                yield group
                group = []
        else:
            group.append(word)
    if group:
        yield group

def similarity(left, right):
    a, b = set(left), set(right)
    overlap = len(a & b)
    if overlap < 7 or overlap / len(a | b) < .53:
        return False
    x, y = Counter(left), Counter(right)
    cosine = sum(x[w] * y[w] for w in a & b) / math.sqrt(sum(v*v for v in x.values()) * sum(v*v for v in y.values()))
    if cosine < .76:
        return False
    row = list(range(len(right)+1))
    for i, word in enumerate(left, 1):
        new = [i]
        for j, other in enumerate(right, 1):
            new.append(min(row[j]+1, new[-1]+1, row[j-1]+(word != other)))
        row = new
    return row[-1] <= 2

def query_phrases(groups):
    phrases = []
    for group in groups:
        phrases.extend(' '.join(m.group() for m in group[i:i+10]) for i in range(0, len(group)-7, 8))
    if len(phrases) > 6:
        phrases = [phrases[round(i*(len(phrases)-1)/5)] for i in range(6)]
    return list(dict.fromkeys(phrases))

def compare(groups, sources):
    index = defaultdict(list)
    exact = defaultdict(set)
    for si, source in enumerate(sources):
        words = [normalize(m.group()) for m in tokens(source['text'])][:25000]
        for i in range(len(words)-7):
            window = tuple(words[i:i+10])
            exact[window].add(si)
            for j in range(len(window)-2):
                postings = index[window[j:j+3]]
                if len(postings) < 24:
                    postings.append((si, window))
    total = sum(map(len,groups))
    hits = defaultdict(set)
    passages = []
    offset = 0
    for group in groups:
        words = [normalize(m.group()) for m in group]
        coverage = [set() for _ in group]
        for i in range(len(words)-7):
            window = tuple(words[i:i+10])
            matched = set(exact.get(window, ()))
            for candidate in set(index.get(window[:3], ())) | set(index.get(window[-3:], ())) | set(index.get(window[3:6], ())):
                si, other = candidate
                if si not in matched and similarity(window, other):
                    matched.add(si)
            if not matched:
                continue
            for j in range(i, i+len(window)):
                coverage[j].update(matched)
            for si in matched:
                hits[si].update(range(offset+i,offset+i+len(window)))
        start = None
        for i, marked in enumerate(coverage + [set()]):
            if marked and start is None:
                start = i
            if not marked and start is not None:
                passages.append([group[start].start(), group[i-1].end()])
                start = None
        offset += len(group)
    union = set().union(*hits.values())
    score = round(len(union)*100/total, 2) if total else 0
    details = []
    for i, words in sorted(hits.items(), key=lambda hit:-len(hit[1])):
        details.append({**{k:v for k,v in sources[i].items() if k != 'text'}, 'words':len(words), 'percentage':round(len(words)*100/total,2)})
    return dict(score=score, unique=round(100-score,2) if total else 0, passages=passages, sources=details, analyzed=total)

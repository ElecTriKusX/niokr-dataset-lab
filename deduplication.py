"""Preserved V1 lexical features and approximate clustering; not a semantic audit."""
import re, hashlib
from collections import Counter, defaultdict
from functools import lru_cache

WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)

GRNTI = re.compile(
    r"(?<!\d)(\d{2})"
    r"(?:[.\-\s](\d{2}))?"
    r"(?:[.\-\s](\d{2}))?"
    r"(?!\d)"
)

TASK_HEADING = re.compile(
    r"(?:задач(?:и|а|у)?"
    r"(?:\s+(?:исследования|работы|проекта))?"
    r"|следующ(?:ие|их)\s+задач(?:и|а))"
    r"\s*[:\-]",
    re.IGNORECASE,
)

LIST_ITEM = re.compile(
    r"(?m)^\s*(?:\d{1,2}[.)]|[•—–-])\s+\S"
)

INLINE_ITEM = re.compile(
    r"(?<!\d)(?:[1-9]|1\d)[.)]\s+[А-Яа-я]"
)


def norm(s):
    s = re.sub(r"[^\w\s]", " ", str(s).casefold())
    return re.sub(r"\s+", " ", s).strip()


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def word_count(s):
    return len(WORDS.findall(s))


def flatten_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(flatten_text(x) for x in value)
    if isinstance(value, dict):
        for key in ("value", "text", "name", "description"):
            if key in value:
                return flatten_text(value[key])
    return ""


def grnti_codes(value):
    """Extract GRNTI-looking codes only from the rubrics field."""
    found = set()

    def visit(x):
        if isinstance(x, str):
            for m in GRNTI.finditer(x):
                parts = [p for p in m.groups() if p]
                found.add(".".join(parts))
        elif isinstance(x, list):
            for item in x:
                visit(item)
        elif isinstance(x, dict):
            for key, item in x.items():
                if isinstance(item, (list, dict)):
                    visit(item)
                elif key.lower() in {
                    "code", "rubric", "rubric_code", "grnti",
                    "grnti_code", "number", "value", "id",
                }:
                    visit(item)

    visit(value)
    return sorted(found)


def year_period(year):
    if 2015 <= year <= 2020:
        return "2015-2020"
    if 2021 <= year <= 2023:
        return "2021-2023"
    if year >= 2024:
        return "2024+"
    return "UNKNOWN"


def explicit_signal(text):
    heading = bool(TASK_HEADING.search(text))
    enumerated = max(
        len(LIST_ITEM.findall(text)),
        len(INLINE_ITEM.findall(text)),
    )
    if heading and enumerated >= 2:
        return 2
    if heading or enumerated >= 2:
        return 1
    return 0


@lru_cache(maxsize=50000)
def hash64(s):
    return int.from_bytes(
        hashlib.blake2b(
            s.encode("utf-8"), digest_size=8
        ).digest(),
        "big",
    )


def simhash64(title, annotation):
    """
    Approximate lexical fingerprint.
    Used only to FIND potential duplicates.
    """
    terms = Counter(
        WORDS.findall(title.casefold()) * 3
        + WORDS.findall(annotation.casefold())[:180]
    )
    if not terms:
        return 0

    chosen = sorted(
        terms.items(),
        key=lambda x: hash64(x[0])
    )[:48]

    vector = [0] * 64
    for token, weight in chosen:
        bits = hash64(token)
        weight = min(weight, 4)
        for i in range(64):
            vector[i] += (
                weight if (bits >> i) & 1 else -weight
            )

    result = 0
    for i, v in enumerate(vector):
        if v >= 0:
            result |= 1 << i
    return result


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        if self.size[a] < self.size[b]:
            a, b = b, a
        self.parent[b] = a
        self.size[a] += self.size[b]



def cluster(records, near_distance=3, bucket_cap=300):
    uf = UnionFind(len(records))
    hash_first = {}
    duplicate_pairs = []
    exact_pairs = 0

    for i, r in enumerate(records):
        h = r["norm_hash"]
        if h in hash_first:
            j = hash_first[h]
            uf.union(i, j)
            exact_pairs += 1
            if len(duplicate_pairs) < 2000:
                duplicate_pairs.append({
                    "type": "exact",
                    "reg_key_a": records[j]["reg_key"],
                    "reg_key_b": r["reg_key"],
                    "distance": 0,
                })
        else:
            hash_first[h] = i

    near_pairs = 0
    skipped_bucket_checks = 0

    if True:
        buckets = defaultdict(list)

        for i, r in enumerate(records):
            sig = r["simhash"]

            for band in range(4):
                part = (sig >> (16 * band)) & 65535
                bucket_key = (band, part)
                bucket = buckets[bucket_key]

                if len(bucket) >= bucket_cap:
                    skipped_bucket_checks += 1
                    continue

                for j in bucket:
                    previous = records[j]

                    if r["norm_hash"] == previous["norm_hash"]:
                        continue

                    shorter = min(
                        r["chars"], previous["chars"]
                    )
                    longer = max(
                        r["chars"], previous["chars"]
                    )
                    if longer == 0 or shorter / longer < 0.8:
                        continue

                    distance = (
                        sig ^ previous["simhash"]
                    ).bit_count()

                    if distance <= near_distance:
                        if uf.find(i) != uf.find(j):
                            uf.union(i, j)
                            near_pairs += 1

                            if len(duplicate_pairs) < 2000:
                                duplicate_pairs.append({
                                    "type": "possible_near",
                                    "reg_key_a": previous["reg_key"],
                                    "reg_key_b": r["reg_key"],
                                    "distance": distance,
                                })

                bucket.append(i)

    cluster_members = defaultdict(list)

    for i, r in enumerate(records):
        root = uf.find(i)
        cluster_members[root].append(i)
        r["cluster_id"] = f"C{root:07d}"

    # One representative per detected duplicate cluster.
    representatives = [
        records[min(members)]
        for members in cluster_members.values()
    ]

    eligible = [
        r for r in representatives
        if r["eligible"]
    ]

    if not eligible:
        raise RuntimeError("No eligible projects")

    return representatives, dict(exact_duplicate_links=exact_pairs, possible_near_duplicate_links=near_pairs, skipped_near_bucket_checks=skipped_bucket_checks)

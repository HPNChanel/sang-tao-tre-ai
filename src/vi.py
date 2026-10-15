"""Vietnamese support: diacritic folding + keyword table that rewrites common Vietnamese
question/instruction phrasing into the English cue words the rule planner understands.
Entity names are left in place (matching is diacritic-insensitive via textmatch)."""
from __future__ import annotations

import re
import unicodedata


def fold(s: str) -> str:
    """lowercase, strip diacritics, đ->d."""
    s = unicodedata.normalize("NFD", str(s).replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


_VI_HINT = re.compile(r"[ăâđêôơưàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]", re.I)
_VI_WORDS = re.compile(r"\b(bao nhieu|nhieu nhat|cao nhat|thap nhat|tong|trung binh|nam nao|so sanh|nhieu hon|it hon|"
                       r"xep hang|co phai|hay khong|khong\?|chi tra loi|tra loi|cua|trong|nam|nao|nhat|tuoi|nguoi|la)\b")


def is_vietnamese(q: str) -> bool:
    return bool(_VI_HINT.search(q)) or len(_VI_WORDS.findall(fold(q).lower())) >= 2


# Cardinals count the following noun. 'năm người' is five people: the next word is a noun.
# 'năm 1997' is not (a year is digits, not a noun). 'của nam từ' is not (từ is a function word).
_CARD = {"mot": "one", "hai": "two", "ba": "three", "bon": "four", "nam": "five",
         "sau": "six", "bay": "seven", "tam": "eight", "chin": "nine", "muoi": "ten"}
_NOT_NOUN = (
    "tu|den|toi|tro|cua|va|la|co|trong|cho|voi|o|duoc|theo|nay|do|nao|nhat|hon|hay|hoac|"
    "cac|nhung|tai|ve|khi|neu|thi|se|da|dang|roi|nua|qua|rat|cung|khong|bao|nhieu|"
    "in|of|and|the|to|for|from|by|with|on|at|or|dai|so|gioi|nam|"
    "tram|muoi|nghin|ngan|le|linh|ruoi"
)


def _card_repl(m):
    return _CARD[m.group(1).lower()] + " " + m.group(2)


_ORD_VI = {"nhat": "first", "hai": "second", "nhi": "second", "ba": "third", "tu": "fourth",
           "nam": "fifth", "sau": "sixth", "bay": "seventh", "tam": "eighth", "chin": "ninth", "muoi": "tenth"}
_SUP_VI = {"cao": "highest", "thap": "lowest", "lon": "largest", "nho": "smallest", "nhieu": "most", "it": "least"}


def _ord_en(tok: str) -> str:
    if str(tok).isdigit():
        n = int(tok)
        words = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
        return words[n] if 1 <= n < len(words) else f"{n}th"
    return _ORD_VI.get(tok, tok)


def _sup_then_ord(m):
    return _ord_en(m.group(2)) + " " + _SUP_VI[m.group(1)]


def _hang_ord(m):
    return _ord_en(m.group(1)) + " highest"


def _ranks_ord(m):
    return "ranks " + _ord_en(m.group(1))


def _top_vi_card(m):
    return m.group(1) + " " + _CARD[m.group(2)]


def _vi_card_sup(m):
    return _CARD[m.group(1)] + " " + _SUP_VI[m.group(2)]


# Operation phrases added for paraphrases. Longer forms stay ahead of the generic
# cardinal/superlative swaps. new_ops=False leaves these out.
NEW_OP_KEYWORDS = [
    (r"\b(cao|thap|lon|nho|nhieu|it)\s+nhat\s+thu\s+(nhat|hai|nhi|ba|tu|nam|sau|bay|tam|chin|muoi|\d+)\b", _sup_then_ord),
    (r"\b(cao|thap|lon|nho|nhieu|it)\s+thu\s+(nhat|hai|nhi|ba|tu|nam|sau|bay|tam|chin|muoi|\d+)\b", _sup_then_ord),
    (r"\bhang\s+(nhat|nhi|ba|tu|nam|sau|bay|tam|chin|muoi)\b", _hang_ord),
    (r"\b(?:dung|xep)\s+thu\s+(nhat|hai|nhi|ba|tu|nam|sau|bay|tam|chin|muoi|\d+)\b", _ranks_ord),
    (r"\bhon\b(.{0,50}?)\bbao nhieu\b", r" how many more \1 "),
    (r"\bchenh nhau\b|\bkhac nhau\b|\bkhac biet\b", " difference between "),
    (r"\bhieu giua\b|\bhieu cua\b", " difference between "),
    (r"\btru\b", " minus "),
    (r"\blan luot\b", " respectively "),
    (r"\bca hai\b", " both "),
    (r"\bcap\s+(diem|gia tri|so lieu|ket qua)\b", r" pair of \1 "),
    (r"\b(top|bottom)\s+(mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\b", _top_vi_card),
    (r"\b(mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\s+(cao|thap|lon|nho|nhieu|it)\s+nhat\b", _vi_card_sup),
]


# (folded Vietnamese regex, English replacement). Order matters: longer phrases first.
KEYWORDS = [
    # instructions
    (r"chi (tra loi|ghi|dien) (bang )?so nguyen( duong)?", " Answer with only the integer."),
    (r"chi (tra loi|ghi|dien) (bang )?so,? lam tron (den )?(\d+) chu so thap phan", r" Answer with only the number rounded to \4 decimal places."),
    (r"lam tron (den )?(\d+) chu so thap phan", r" rounded to \2 decimal places"),
    (r"chi (tra loi|ghi) (co|yes) hoac (khong|no)", " Answer with only Yes or No."),
    (r"chi (tra loi|ghi) (ten|nhan)", " Answer with only the name."),
    (r"chi (tra loi|ghi) (so )?nam", " Answer with only the 4-digit year."),
    (r"(tra loi|ghi) theo dinh dang yyyy-mm-dd", " Answer in YYYY-MM-DD format."),
    (r"phan cach (boi|bang) dau phay( va mot dau cach)?", " separated by a comma and one space"),
    (r"\bliet ke\b", " list "),
    # 'năm' + noun (not a 4-digit year, not a function word) is the cardinal five; same for other cardinals
    (rf"\b(mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\s+(?!(?:{_NOT_NOUN})\b)([a-z]{{2,}})\b", _card_repl),
    (r"\btrong\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+nguoi\s+(?:lon tuoi|gia)\s+nhat\b",
     r" among the \1 oldest "),
    (r"\btrong\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+nguoi\s+(?:tre|nho tuoi)\s+nhat\b",
     r" among the \1 youngest "),
    # common nouns/phrases (general statistics vocabulary; works with or without diacritics)
    (r"\bty le that nghiep\b|\bti le that nghiep\b", " unemployment rate "),
    (r"\bthat nghiep\b", " unemployed "),
    (r"\bco viec lam\b", " employed "),
    (r"\bviec lam\b", " employment "),
    (r"\b(?:ngoai|phi) nong nghiep\b", " nonagricultural "),
    (r"\btrong nganh nong nghiep\b|\bnganh nong nghiep\b", " agriculture "),
    (r"\bnong nghiep\b", " agricultural "),
    (r"\bkhong luong\b", " unpaid "),
    (r"\bgia dinh\b", " family "),
    (r"\blao dong\b", " workers "),
    (r"\bgia tri\b", " value "),
    (r"\bxuat khau\b", " export "),
    (r"\bnhap khau\b", " import "),
    (r"\bdoanh thu\b", " revenue "),
    (r"\bdan so\b", " population "),
    (r"\bdien tich\b", " area "),
    (r"\bnhom tuoi nao\b", " which age group "),
    (r"\bnhom tuoi\b", " age group "),
    (r"\bnganh\b", " industry "),
    (r"\bdiem\b", " score "),
    (r"\bnhap hoc\b", " admission "),
    (r"\btat ca moi nguoi\b|\bmoi nguoi\b", " all people "),
    (r"\bnguoi lon tuoi nhat\b|\b(?:ai|nguoi nao) (?:lon tuoi|gia) nhat\b", " who is the oldest person "),
    (r"\bnguoi tre nhat\b|\b(?:ai|nguoi nao) (?:tre|nho tuoi) nhat\b", " who is the youngest person "),
    (r"\bnho tuoi nhat\b", " youngest "),
    (r"\b(?:gioi_nam gioi|nam gioi|dan ong)\b", " men "),
    (r"\b(?:nu gioi|phu nu)\b", " women "),
    # a 4-digit year after 'nam' is a calendar year, never men.
    # gioi_nam is the undiacritic gender/south token, shielded before folding
    # so it is not the cardinal 'năm'.
    (r"\b(cua|o|trong|danh cho|doi voi) (?:gioi_nam|nam)\b(?!\s*(?:19|20)\d\d)", r" of men "),
    (r"\b(cua|o|trong|danh cho|doi voi) nu\b", r" of women "),
    (r"\btu (\d+) tuoi tro len\b", r" aged \1 years and over "),
    (r"\b(?:so nguoi|so luong nguoi)\b", " number of people "),
    # 'gio' is hours; an age span says 'tuoi' or is not followed by a unit of time
    (r"\b(?:tu\s+)?(\d+) (?:den|toi) (\d+) gio\b", r" \1 to \2 hours "),
    (r"\btu (\d+) (?:den|toi) (\d+) tuoi\b", r" aged \1 to \2 "),
    (r"\btu (\d+) (?:den|toi) (\d+)\b(?!\s*(?:gio|tuoi)\b)", r" aged \1 to \2 "),
    (r"\b(\d+) (?:den|toi) (\d+) tuoi\b", r" aged \1 to \2 "),
    (r"\b(\d+) (?:den|toi) (\d+)\b(?!\s*gio\b)", r" \1 to \2 "),
    (r"\bsinh nam ((?:19|20)\d\d)\b", r" born in \1 "),
    (r"\bnam ((?:19|20)\d\d)\b", r" in \1 "),
    # wh / ops
    (r"\bit nhat la\b", " at least "),
    (r"\btu (\d+)( \w+)? tro len\b", r" \1 or more \2 "),
    (r"\btu (\d+)( \w+)? tro xuong\b", r" \1 or less \2 "),
    (r"\b(?:nhieu|dong)\b(.{0,90}?)\bnhat\b", r" most \1 "),
    (r"\bthang (1[0-2]|[1-9])\b", lambda m: " " + ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m.group(1)) - 1] + " "),
    (r"\btu\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(?:den|toi)\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", r" \1 to \2 "),
    (r"\b(\d+) tro len\b", r" \1 or more "),
    (r"\bnam nao\b", " which year "),
    (r"\bngay nao\b", " what date "),
    (r"\b(?:la|bang|duoc) bao nhieu\b", " what is the value "),
    (r"\bco bao nhieu\b", " how many "),
    (r"\bbao nhieu\b", " how many "),
    (r"\bnhieu hon bao nhieu\b", " how many more "),
    (r"\bchenh lech\b|\bhieu so\b|\bkhoang cach\b", " absolute difference between "),
    (r"\bthay doi\b", " change "),
    (r"\btu cao den thap\b|\bgiam dan\b", " descending "),
    (r"\btu thap den cao\b|\btang dan\b", " ascending "),
    (r"\btang\b", " increase "),
    (r"\bgiam\b", " decrease "),
    (r"\bcao nhat\b|\blon nhat\b|\bnhieu nhat\b", " highest "),
    (r"\bthap nhat\b|\bnho nhat\b|\bit nhat\b", " lowest "),
    (r"\bsom nhat\b", " earliest "),
    (r"\bmuon nhat\b|\bgan nhat\b", " latest "),
    (r"\bthu hai\b", " second "),
    (r"\bthu ba\b", " third "),
    (r"\btre nhat\b", " youngest "),
    (r"\bgia nhat\b|\blon tuoi nhat\b", " oldest "),
    (r"\btong cong\b|\btong so\b|\btong\b|\bcong lai\b", " total "),
    (r"\btrung binh\b|\bbinh quan\b", " average "),
    (r"\bty le\b|\bti le\b|\bty trong\b", " share "),
    (r"\bxep hang\b|\bsap xep\b", " rank "),
    (r"\bnhieu hon\b|\bcao hon\b|\blon hon\b|\bvuot\b", " greater than "),
    (r"\bit hon\b|\bthap hon\b|\bnho hon\b", " less than "),
    (r"\bso sanh\b", " compare "),
    (r"\bcai nao\b|\bnao\b", " which "),
    (r"\bai\b", " who "),
    (r"\bco phai\b|\bphai khong\b|\bhay khong\b|\bco\b(?=.*\bkhong\s*\??\s*$)", " is it "),
    (r"\bkhong\s*\?\s*$", " ? Answer with only Yes or No."),
    (r"\btat ca\b|\bmoi\b", " all "),
    (r"\btrong nam\b", " in "),
    (r"\bcua\b", " of "),
    (r"\bva\b", " and "),
    (r"\bgio\b", " hours "),
    (r"\bthang\b", " month "),
]


def translate(q: str, new_ops: bool = True) -> tuple[str, int]:
    """Return (rewritten query, number of keyword hits). Entity names keep their original form.

    new_ops=False skips the operation phrases added for paraphrases (rank, difference,
    top-N number words, pair). Callers use that when those phrases did not resolve.

    Undiacritic 'nam' is gender or south. 'năm' is five or a year. Shield the
    undiacritic token before folding so a number rule cannot turn it into five.
    """
    q = re.sub(r"\bnam\b", "gioi_nam", q)
    s = fold(q)
    hits = 0
    patterns = (NEW_OP_KEYWORDS + KEYWORDS) if new_ops else KEYWORDS
    for pat, rep in patterns:
        s, n = re.subn(pat, rep, s, flags=re.I)
        hits += n
    s = s.replace("gioi_nam", "nam")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\?\s*(?=Answer)", "? ", s)
    s = re.sub(r"\.{2,}", ".", s)
    return s, hits


# Column-role words. A header matches a role when one of these phrases is a whole word in it.
COLUMN_HINTS = (
    ("shift", ("shift", "kip", "ca truc", "ca lam", "ca")),
    ("city", ("city", "town", "thanh pho", "tp")),
    ("province", ("province", "tinh thanh", "tinh")),
    ("region", ("region", "area", "khu vuc", "mien", "vung")),
    ("department", ("department", "dept", "phong ban", "bo phan", "phong")),
    ("branch", ("branch", "chi nhanh")),
    ("team", ("team", "group", "nhom", "doi")),
    ("score", ("score", "point", "diem so", "diem")),
    ("age", ("age", "tuoi")),
    ("hours", ("hours", "hour", "gio lam", "gio")),
    ("pay", ("salary", "wage", "luong", "thu nhap")),
)

# Spoken shift names, used only when the literal token is not itself a cell value.
VALUE_ALIASES = {
    "dem": ("night",),
    "toi": ("night",),
    "sang": ("day", "morning"),
    "chieu": ("evening", "afternoon"),
}

# Tokens that look like a filter value but are grammar or a row-group word.
# "ca" + one of these is "cả ..." (all of), not a shift name.
_FILTER_SKIP = {
    "ngay", "nuoc", "het", "moi", "roi", "dan", "bo", "gia", "the", "nay", "do",
    "nam", "nu", "phu", "gioi", "ong", "ba", "tuoi", "nganh", "nhom", "nguoi",
    "viec", "lam", "diem", "score", "tong", "cao", "thap", "nhat", "bao",
    "nhieu", "cac", "dong", "ban", "ghi", "truong", "hop", "thanh", "pho",
    "tinh", "khu", "vuc", "quoc", "gia", "phong", "phan", "mien", "vung",
    "chi", "xet", "loc", "tai", "thuoc", "cua", "cho", "biet", "hay", "nhung",
    "tat", "moi", "ca",
    "ai", "mot", "hai", "bon", "sau", "bay", "tam", "chin", "muoi", "cap",
    "khoa", "hoc", "sinh", "bang", "la", "co", "ma", "voi", "trong", "so",
    "den", "tro", "len", "xuong", "dung", "dat", "muc", "chua", "vuot",
    "chinh", "xac", "theo", "nao", "gi", "cac", "nay", "roi", "va", "hoac",
    "neu", "thi", "se", "da", "dang", "rat", "cung", "khong", "duoc", "cac",
    "ban", "ghi", "dong", "nay", "nao", "nhieu", "bao", "tra", "loi", "so",
    "nguyen", "thap", "phan", "lam", "tron", "chu", "den", "muc",
}

_TYPES = (
    "thanh pho|tinh thanh|khu vuc|quoc gia|chi nhanh|phong ban|bo phan|"
    "mien|vung|tp|nhom|kip|ca|doi"
)
_SHIFT = re.compile(
    r"\b(?:lam(?:\s+viec)?(?:\s+theo)?\s+(?:ca|kip)|ca\s+lam(?:\s+viec)?|kip\s+lam|"
    r"ca\s+truc|ca\s+(?:la|bang)|kip\s+(?:la|bang))\s+([a-z0-9][a-z0-9_-]*)\b"
)
_TYPED = re.compile(
    rf"\b(?:o|tai|thuoc|cua)\s+({_TYPES})\s+(?:la\s+|bang\s+)?([a-z0-9][a-z0-9_-]*)\b"
)
_PREP = re.compile(r"\b(?:o|tai|thuoc)\s+([a-z0-9][a-z0-9_-]*)\b")
_INTRO = re.compile(
    r"\bchi (?:xet|loc)\b|\bxet rieng\b|\bgioi han (?:trong|o)\b|\bloc (?:ra|nhung)\b"
)
_THRESH = re.compile(
    r"\b(chua\s+(?:toi|dat|vuot|den|bang)|dat\s+dung|dung\s+bang|bang\s+dung|"
    r"chinh\s+xac(?:\s+bang)?)"
    r"(?:\s+muc)?\s+(-?\d+(?:\.\d+)?)\b"
)
_NUMWORD = (
    "khong|mot|hai|ba|bon|tu|fiveword|sau|bay|tam|chin|muoi|tram|nghin|ngan|le|linh|ruoi"
)
_THRESH_WORD = re.compile(
    r"\b(chua\s+(?:toi|dat|vuot|den|bang)|dat\s+dung|dung\s+bang|bang\s+dung|"
    r"chinh\s+xac(?:\s+bang)?)"
    r"(?:\s+muc)?\s+((?:" + _NUMWORD + r")(?:\s+(?:" + _NUMWORD + r")){0,5})\b"
)
_THRESH_CUE = re.compile(
    r"\b(chua\s+(?:toi|dat|vuot|den|bang)|dat\s+dung|dung\s+bang|bang\s+dung|"
    r"chinh\s+xac(?:\s+bang)?)\b"
)
_ROLE_VALUE = re.compile(
    r"\b(?:thuoc|o|tai)\s+([a-z][a-z0-9]{1,20})\s+([a-z0-9][a-z0-9_-]*)\b"
)
_ONES = {"khong": 0, "mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4,
         "fiveword": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9}


def _fold_filters(question: str) -> str:
    """Fold a question for filter parsing.

    'năm' and 'lăm' are the digit five. Undiacritic 'nam' is gender or south
    and stays a word, never a digit.
    """
    raw = re.sub(r"\s+", " ", str(question))
    raw = re.sub(r"\bnam\b", "gioi_nam", raw)
    raw = re.sub(r"\bnăm\b", " fiveword ", raw, flags=re.I)
    raw = re.sub(r"\blăm\b", " fiveword ", raw, flags=re.I)
    return re.sub(r"\s+", " ", fold(raw).lower())


def _vi_cardinal(phrase):
    """A Vietnamese cardinal, including hundreds, or None.

    Scales are applied as they are spoken: 'hai tram fiveword muoi' is 250,
    not 205 and not 5.
    """
    total = group = acc = 0
    saw = False
    for tok in str(phrase).split():
        if tok in _ONES:
            acc = _ONES[tok]
            saw = True
        elif tok == "muoi":
            acc = (acc or 1) * 10
            group += acc
            acc = 0
            saw = True
        elif tok == "tram":
            acc = (acc or 1) * 100
            group += acc
            acc = 0
            saw = True
        elif tok in ("nghin", "ngan"):
            group += acc
            total += (group or 1) * 1000
            group = acc = 0
            saw = True
        elif tok in ("le", "linh"):
            continue
        elif tok == "ruoi":
            acc += 0.5
            saw = True
        else:
            return None
    if not saw:
        return None
    return total + group + acc


def _leading_cardinal(text):
    toks = []
    allowed = set(_ONES) | {"muoi", "tram", "nghin", "ngan", "le", "linh", "ruoi"}
    for tok in str(text).split():
        if tok not in allowed:
            break
        toks.append(tok)
    if not toks:
        return None
    return _vi_cardinal(" ".join(toks))


def _clause(text, start, end):
    """The phrase that holds a threshold, cut at a comma or sentence boundary."""
    left = max(text.rfind(ch, 0, start) for ch in ",.;?")
    rights = [text.find(ch, end) for ch in ",.;?"]
    rights = [i for i in rights if i >= 0]
    right = min(rights) if rights else len(text)
    return text[left + 1:right]


def filter_constraints(question: str):
    """Row-filter cues in a Vietnamese question, or None when there are none.

    Recognizes subset frames ('chi xet'), a role plus a value ('lam ca',
    'o thanh pho', 'thuoc ... '), a value after o/tai/thuoc, and thresholds
    'chua toi' / 'dat dung'. Grammar words are not values. The caller binds
    each cue to one column; this function does not look at the table.
    """
    text = _fold_filters(question)
    cats, seen = [], set()

    def add(hint, value, force=False):
        value = value.lower()
        if value == "gioi_nam":
            value = "nam"
            force = True
        if value == "fiveword":
            return
        if not force and value in _FILTER_SKIP:
            return
        key = ((hint or ""), value)
        if key in seen:
            return
        seen.add(key)
        cats.append({"hint": hint, "value": value})

    for m in _SHIFT.finditer(text):
        add("shift", m.group(1))
    for m in _TYPED.finditer(text):
        add(m.group(1), m.group(2))
    for m in _ROLE_VALUE.finditer(text):
        add(m.group(1), m.group(2))
    nums = []
    covered = []

    def add_num(op, value, start, end):
        nums.append({"op": op, "value": float(value), "window": _clause(text, start, end)})
        covered.append((start, end))

    for m in _THRESH.finditer(text):
        word = re.sub(r"\s+", " ", m.group(1))
        op = "<" if word.startswith("chua") else "=="
        add_num(op, m.group(2), m.start(), m.end())
    for m in _THRESH_WORD.finditer(text):
        if any(a <= m.start() and m.end() <= b for a, b in covered):
            continue
        value = _vi_cardinal(m.group(2))
        if value is None:
            continue
        word = re.sub(r"\s+", " ", m.group(1))
        op = "<" if word.startswith("chua") else "=="
        add_num(op, value, m.start(), m.end())
    unparsed = False
    for m in _THRESH_CUE.finditer(text):
        if any(a <= m.start() < b for a, b in covered):
            continue
        rest = re.split(r"[,.;?]", text[m.end():], maxsplit=1)[0]
        if re.search(r"\d", rest) or _leading_cardinal(rest) is not None:
            continue
        unparsed = True
    type_words = set()
    for phrase in _TYPES.split("|"):
        type_words.update(phrase.split())
    preps = []
    for m in _PREP.finditer(text):
        tok = m.group(1).lower()
        if tok in preps:
            continue
        # The value after thuộc/ở/tại is a filter even when it is also a gender word.
        # A type word ('thanh' in 'thuộc thành phố') is not itself the value.
        if tok in ("gioi_nam", "nam", "nu"):
            add(None, "nam" if tok == "gioi_nam" else tok, force=True)
            continue
        if tok in _FILTER_SKIP or tok in type_words:
            continue
        add(None, tok, force=True)
        preps.append(tok)
    intro = bool(_INTRO.search(text))
    if not cats and not nums and not preps and not intro and not unparsed:
        return None
    return {"cats": cats, "nums": nums, "preps": preps, "intro": intro, "unparsed": unparsed}

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
    if re.search(r"\b(parmi|répondez|pourcentage|uniquement)\b", q, re.I):
        return False
    return bool(_VI_HINT.search(q)) or len(_VI_WORDS.findall(fold(q).lower())) >= 2


# Cardinals count the following noun. 'năm người' is five people: the next word is a noun.
# 'năm 1997' is not (a year is digits, not a noun). 'của nam từ' is not (từ is a function word).
_CARD = {"mot": "one", "hai": "two", "ba": "three", "bon": "four", "nam": "five",
         "sau": "six", "bay": "seven", "tam": "eight", "chin": "nine", "muoi": "ten"}
_NOT_NOUN = (
    "tu|den|toi|tro|cua|va|la|co|trong|cho|voi|o|duoc|theo|nay|do|nao|nhat|hon|hay|hoac|"
    "cac|nhung|tai|ve|khi|neu|thi|se|da|dang|roi|nua|qua|rat|cung|khong|bao|nhieu|"
    "in|of|and|the|to|for|from|by|with|on|at|or|dai|so|gioi|nam"
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
    (r"\b(?:chi tra loi bang so voi dung|lam tron den) (mot|hai|ba|\d+) chu so thap phan[^.?!]*",
     lambda m: " Answer with only the number rounded to " + str({"mot": 1, "hai": 2, "ba": 3}.get(m[1], m[1])) + " decimal places."),
    (r"\bbang bao nhieu phan tram so voi\b", " ratio percent to "),
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
    (r"\b(?:nam gioi|dan ong)\b", " men "),
    (r"\b(?:nu gioi|phu nu)\b", " women "),
    # a 4-digit year after 'nam' is a calendar year, never men
    (r"\b(cua|o|trong|danh cho|doi voi) nam\b(?!\s*(?:19|20)\d\d)", r" of men "),
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


def translate(q: str, new_ops: bool = True, entities=()) -> tuple[str, int]:
    """Return (rewritten query, number of keyword hits). Entity names keep their original form.

    new_ops=False skips the operation phrases added for paraphrases (rank, difference,
    top-N number words, pair). Callers use that when those phrases did not resolve.
    """
    s = q
    instruction = re.search(r"\b(?:chỉ (?:trả lời|ghi|điền)|làm tròn|chi (?:tra loi|ghi|dien)|lam tron|answer|respond)\b", s, re.I)
    tail = s[instruction.start():] if instruction else ""
    s = s[:instruction.start()] if instruction else s
    protected = {}
    for entity in sorted(set(entities), key=len, reverse=True):
        label = entity.lower()
        if len(label) < 2:
            continue
        pattern = r"(?<!\w)" + re.escape(label) + r"(?!\w)"
        if re.search(pattern, s, re.I):
            key = "zzentity" + str(len(protected)) + "zz"
            s = re.sub(pattern, key, s, flags=re.I)
            protected[key] = entity
    s = fold(s + tail)
    hits = 0
    patterns = (NEW_OP_KEYWORDS + KEYWORDS) if new_ops else KEYWORDS
    for pat, rep in patterns:
        s, n = re.subn(pat, rep, s, flags=re.I)
        hits += n
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\?\s*(?=Answer)", "? ", s)
    s = re.sub(r"\.{2,}", ".", s)
    for key, entity in protected.items():
        s = s.replace(key, entity)
    return s, hits

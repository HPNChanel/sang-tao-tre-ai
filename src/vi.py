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
    # common nouns/phrases (general statistics vocabulary; works with or without diacritics)
    (r"\bty le that nghiep\b|\bti le that nghiep\b", " unemployment rate "),
    (r"\bthat nghiep\b", " unemployed "),
    (r"\bco viec lam\b", " employed "),
    (r"\bviec lam\b", " employment "),
    (r"\bphi nong nghiep\b", " nonagricultural "),
    (r"\bnong nghiep\b", " agricultural "),
    (r"\bkhong luong\b", " unpaid "),
    (r"\blao dong\b", " workers "),
    (r"\bgia tri\b", " value "),
    (r"\bxuat khau\b", " export "),
    (r"\bnhap khau\b", " import "),
    (r"\bdoanh thu\b", " revenue "),
    (r"\bdan so\b", " population "),
    (r"\bdien tich\b", " area "),
    (r"\bnhom tuoi\b", " age group "),
    (r"\bnganh\b", " industry "),
    (r"\bdiem\b", " score "),
    (r"\btat ca moi nguoi\b|\bmoi nguoi\b", " all people "),
    (r"\b(?:ai|nguoi nao) (?:lon tuoi|gia) nhat\b", " who is the oldest person "),
    (r"\b(?:ai|nguoi nao) (?:tre|nho tuoi) nhat\b", " who is the youngest person "),
    (r"\bnho tuoi nhat\b", " youngest "),
    (r"\b(?:nam gioi|dan ong)\b", " men "),
    (r"\b(?:nu gioi|phu nu)\b", " women "),
    (r"\b(cua|o|trong|danh cho) nam\b(?!\s*(?:19|20)\d\d)", r" of men "),
    (r"\b(cua|o|trong|danh cho) nu\b", r" of women "),
    (r"\btu (\d+) tuoi tro len\b", r" aged \1 years and over "),
    (r"\b(?:so nguoi|so luong nguoi)\b", " number of people "),
    (r"\btu (\d+) (?:den|toi) (\d+)( tuoi)?\b", r" aged \1 to \2 "),
    (r"\bnam ((?:19|20)\d\d)\b", r" in \1 "),
    # wh / ops
    (r"\bit nhat la\b", " at least "),
    (r"\btu (\d+)( \w+)? tro len\b", r" \1 or more \2 "),
    (r"\btu (\d+)( \w+)? tro xuong\b", r" \1 or less \2 "),
    (r"\bnhieu\b(.{0,90}?)\bnhat\b", r" most \1 "),
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
    (r"\btu cao den thap\b|\bgiam dan\b", " descending "),
    (r"\btu thap den cao\b|\btang dan\b", " ascending "),
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
    (r"\bthang\b", " month "),
]


def translate(q: str) -> tuple[str, int]:
    """Return (rewritten query, number of keyword hits). Entity names keep their original form."""
    s = fold(q)
    hits = 0
    for pat, rep in KEYWORDS:
        s, n = re.subn(pat, rep, s, flags=re.I)
        hits += n
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\?\s*(?=Answer)", "? ", s)
    s = re.sub(r"\.{2,}", ".", s)
    return s, hits

#!/usr/bin/env python3
"""계속 새로 쓰는 장치 (BR-A)

input/ 폴더의 리추얼 기록·출석 숫자·과제 목록을 읽어
  1) 사이트 숫자 칸 (출처 포함)
  2) 세 능력별 문단 후보 (날짜·근거 포함)
를 output/ 에 다시 만든다. 사이트에는 input/approved.txt 에 적힌 후보만 들어간다.

같은 입력이면 같은 결과가 나오도록:
  - AI 호출, 현재 시각, 난수를 쓰지 않는다.
  - 모든 목록은 정해진 기준으로 정렬한다.
  - 기준일은 실행한 날이 아니라 입력 기록의 마지막 날짜다.
표준 라이브러리만 쓴다 (Python 3.8 이상).
"""
import csv
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
IN = BASE / "input"
OUT = BASE / "output"
SITE_DATA = BASE.parent / "site" / "data.js"  # 사이트 폴더가 옆에 있으면 함께 갱신

ABILITIES = {
    "자기조절력": ["집중", "숨을", "호흡", "리셋", "다시", "불안", "지켰", "덮", "차분", "전환", "다독", "안정", "조절", "감정"],
    "대인관계력": ["도와", "도움", "도울", "설명", "알려", "인사", "대화", "공감", "함께", "챙", "웃", "다가"],
    "자기동기력": ["목표", "공부", "과제", "책", "도전", "발전", "완성", "끝까지", "배우고", "배워", "이해하려", "따라가", "차근차근", "즐겁"],
}
# 문단 후보로 볼 칸 (본인이 쓴 칸만. 동료가 쓴 말은 '동료 피드백'으로 따로 모은다)
OWN_FIELDS = [
    "강점이 드러난 일화",
    "강점을 위해 노력하고 생각한 것",
    "내가 나눈 감사",
    "나에게 남기는 말",
    "오늘의 첫 행동",
]
PEER_FIELD = re.compile(r"동료 \d가 말해 준 내 장점")
PER_ABILITY = 8  # 능력마다 후보 수


def fail(msg):
    print("오류:", msg)
    sys.exit(1)


# ---------- 입력 읽기 ----------
def load_redact():
    p = IN / "redact.txt"
    if not p.exists():
        return []
    words = [w.strip() for w in p.read_text(encoding="utf-8").splitlines()]
    return sorted({w for w in words if w and not w.startswith("#")}, key=lambda w: (-len(w), w))


def redact(text, words):
    for w in words:
        text = text.replace(w, "(이름 가림)")
    return text


def parse_ritual_txt(text):
    """ALEPH 「텍스트 파일로 담기」 형식: ## 날짜 / [아침] / [마무리] / - 칸: 값"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    days = []
    for m in re.finditer(r"^## (\d{4}-\d{2}-\d{2})\n(.*?)(?=^## |\Z)", text, re.S | re.M):
        d, body = m.group(1), m.group(2)
        section, items = "", []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif line.startswith("- ") and ":" in line:
                k, v = line[2:].split(":", 1)
                items.append({"section": section, "field": k.strip(), "text": v.strip()})
        days.append({"date": d, "items": items})
    return days


def parse_ritual_json(obj):
    """「JSON으로 담기」 형식. 날짜 키와 칸 이름이 조금 달라도 읽을 수 있게 느슨하게 받는다."""
    records = obj.get("records") or obj.get("days") or obj.get("entries") if isinstance(obj, dict) else obj
    if not isinstance(records, list):
        fail("ritual.json 에서 기록 목록(records/days/entries)을 찾지 못했습니다.")
    days = []
    for r in records:
        d = str(r.get("date") or r.get("날짜") or "")[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}$", d):
            continue
        items = []
        for section_key, section in (("morning", "아침"), ("evening", "마무리"), ("아침", "아침"), ("마무리", "마무리")):
            sec = r.get(section_key)
            if isinstance(sec, dict):
                for k, v in sec.items():
                    if isinstance(v, (str, int, float)):
                        items.append({"section": section, "field": str(k), "text": str(v).strip()})
                    elif isinstance(v, list):
                        for x in v:
                            items.append({"section": section, "field": str(k), "text": str(x).strip()})
        days.append({"date": d, "items": items})
    return days


def load_ritual(words):
    j, t = IN / "ritual.json", IN / "ritual.txt"
    if j.exists():
        days = parse_ritual_json(json.loads(j.read_text(encoding="utf-8")))
        src = "ritual.json"
    elif t.exists():
        days = parse_ritual_txt(t.read_text(encoding="utf-8"))
        src = "ritual.txt"
    else:
        fail("input/ritual.txt 또는 input/ritual.json 이 필요합니다.")
    if not days:
        fail(f"{src} 에서 날짜 기록을 하나도 읽지 못했습니다.")
    for d in days:
        for it in d["items"]:
            it["text"] = redact(it["text"], words)
    days.sort(key=lambda d: d["date"])
    return days, src


def load_attendance():
    p = IN / "attendance.json"
    if not p.exists():
        fail("input/attendance.json 이 필요합니다 (「내 출석 기록」 숫자).")
    return json.loads(p.read_text(encoding="utf-8"))


def load_tasks():
    p = IN / "tasks.csv"
    if not p.exists():
        fail("input/tasks.csv 가 필요합니다 (「내 제출 현황」 과제 목록).")
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (int(re.sub(r"\D", "", r["번호"]) or 0), r["번호"]))
    return rows


def load_polish():
    """input/approved-text.json: {후보 id: 사이트에 실을 문장}. 맞춤법·띄어쓰기만 다듬을 때 쓴다."""
    p = IN / "approved-text.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_approved():
    p = IN / "approved.txt"
    if not p.exists():
        return []
    ids = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            ids.append(line)
    return ids


# ---------- 계산 ----------
def field_value(day, name):
    for it in day["items"]:
        if it["field"] == name:
            return it["text"]
    return None


def numbers(days, att, tasks, src):
    sections = {d["date"]: {it["section"] for it in d["items"]} for d in days}
    morning = sum(1 for s in sections.values() if "아침" in s)
    closing = sum(1 for s in sections.values() if "마무리" in s)
    pairs = sum(1 for s in sections.values() if {"아침", "마무리"} <= s)
    weeks = sorted({date.fromisoformat(d["date"]).isocalendar()[:2] for d in days})
    practice = [field_value(d, "강점 행동") or "" for d in days]
    done = sum(1 for p in practice if p == "실천했다")
    partial = sum(1 for p in practice if p.startswith("일부"))
    breath = 0.0
    for d in days:
        v = field_value(d, "호흡")
        m = re.match(r"([\d.]+)", v or "")
        if m:
            breath += float(m.group(1))
    peer = sum(1 for d in days for it in d["items"] if PEER_FIELD.fullmatch(it["field"]))
    approved_tasks = [t for t in tasks if t.get("상태", "").strip() == "승인"]
    first, last = days[0]["date"], days[-1]["date"]

    rit = f"리추얼 기록 ({src}, {first}~{last})"
    attsrc = f"내 출석 기록 ({att['기준일']} 기준)"
    tsrc = f"내 제출 현황 ({att['기준일']} 기준)"
    # 면접관이 먼저 볼 숫자(완주·출석)부터, 그다음 리추얼 기록
    cards = [
        {"key": "tasks_approved", "label": "핵심 과제 마스터 승인", "value": len(approved_tasks), "unit": f"/ {len(tasks)}개", "source": tsrc},
        {"key": "attendance_rate", "label": "출석률", "value": att["출석률"], "unit": "%", "source": attsrc},
        {"key": "attended", "label": "출석", "value": att["출석"], "unit": f"/ 재적일 {att['재적일']}일 (결석 {att['결석']})", "source": attsrc},
        {"key": "ritual_pairs", "label": "아침·마무리를 모두 남긴 날", "value": pairs, "unit": f"/ {len(days)}일", "source": rit},
        {"key": "ritual_weeks", "label": "기록이 있는 주", "value": len(weeks), "unit": "주 연속" if is_consecutive(weeks) else "주", "source": rit},
        {"key": "strength_done", "label": "강점 행동 '실천했다'", "value": done, "unit": f"일 (일부 실천 {partial}일)", "source": rit},
        {"key": "peer_words", "label": "동료가 말해 준 내 장점", "value": peer, "unit": "개", "source": rit},
        {"key": "ritual_days", "label": "리추얼을 남긴 날", "value": len(days), "unit": "일", "source": rit},
        {"key": "breath", "label": "아침 호흡 누적", "value": round(breath, 1), "unit": "분", "source": rit},
    ]
    meta = {"기준일": last, "첫 기록": first, "아침": morning, "마무리": closing}
    return cards, meta


def is_consecutive(weeks):
    if not weeks:
        return False
    ords = [date.fromisocalendar(y, w, 1).toordinal() // 7 for y, w in weeks]
    return ords == list(range(ords[0], ords[0] + len(ords)))


def split_sentences(text):
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)", text)
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def candidates(days):
    pool = {a: [] for a in ABILITIES}
    for d in days:
        for it in d["items"]:
            if it["field"] not in OWN_FIELDS:
                continue
            for s in split_sentences(it["text"]):
                if "(이름 가림)" in s and len(s.replace("(이름 가림)", "")) < 12:
                    continue
                # 한 문장은 키워드가 가장 많이 걸린 능력 하나에만 들어간다 (같으면 위 순서대로)
                scored = [(len(h), -i, ab, h) for i, (ab, h) in
                          enumerate((ab, sorted({k for k in kws if k in s})) for ab, kws in ABILITIES.items())]
                n, _, ab, hits = max(scored)
                if n:
                    cid = hashlib.sha1(f"{d['date']}|{it['section']}|{it['field']}|{s}".encode()).hexdigest()[:8]
                    pool[ab].append({
                        "id": cid,
                        "ability": ab,
                        "date": d["date"],
                        "basis": f"리추얼 기록 {d['date']} [{it['section']}] {it['field']}",
                        "keywords": hits,
                        "score": len(hits),
                        "text": s,
                    })
    out = {}
    for ab, items in pool.items():
        # 점수 높은 순 → 날짜 → id. 같은 날 같은 문장이 겹치지 않게 한다.
        items.sort(key=lambda c: (-c["score"], c["date"], c["id"]))
        seen, picked = set(), []
        for c in items:
            if c["date"] in seen:
                continue
            seen.add(c["date"])
            picked.append(c)
            if len(picked) == PER_ABILITY:
                break
        picked.sort(key=lambda c: (c["date"], c["id"]))
        out[ab] = picked
    return out


def peer_feedback(days):
    rows = []
    for d in days:
        for it in d["items"]:
            if PEER_FIELD.fullmatch(it["field"]) and len(it["text"]) >= 10:
                rows.append({"date": d["date"], "basis": f"리추얼 기록 {d['date']} [{it['section']}] {it['field']}", "text": it["text"]})
    return rows


# ---------- 쓰기 ----------
def dump(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main():
    words = load_redact()
    days, src = load_ritual(words)
    att = load_attendance()
    tasks = load_tasks()
    approved_ids = load_approved()
    polish = load_polish()

    cards, meta = numbers(days, att, tasks, src)
    cands = candidates(days)
    all_c = {c["id"]: c for v in cands.values() for c in v}
    approved = [all_c[i] for i in approved_ids if i in all_c]
    missing = [i for i in approved_ids if i not in all_c]
    peers = peer_feedback(days)

    numbers_json = json.dumps({"meta": meta, "cards": cards}, ensure_ascii=False, indent=2, sort_keys=True)
    cands_json = json.dumps(cands, ensure_ascii=False, indent=2, sort_keys=True)

    md = [f"# 문단 후보 (기준일 {meta['기준일']})", "",
          "사이트에 넣고 싶은 후보의 id를 input/approved.txt 에 한 줄씩 적고 다시 실행하세요.", ""]
    for ab, items in cands.items():
        md.append(f"## {ab}")
        md.append("")
        for c in items:
            mark = "✅ 승인됨" if c["id"] in approved_ids else "후보"
            md.append(f"- `{c['id']}` {c['date']} · {mark}")
            md.append(f"  - 문장: {c['text']}")
            md.append(f"  - 근거: {c['basis']} (키워드: {', '.join(c['keywords'])})")
        md.append("")
    md.append("## 숫자 칸")
    md.append("")
    for c in cards:
        md.append(f"- {c['label']}: {c['value']} {c['unit']} — 출처: {c['source']}")
    md.append("")
    if missing:
        md.append("## 경고: 후보 목록에 없는 승인 id")
        md += [f"- {i}" for i in missing]
        md.append("")

    site = {"meta": meta, "cards": cards,
            "approved": [dict({k: c[k] for k in ("id", "ability", "date", "basis")}, text=polish.get(c["id"], c["text"])) for c in approved]}
    site_js = "// renew.py 가 만든 파일입니다. 직접 고치지 말고 장치를 다시 돌리세요.\nwindow.SITE_DATA = " + \
        json.dumps(site, ensure_ascii=False, indent=2, sort_keys=True) + ";\n"

    files = {
        "numbers.json": numbers_json + "\n",
        "candidates.json": cands_json + "\n",
        "candidates.md": "\n".join(md),
        "site-data.js": site_js,
        "peer-feedback.json": json.dumps(peers, ensure_ascii=False, indent=2) + "\n",
    }
    for name, text in files.items():
        dump(OUT / name, text)
    sums = "".join(f"{hashlib.sha256((OUT / n).read_bytes()).hexdigest()}  {n}\n" for n in sorted(files))
    dump(OUT / "SHA256SUMS.txt", sums)
    if SITE_DATA.parent.exists():
        dump(SITE_DATA, site_js)

    total = hashlib.sha256(sums.encode()).hexdigest()[:16]
    print(f"기록 {len(days)}일 ({meta['첫 기록']}~{meta['기준일']}), 후보 {sum(len(v) for v in cands.values())}개, 승인 {len(approved)}개")
    if missing:
        print(f"경고: 후보에 없는 승인 id {len(missing)}개 → output/candidates.md 맨 아래 참고")
    print(f"결과 지문: {total}  (두 번 돌려 이 값이 같으면 같은 결과입니다)")


if __name__ == "__main__":
    main()

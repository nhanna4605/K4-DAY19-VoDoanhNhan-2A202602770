"""Knowledge Graph (Neo4j) + GraphRAG over two drug-topic knowledge bases.

Contract (fixed — bench_kg.py and the tests rely on it):
    link_entity(name, known)                       -> one of `known` or None          (TODO KG-1)
    build_graph(graph, law_docs, news_docs, llm_fn)   load both KBs into Neo4j      (TODO KG-2)
        every node created from ONE document carries the property `doc_id`
    Neo4jGraph.context(question, doc_ids)         -> list[str] facts               (TODO KG-3)
    GraphRAGAgent.answer(question, top_k)         -> str                           (TODO KG-4)

Everything else in this file is a HINT: one possible ontology (below). Use it as is, change it,
or design your own — your own ontology + report/ONTOLOGY.md earns the bonus (see SUBMISSION.md).

Suggested ontology (Crime is the bridge between the law KB and the news KB):

    (:Article {id, title, law, doc_id})-[:DEFINES]->(:Crime {name})
    (:Article)-[:HAS_CLAUSE]->(:Clause {id, number, penalty, text})-[:MENTIONS]->(:Substance {name})
    (:Case {name, summary, date, doc_id})-[:CHARGED_WITH]->(:Crime)
    (:Case)-[:INVOLVES {amount}]->(:Substance)
    (:Case)-[:LOCATED_IN]->(:Location {name})
    (:Person {name, aliases})-[:INVOLVED_IN {role, sentence, charge}]->(:Case)
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .models import Document
from .store import EmbeddingStore

# Canonical substance names: the ones BLHS Chương XX lists, plus common ones in Vietnamese news.
SUBSTANCES = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11", "Ketamine",
              "cần sa", "thuốc phiện", "côca"]
CLAUSE_START = re.compile(r"^(\d+)\.\s", re.MULTILINE)
FOOTNOTE = re.compile(r"\[\d+\]")

def load_markdown_docs(folder: str | Path) -> list[Document]:
    """Read crawler output (.md with a flat `key: "value"` front matter) into Documents."""
    docs = []
    for path in sorted(Path(folder).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        metadata = {k: json.loads(v) for k, v in re.findall(r'^(\w+): (".*")$', front, re.MULTILINE)}
        docs.append(Document(id=metadata.get("doc_id", path.stem), content=body.strip(), metadata=metadata))
    return docs

def normalize_crime(name: str) -> str:
    """'Tội Mua bán trái phép chất ma túy' -> 'mua bán trái phép chất ma túy'."""
    name = re.sub(r"\s+", " ", name.strip().strip("\"'“”").lower())
    return name.removeprefix("tội ").strip()

def link_entity(name: str, known: list[str], normalize: Callable[[str], str] = normalize_crime) -> str | None:
    """Map a free-text mention (e.g. a charge written by a journalist) onto one canonical name in `known`."""
    target = normalize(name or "")
    if not target:
        return None
    by_normalized = {normalize(k): k for k in known if normalize(k)}   # chuẩn hóa cả hai phía, nhớ tên gốc
    if target in by_normalized:                                         # khớp chính xác trước
        return by_normalized[target]
    close = difflib.get_close_matches(target, list(by_normalized), n=1, cutoff=0.8)
    return by_normalized[close[0]] if close else None

def find_substances(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in SUBSTANCES if name.lower() in lowered]

# ----------------------------------------------------------------------------------------------
# HINT — suggested ontology: extraction helpers
# ----------------------------------------------------------------------------------------------

def parse_law_article(doc: Document) -> dict[str, Any]:
    """Deterministic (regex) extraction for one 'Điều' — law text is regular enough to skip the LLM."""
    article_id = doc.metadata["article"]                       # "Điều 251 BLHS"
    title = doc.metadata["title"].split(". ", 1)[-1]           # "Tội mua bán trái phép chất ma túy"
    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = text.splitlines()[0]
        penalty = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        clauses.append({
            "id": f"{article_id} khoản {start.group(1)}",
            "number": int(start.group(1)),
            "penalty": penalty.group(1).rstrip(".") if penalty else "",
            "text": text,
            "substances": find_substances(text),
        })
    return {
        "id": article_id,
        "law": doc.metadata.get("law", ""),
        "title": title,
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }

NEWS_EXTRACTION_PROMPT = """Bạn trích xuất knowledge graph từ một bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài. Trả về JSON đúng dạng:
{{"cases": [{{
  "name": "tên ngắn của vụ việc, ví dụ: Vụ mua bán 36kg ma túy tại TP.HCM",
  "summary": "1-2 câu tóm tắt",
  "date": "ngày xảy ra/xét xử nếu có, dạng YYYY-MM-DD hoặc chuỗi rỗng",
  "location": "tỉnh/thành phố, chuỗi rỗng nếu không rõ",
  "charges": ["tội danh, BẮT BUỘC chọn đúng nguyên văn từ DANH SÁCH TỘI DANH"],
  "substances": [{{"name": "tên chất, dùng tên chuẩn trong DANH SÁCH CHẤT nếu khớp", "amount": "khối lượng nếu có"}}],
  "people": [{{"name": "họ tên", "aliases": ["biệt danh"], "role": "bị cáo|bị can|nghi phạm|người liên quan|cán bộ",
               "charge": "tội danh của người này (từ DANH SÁCH TỘI DANH) hoặc chuỗi rỗng",
               "sentence": "mức án nếu có, ví dụ: tử hình, 8 năm tù"}}]
}}]}}
Bài không nói về vụ việc cụ thể (tuyên truyền, hội nghị...) thì trả về {{"cases": []}}.

DANH SÁCH TỘI DANH: {crimes}
DANH SÁCH CHẤT: {substances}

Tiêu đề: {title}
Nội dung:
{content}"""

def extract_news_cases(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    """LLM extraction for one news article; charges are re-linked to law-KB crimes in code."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        cases = json.loads(llm_fn(prompt)).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        for person in case.get("people", []):
            person["charge"] = link_entity(person.get("charge") or "", known_crimes) or ""
    return cases

# ----------------------------------------------------------------------------------------------
# ONTOLOGY v2 (tự thiết kế) — khác ontology gợi ý có chủ đích, xem report/ONTOLOGY.md mục 7.
#   D1 Substance chuẩn hóa (tên đồng nghĩa/hoa thường -> một node)        <- lỗi E3 đo được ở ontology gợi ý
#   D2 Clause.severity: mức phạt có cấu trúc (năm, chung thân, tử hình)   <- lỗi E2 (hỏi "tối đa" chỉ có khoản 1)
#   D3 (Clause)-[:THRESHOLD {min_g,max_g}]->(Substance) + INVOLVES.grams  <- chọn đúng khoản theo khối lượng
#   D4 giữ nguyên tội danh gốc khi không nối được (charges_raw)           <- lỗi E6 (mất "chống người thi hành công vụ")
# Đặt KG_ONTOLOGY=hint để dựng lại ontology gợi ý (dùng để tái tạo ket_qua_benchmark_kg.hint.txt).
# ----------------------------------------------------------------------------------------------

GENERIC_SUBSTANCE = "ma túy (không rõ loại)"
GENERIC_TERMS = {"ma túy", "chất ma túy", "ma túy tổng hợp", "các chất ma túy", "chất ma túy tổng hợp"}
SUBSTANCE_ALIASES = {"thuốc lắc": "MDMA", "ecstasy": "MDMA", "ma túy đá": "Methamphetamine",
                     "heroin": "Heroine", "cocain": "Cocaine"}

def canonical_substance(name: str) -> str:
    """'ketamine' / 'Ketamine' -> 'Ketamine'; 'thuốc lắc' -> 'MDMA'; 'chất ma túy' -> nút chung; chất lạ giữ 1 nút."""
    key = " ".join((name or "").lower().replace("tuý", "túy").split())
    if not key:
        return ""
    if key in GENERIC_TERMS:
        return GENERIC_SUBSTANCE
    canon = {s.lower(): s for s in SUBSTANCES}
    if key in canon:
        return canon[key]
    if key in SUBSTANCE_ALIASES:
        return SUBSTANCE_ALIASES[key]
    close = difflib.get_close_matches(key, list(canon) + list(SUBSTANCE_ALIASES), n=1, cutoff=0.85)
    if close:
        return canon.get(close[0]) or SUBSTANCE_ALIASES[close[0]]
    return key                                           # chất ngoài danh sách (vd. etomidate): một nút theo tên thường

_UNIT_G = {"miligam": 0.001, "mg": 0.001, "gam": 1.0, "g": 1.0, "kilôgam": 1000.0, "kilogram": 1000.0, "kg": 1000.0}
AMOUNT = re.compile(r"([\d.,]*\d)\s*(miligam|kilôgam|kilogram|gam|kg|mg|g)\b", re.IGNORECASE)
# Hai kiểu trong luật: "từ 0,1 gam đến dưới 05 gam" (khoảng) và "100 gam trở lên" (không chặn trên).
RANGE = re.compile(r"([\d.,]*\d)\s*(miligam|kilôgam|gam)"
                   r"\s+(?:đến\s+dưới\s+([\d.,]*\d)\s*(miligam|kilôgam|gam)|trở\s+lên)")
POINT = re.compile(r"^([a-zđ])\)\s+(.*)$")

def _to_number(text: str) -> float:
    text = text.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", text):          # '1.000' = một nghìn
        return float(text.replace(".", ""))
    return float(text.replace(",", "."))                   # '0,1' hoặc '9.6' = số thập phân

def amount_to_grams(text: str) -> float | None:
    """'9.6kg' -> 9600.0 ; 'hơn 406g' -> 406.0 ; không có số + đơn vị khối lượng -> None."""
    match = AMOUNT.search(text or "")
    if not match:
        return None
    try:
        return _to_number(match.group(1)) * _UNIT_G[match.group(2).lower()]
    except (ValueError, KeyError):
        return None

def parse_thresholds(clause_text: str) -> list[dict]:
    """Từng điểm 'a) ...' của một khoản: chất nào, khối lượng từ min_g đến dưới max_g (None = trở lên)."""
    out = []
    for line in clause_text.splitlines():
        point = POINT.match(line.strip())
        if not point:
            continue
        body = point.group(2)
        substances, span = find_substances(body), RANGE.search(body)
        if not substances or not span:
            continue
        low = _to_number(span.group(1)) * _UNIT_G[span.group(2)]
        high = _to_number(span.group(3)) * _UNIT_G[span.group(4)] if span.group(3) else None
        out += [{"point": point.group(1), "substance": s, "min_g": low, "max_g": high} for s in substances]
    return out

def penalty_severity(penalty: str) -> int:
    """Xếp hạng khung phạt để so sánh: tử hình 100 > chung thân 90 > số năm tù tối đa > 0 (không phải tù)."""
    text = penalty.lower()
    if "tử hình" in text:
        return 100
    if "chung thân" in text:
        return 90
    if "phạt tù" in text or text.startswith("tù"):
        years = [int(n) for n in re.findall(r"(\d+)\s*năm", text)]
        return max(years) if years else 1
    return 0

def parse_law_article_v2(doc: Document) -> dict[str, Any]:
    article = parse_law_article(doc)
    for clause in article["clauses"]:
        clause["severity"] = penalty_severity(clause["penalty"])
        clause["thresholds"] = parse_thresholds(clause["text"])
    return article

def extract_news_cases_v2(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    """Như extract_news_cases, nhưng chuẩn hóa chất, đổi lượng ra gam, và GIỮ tội danh gốc khi không nối được luật."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        cases = json.loads(llm_fn(prompt)).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        raw = [c for c in case.get("charges", []) if c]
        case["charges_raw"] = raw
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in raw) if c})
        merged: dict[str, dict] = {}
        for item in case.get("substances", []):
            name = canonical_substance(item.get("name", ""))
            if not name:
                continue
            amount = item.get("amount") or ""
            grams = amount_to_grams(amount)
            current = merged.setdefault(name, {"name": name, "amount": amount, "grams": grams})
            if grams is not None and (current["grams"] is None or grams > current["grams"]):
                current.update(amount=amount, grams=grams)
        case["substances"] = list(merged.values())
        for person in case.get("people", []):
            raw_charge = person.get("charge") or ""
            person["charge_raw"] = raw_charge
            person["charge"] = link_entity(raw_charge, known_crimes) or ""
    return cases

# ----------------------------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------------------------

class Neo4jGraph:
    """Thin wrapper over the official neo4j driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params)
        return [record.data() for record in records]

    def reset(self) -> None:
        """Delete every node, relationship and constraint (bench_kg.py calls this before build_graph)."""
        self.run("MATCH (n) DETACH DELETE n")
        for row in self.run("SHOW CONSTRAINTS YIELD name RETURN name"):
            self.run(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")

    def stats(self) -> dict[str, int]:
        nodes = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return {"nodes": nodes, "relationships": rels}

    def seed_facts(self, question: str, doc_ids: list[str], skip_labels: tuple[str, ...] = (),
                   limit: int = 60) -> tuple[list[str], list[str]]:
        """Ontology-independent first step: seed nodes + their 1-hop edges as text facts.

        Seeds = nodes whose `doc_id` is in doc_ids, or whose `name`/`aliases` appear in the question.
        Returns (seed elementIds, facts). Nodes with a label in skip_labels are left out of the facts.
        """
        seeds = self.run(
            """
            MATCH (n)
            WHERE n.doc_id IN $doc_ids
               OR (n.name IS :: STRING AND size(n.name) >= 3 AND toLower($q) CONTAINS toLower(n.name))
               OR any(a IN coalesce(n.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
            RETURN elementId(n) AS id
            """,
            q=question, doc_ids=doc_ids,
        )
        seed_ids = [row["id"] for row in seeds]
        edges = self.run(
            """
            MATCH (s)-[r]-(m)
            WHERE elementId(s) IN $ids
              AND none(l IN labels(s) + labels(m) WHERE l IN $skip)
            WITH DISTINCT r LIMIT $limit
            WITH startNode(r) AS a, r, endNode(r) AS b
            RETURN labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a_name, type(r) AS rel,
                   properties(r) AS props, labels(b)[0] AS b_label, coalesce(b.name, b.id) AS b_name
            """,
            ids=seed_ids, skip=list(skip_labels), limit=limit,
        )
        facts = []
        for e in edges:
            props = ", ".join(f"{k}: {v}" for k, v in e["props"].items() if v)
            facts.append(f"({e['a_label']}: {e['a_name']}) -[{e['rel']}{' {' + props + '}' if props else ''}]-> "
                         f"({e['b_label']}: {e['b_name']})")
        return seed_ids, facts

    # ---------------------------------------------------------------- HINT — suggested ontology: writes

    def suggested_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            """,
            **article,
        )

    def add_news_case(self, case: dict, doc: Document) -> None:
        self.run(
            """
            MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, [])
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence)
            """,
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    # ---------------------------------------------------------------- ontology v2: writes

    def add_law_article_v2(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text,
                  cl.severity = clause.severity, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            FOREACH (t IN clause.thresholds | MERGE (sub:Substance {name: t.substance})
                MERGE (cl)-[r:THRESHOLD {point: t.point}]->(sub) SET r.min_g = t.min_g, r.max_g = t.max_g)
            """,
            **article,
        )

    def add_news_case_v2(self, case: dict, doc: Document) -> None:
        self.run(
            """
            MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title,
                  k.charges_raw = $charges_raw
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount, r.grams = s.grams)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, [])
                MERGE (person)-[r:INVOLVED_IN]->(k)
                SET r.role = p.role, r.charge = p.charge, r.charge_raw = p.charge_raw, r.sentence = p.sentence)
            """,
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), charges_raw=case.get("charges_raw", []),
            people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    # ---------------------------------------------------------------- KG-3

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Graph facts for a question: seeds + 1 hop, then the legal basis of every case reached."""
        # 1. Seed + 1 hop (ontology-independent). Clause nodes are skipped here: step 3 adds their full text,
        #    so repeating "Điều X khoản N" edges would only cost tokens.
        seed_ids, seed = self.seed_facts(question, doc_ids, skip_labels=("Clause",))

        # 2. Cases that are a seed or adjacent to one (e.g. the Person named in the question) -> one summary each.
        cases = self.run(
            """
            MATCH (k:Case)
            WHERE elementId(k) IN $ids OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
            RETURN DISTINCT elementId(k) AS id, k.name AS name, k.summary AS summary
            """,
            ids=seed_ids,
        )
        case_ids = [c["id"] for c in cases]
        facts = [f"Vụ việc '{c['name']}': {c['summary']}" for c in cases if c.get("summary")]

        # 3. News -> law through the bridge Crime. Clause 1 (base penalty) always; then, per Substance the case
        #    INVOLVES: if the amount is known and the ontology has THRESHOLD edges (v2), only the clause whose
        #    range contains that amount (e.g. 9 600 g MDMA -> khoản 4); otherwise every clause that MENTIONS the
        #    substance (ontology gợi ý, or amount unknown) so the answer is never missing the decisive clause.
        clause_cypher = """
            MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE elementId(k) = $case_id AND {where}
            RETURN DISTINCT a.id AS article, a.title AS title, cl.number AS number, cl.text AS text
            ORDER BY article, number
        """
        clause_rows = self.run(
            """
            MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE elementId(k) IN $case_ids AND cl.number = 1
            RETURN DISTINCT a.id AS article, a.title AS title, cl.number AS number, cl.text AS text
            ORDER BY article, number
            """,
            case_ids=case_ids,
        )
        for item in self.run(
            """
            MATCH (k:Case)-[i:INVOLVES]->(s:Substance) WHERE elementId(k) IN $case_ids
            RETURN elementId(k) AS case_id, s.name AS substance, i.grams AS grams
            """,
            case_ids=case_ids,
        ):
            by_quantity = []
            if item["grams"] is not None:
                by_quantity = self.run(
                    clause_cypher.format(where="EXISTS { (cl)-[t:THRESHOLD]->(:Substance {name: $sub}) "
                                               "WHERE t.min_g <= $grams AND (t.max_g IS NULL OR $grams < t.max_g) }"),
                    case_id=item["case_id"], sub=item["substance"], grams=item["grams"])
            clause_rows += by_quantity or self.run(
                clause_cypher.format(where="EXISTS { (cl)-[:MENTIONS]->(:Substance {name: $sub}) }"),
                case_id=item["case_id"], sub=item["substance"])

        # 4. Law named directly in the question ("Điều 251"): clause 1 + clauses mentioning a substance of the question.
        for number in dict.fromkeys(re.findall(r"[Đđ]iều (\d+)", question)):
            clause_rows += self.run(
                """
                MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                WHERE a.id STARTS WITH $prefix
                  AND (cl.number = 1 OR EXISTS { (cl)-[:MENTIONS]->(s:Substance) WHERE s.name IN $subs })
                RETURN DISTINCT a.id AS article, a.title AS title, cl.number AS number, cl.text AS text
                ORDER BY article, number
                """,
                prefix=f"Điều {number} ", subs=find_substances(question),
            )

        # 4b. "tối đa / cao nhất": the clause with the highest penalty of every article the cases are charged under
        #     (needs Clause.severity of ontology v2; on the suggested ontology this simply returns nothing).
        if re.search(r"tối đa|cao nhất|nặng nhất|lớn nhất", question.lower()):
            clause_rows += self.run(
                """
                MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                WHERE elementId(k) IN $case_ids AND cl.severity IS NOT NULL
                WITH a, cl ORDER BY cl.severity DESC
                WITH a, collect(cl)[0] AS cl
                RETURN a.id AS article, a.title AS title, cl.number AS number, cl.text AS text
                ORDER BY article
                """,
                case_ids=case_ids,
            )

        # 5. Aggregation ("Những vụ nào liên quan đến MDMA?"): every Case that INVOLVES a substance of the question.
        substances = find_substances(question)
        if substances:
            for row in self.run(
                """
                MATCH (k:Case)-[r:INVOLVES]->(s:Substance) WHERE s.name IN $subs
                RETURN k.name AS name, k.summary AS summary, s.name AS substance, r.amount AS amount
                ORDER BY k.name
                """,
                subs=substances,
            ):
                amount = f" ({row['amount']})" if row.get("amount") else ""
                facts.append(f"Vụ việc '{row['name']}' liên quan {row['substance']}{amount}: {row['summary']}")

        facts += [f"[{r['article']} - {r['title']}] khoản {r['number']}: {r['text']}" for r in clause_rows]
        facts += seed
        return list(dict.fromkeys(facts))[:max_facts]   # bỏ trùng, giữ thứ tự: dữ kiện quan trọng nhất đứng trước

# ---------------------------------------------------------------------------------------------- KG-2

def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    """Load both KBs into an empty graph. llm_fn(prompt, json_mode=False) -> str (metered OpenAI chat)."""
    hint = os.getenv("KG_ONTOLOGY", "v2").lower() == "hint"      # hint = ontology gợi ý, để tái tạo số liệu so sánh
    parse_article = parse_law_article if hint else parse_law_article_v2
    extract_cases = extract_news_cases if hint else extract_news_cases_v2
    add_article = graph.add_law_article if hint else graph.add_law_article_v2
    add_case = graph.add_news_case if hint else graph.add_news_case_v2

    graph.suggested_constraints()                                 # CONSTRAINT ... IS UNIQUE cho khóa MERGE
    articles = [parse_article(doc) for doc in law_docs]           # KB luật: regex, tất định, không tốn LLM
    for article in articles:
        add_article(article)
    crimes = [a["crime"] for a in articles if a["crime"]]         # tên tội chuẩn = node cầu nối Crime
    for doc in news_docs:                                         # KB tin tức: LLM trích JSON, rồi link_entity
        for case in extract_cases(doc, lambda prompt: llm_fn(prompt, json_mode=True), crimes):
            add_case(case, doc)

# ---------------------------------------------------------------------------------------------- KG-4

GRAPH_PROMPT = """Trả lời câu hỏi chỉ dựa trên ngữ cảnh (đoạn văn bản và dữ kiện từ knowledge graph).
Nêu rõ số Điều luật khi có. Nếu ngữ cảnh không đủ, nói không đủ thông tin.

Dữ kiện knowledge graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""

class GraphRAGAgent:
    """Hybrid GraphRAG: the same vector top-k as flat RAG, plus facts expanded from the graph."""

    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str]) -> None:
        self.store = store
        self.graph = graph
        self.llm_fn = llm_fn

    def answer(self, question: str, top_k: int = 3) -> str:
        chunks = self.store.search(question, top_k=top_k)                    # giống hệt Flat RAG
        doc_ids = list(dict.fromkeys(                                         # không trùng, giữ thứ tự
            c["metadata"]["doc_id"] for c in chunks if c.get("metadata", {}).get("doc_id")))
        facts = self.graph.context(question, doc_ids)
        prompt = GRAPH_PROMPT.format(
            facts="\n".join(f"- {fact}" for fact in facts),
            chunks="\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1)),
            question=question,
        )
        return self.llm_fn(prompt)

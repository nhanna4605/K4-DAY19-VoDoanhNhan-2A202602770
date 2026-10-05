# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Võ Doanh Nhân  **MSSV:** 2A202602770  **Ngày:** 05/10/2026

> Kỳ vọng và thang điểm: `SUBMISSION.md`. Mọi số liệu dưới đây khớp với `ket_qua_benchmark_kg.txt` (ontology gợi ý, chạy `python bench_kg.py --judge` từ code cuối cùng của repo). Bản thiết kế ontology nộp riêng ở `report/ONTOLOGY.md`. Truy vấn Cypher và kết quả nguyên văn dùng làm bằng chứng lưu ở `report/evidence/`.

**Lưu ý về môi trường đo.** Chat/embedding gọi qua một cổng API trung gian tương thích OpenAI (`gpt-4o-mini`, `text-embedding-3-small`). Cột USD do `src/llm.py` tính từ số token nhân bảng giá công bố, không phải hóa đơn thật. Cột giây bị ảnh hưởng mạnh bởi độ trễ của cổng (cùng một bước embed 176 chunk của Flat RAG mất từ 657 s đến 1 507 s ở các lần chạy khác nhau của tôi), nên **không nên đọc cột giây như khác biệt giữa hai pipeline**. Trích xuất bằng LLM không tất định, nên số node/cạnh và một số câu trả lời có thể lệch nhẹ giữa các lần chạy.

## 1. Chi phí (10 điểm)

Hai bảng dán nguyên từ `ket_qua_benchmark_kg.txt`:

```
Chat model: openai:gpt-4o-mini | Embedding: openai:text-embedding-3-small | top_k=3 | chunk_size=800 | chunks=176 | KG: 203 nodes / 379 rels

== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176     56072        0   0.00112    825.9
graph       196     91958     4505   0.00921    927.6

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.43   1.00      694       47   0.00013     7.15
graph       0.94   1.67     3735       84   0.00060     7.97
```

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | --- | --- | --- |
| Indexing USD | 0,00112 | 0,00921 | ×8,22 |
| Indexing giây | 825,9 | 927,6 | ×1,12 |
| Mỗi câu: USD | 0,00013 | 0,00060 | ×4,62 |
| Mỗi câu: giây | 7,15 | 7,97 | ×1,11 |
| Mỗi câu: in_tok | 694 | 3 735 | ×5,38 |

**Chi phí tăng thêm đến từ đâu?**
- *Dựng chỉ mục:* cả hai pipeline đều embed 176 chunk (56 072 token). GraphRAG thêm đúng **20 lần gọi LLM** (mỗi bài báo một lần để trích JSON), tức +35 886 token vào và +4 505 token ra, +$0,00809. Phần luật dựng bằng regex nên **0 lần gọi LLM**.
- *Mỗi câu hỏi:* GraphRAG gửi cùng 3 chunk như Flat RAG **cộng** các dữ kiện từ graph (với Q4 và Q5 ngữ cảnh graph dài khoảng 5 700 và 16 900 ký tự), nên token vào gấp 5,38 lần và token ra gấp 1,79 lần (câu trả lời dài hơn).
- *Ước tính hòa vốn:* chi phí thêm cho n câu là `0,00809 + n × 0,00047` USD (0,00047 = 0,00060 − 0,00013). Với 6 câu thêm khoảng $0,0109; với 100 câu, bình quân còn thêm khoảng $0,00055/câu vì chi phí dựng chia đều. Con số tuyệt đối rất nhỏ; điều cần cân nhắc là độ phức tạp vận hành (Neo4j, ontology, trích xuất) hơn là tiền.

## 2. Từng câu hỏi (10 điểm)

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao (1 câu) |
| --- | --- | --- | --- | --- | --- |
| Q1 | single-hop-law | 1,00 / 2 | 1,00 / 2 | Hòa | Định nghĩa "tiền chất" nằm gọn trong một chunk luật nên vector search đủ |
| Q2 | single-hop-news | 1,00 / 2 | 1,00 / 2 | Hòa | Hai bị cáo tử hình nằm trong một bài báo, một chunk là đủ |
| Q3 | cross-kb | 0,00 / 0 | 1,00 / 2 | **Graph** | Tên người chỉ có trong tin, khung phạt chỉ có trong luật; Flat trả "Không đủ thông tin", Graph đi người → vụ → tội → Điều 251 |
| Q4 | cross-kb | 0,00 / 0 | 0,67 / 1 | **Graph** (chưa đúng) | Graph nối được Hoàng Nato tới tội tổ chức sử dụng chất (Điều 255) nhưng vẫn trả "tối đa 7 năm" thay vì chung thân (lỗi E2, mục 3) |
| Q5 | cross-kb-multi-hop | 0,60 / 1 | 1,00 / 2 | **Graph** | Graph đưa đủ các khoản nhắc MDMA của Điều 250 và trả lời đúng khoản 4; Flat gọi nhầm "khoản b)" |
| Q6 | aggregation | 0,00 / 1 | 1,00 / 1 | Graph (recall), hòa (judge) | Graph liệt kê đủ 4 vụ MDMA có trong graph, nhưng recall và judge mâu thuẫn (E4) |

**Quy luật:** câu chỉ cần **một nguồn** (Q1, Q2) thì Flat RAG ngang Graph và rẻ hơn; câu cần **ghép dữ kiện của hai KB** (Q3–Q5) thì Graph thắng rõ vì không một chunk nào chứa cả tên người lẫn khung phạt; câu **gom nhóm** (Q6) Graph liệt kê tốt hơn nhưng điểm judge không phản ánh được.

## 3. Phân tích lỗi (20 điểm)

### Lỗi E3: Trùng thực thể (`Substance`)

- **Hiện tượng:** cùng một chất bị tách thành nhiều node `Substance` khi LLM viết khác hoa/thường: graph có 18 node `Substance`, trong đó 3 cặp trùng.
- **Bằng chứng:** (`report/evidence/evidence_hint.txt`, graph của lần chạy tạo ra `ket_qua_benchmark_kg.txt`)

```cypher
MATCH (s:Substance) RETURN count(s) AS substances;
MATCH (s:Substance) WITH toLower(s.name) AS k, collect(s.name) AS names WHERE size(names) > 1 RETURN k, names;
```

```
{"substances": 18}
{"k": "ketamine", "names": ["Ketamine", "ketamine"]}
{"k": "cần sa", "names": ["Cần sa", "cần sa"]}
{"k": "methamphetamine", "names": ["methamphetamine", "Methamphetamine"]}
```

- **Nguyên nhân:** nằm ở **bước trích xuất và khóa định danh của ontology**. `add_news_case` `MERGE` theo `s.name` do LLM tự viết; prompt có đưa "DANH SÁCH CHẤT" nhưng LLM không luôn tuân thủ, và khác với tội danh (có `link_entity`), chất không qua bước chuẩn hóa nào, nên `MERGE` coi `Ketamine` và `ketamine` là hai khóa khác nhau.
- **Đề xuất sửa:** chuẩn hóa tên chất trước khi `MERGE` (chữ thường rồi đối chiếu danh sách `SUBSTANCES`, hoặc tái dùng `link_entity` với `normalize` thích hợp). Đánh đổi: phải duy trì danh sách chất chuẩn và chất lạ (vd. `etomidate`) vẫn thành một node riêng. Hậu quả nếu không sửa: câu hỏi gom nhóm theo chất (Q6) có thể bỏ sót các vụ nằm ở node `Substance` thứ hai.

### Lỗi E2: Thiếu ngữ cảnh luật cho câu hỏi "tối đa" (Q4)

- **Hiện tượng:** Q4 hỏi mức phạt tù **tối đa** cho hành vi của 'Hoàng Nato'. Graph trả "tối đa 7 năm theo Điều 255" (judge 1), trong khi khung cao nhất của Điều 255 là "20 năm hoặc tù chung thân".
- **Bằng chứng:** câu trả lời trích nguyên văn từ `ket_qua_benchmark_kg.txt`, mục `Q4 graph`: *"Hành vi này có thể bị phạt tù tối đa 7 năm theo Điều 255 Bộ luật Hình sự."* Cypher cho thấy graph **có** khoản chung thân:

```cypher
MATCH (:Article {id:'Điều 255 BLHS'})-[:HAS_CLAUSE]->(c:Clause) RETURN c.number AS khoan, c.penalty AS penalty ORDER BY khoan;
```

```
{'khoan': 1, 'penalty': 'phạt tù từ 02 năm đến 07 năm'}
{'khoan': 4, 'penalty': 'phạt tù 20 năm hoặc tù chung thân'}      (khoản 2, 3, 5 lược bớt; xem evidence_hint.txt)
```

  Nhưng `context(Q4)` chỉ đưa vào **khoản 1** của mỗi Điều: `khoản theo Điều: {'Điều 249': [1], 'Điều 251': [1], 'Điều 255': [1]}` (26 dữ kiện, 5 685 ký tự).
- **Nguyên nhân:** ở bước **Cypher của KG-3**: quy tắc lọc khoản ("khoản 1 + khoản nhắc chất của vụ") bỏ sót khoản nặng nhất khi câu hỏi không nhắc chất nào. Vì vậy câu trả lời chỉ thấy khung cơ bản 02–07 năm. Ngoài ra 'Hoàng Nato' bị nối với nhiều tội khác nhau (4 `Case`, trong đó một vụ nối tới cả 249, 251 và 255), nên "tối đa" còn mơ hồ về Điều nào.
- **Đề xuất sửa:** thêm vào `context()` một quy tắc cho câu hỏi chứa "tối đa/cao nhất": lấy thêm khoản có khung phạt nặng nhất của các Điều mà vụ bị nối tới. Đánh đổi: ngữ cảnh dài hơn. **Lưu ý trung thực:** tôi đã thử đúng ý tưởng này trong một bản thử nghiệm không nộp (thêm thuộc tính xếp hạng mức phạt cho `Clause`); ngữ cảnh khi đó có đủ khoản 4 của Điều 255 nhưng `gpt-4o-mini` vẫn trả "7 năm". Nghĩa là sửa ở tầng truy hồi chưa chắc sửa được câu trả lời cuối; có thể cần chỉnh cả `GRAPH_PROMPT`.

### Lỗi E4: Phép đo sai (recall và judge mâu thuẫn ở Q6)

- **Hiện tượng:** ở Q6, `recall` và `judge` không nhất quán: Flat có `recall=0,00` nhưng `judge=1`; Graph có `recall=1,00` nhưng `judge=1` (cùng điểm judge dù recall khác hẳn).
- **Bằng chứng:** câu trả lời Flat (`ket_qua_benchmark_kg.txt`, `Q6 flat`) nêu ba vụ nhưng bằng tên khác với `must_include`: *"Vụ việc của Đức … Vụ việc của Thành … Vụ việc của Đông …"*. Chuỗi bắt buộc là `Cái Quang Huy`, `Lê Minh Thành`, `Pháp y tâm thần`, nên `keyword_recall` ra 0 dù câu trả lời đúng một phần (judge 1). Chiều ngược lại, câu trả lời `Q6 graph` chứa đủ ba chuỗi (`recall=1,00`) và liệt kê 4 vụ, khớp 4 vụ MDMA có trong graph:

```cypher
MATCH (k:Case)-[i:INVOLVES]->(:Substance {name:'MDMA'}) RETURN k.name AS vu, k.doc_id AS doc, i.amount AS amount ORDER BY vu;
```

```
Vụ góp tiền mua ma túy tại Hà Nội | news-100260918080821054 | 5 viên
Vụ tổ chức sử dụng ma túy tại Sầm Sơn | news-100260930085028036 | 0,686g
Vụ vận chuyển ma túy từ Đức về Việt Nam | news-100260917203001265 | 9.6kg
Vụ án tại Viện Pháp y tâm thần Trung ương | news-100260924105118645 |
(4 vụ)
```

  Đáp án chuẩn chỉ nêu ba vụ (Cái Quang Huy, Lê Minh Thành, Viện Pháp y); câu trả lời của Graph thêm vụ Sầm Sơn mà graph có. Lý do judge cho 1 không được ghi trong file kết quả, nên tôi không khẳng định nguyên nhân.
- **Nguyên nhân:** nằm ở **bước phép đo**: `keyword_recall` khớp chuỗi con nguyên văn, không chịu được biến thể tên ("Thành" so với "Lê Minh Thành", "vụ của Đức" so với "Cái Quang Huy") và không phạt phần thừa; judge so với đáp án chuẩn chỉ có ba vụ.
- **Đề xuất sửa:** đưa `must_include` về dạng có alias (ví dụ `["Lê Minh Thành", "Thành"]`) hoặc chuẩn hóa tên trước khi so; báo cả recall lẫn judge thay vì một điểm duy nhất; kiểm đáp án chuẩn bằng Cypher trước khi dùng. Đánh đổi: tốn thêm công thiết kế bộ đánh giá. (Tôi không sửa `bench_kg.py` hay `benchmark_kg.json` vì `SUBMISSION.md` cấm; đây chỉ là đề xuất.)

### Lỗi E1/E6: Cầu nối "gãy" và mất thông tin tội danh ngoài luật ma túy

- **Hiện tượng:** có đúng 1 vụ không nối được sang luật ("Vụ tông cảnh sát giao thông ở An Giang"), và 9 trong 42 quan hệ `INVOLVED_IN` có `charge` rỗng.
- **Bằng chứng:** (`report/evidence/evidence_hint.txt`)

```cypher
MATCH (k:Case) WHERE NOT (k)-[:CHARGED_WITH]->() RETURN k.name AS vu, k.doc_id AS doc;
MATCH ()-[r:INVOLVED_IN]->() RETURN count(r) AS tong, sum(CASE WHEN r.charge = '' THEN 1 ELSE 0 END) AS charge_rong;
MATCH ()-[r:INVOLVED_IN]->() WITH collect(DISTINCT keys(r)) AS rel_keys RETURN rel_keys;
```

```
{"vu": "Vụ tông cảnh sát giao thông ở An Giang", "doc": "news-100260926112415229"}
{"tong": 42, "charge_rong": 9}
{"rel_keys": [["role", "charge", "sentence"], ["sentence", "role", "charge"]]}
```

  Bài gốc (`data/drug_news/news-100260926112415229.md`) ghi: bị can bị khởi tố "về hành vi chống người thi hành công vụ" (tội này không thuộc KB luật ma túy).
- **Nguyên nhân:** nằm ở **thiết kế ontology và `link_entity`**. Tội danh "chống người thi hành công vụ" không có trong danh sách tội danh của KB luật nên `link_entity` trả `None`; ontology gợi ý chỉ lưu tội danh **đã nối được** (`charge`) và không có thuộc tính nào giữ tên tội gốc, nên thông tin bị mất hoàn toàn. Việc *không nối* vụ này sang luật là hợp lý (nối bừa sẽ sai), còn việc *mất tên tội* là điểm yếu. Các quan hệ `charge` rỗng khác chưa phân loại được từng trường hợp là hợp lý hay lỗi trích xuất, vì graph không lưu tội danh gốc để đối chiếu.
- **Đề xuất sửa:** thêm thuộc tính giữ tội danh gốc (vd. `Case.charges_raw`, `INVOLVED_IN.charge_raw`) để phân biệt "không có tội danh" với "tội danh ngoài phạm vi luật". Đánh đổi: thêm vài thuộc tính chuỗi, đổi `add_news_case` và `extract_news_cases`.

## 4. Kết luận (5 điểm)

**Khi nào nên dùng KG:** khi câu hỏi cần **ghép dữ kiện nằm ở hai nguồn khác nhau** và có một khóa nối rõ ràng. Trên bộ này: Q3, Q4, Q5 (cross-kb) Flat RAG thua hẳn (recall 0,00 / 0,00 / 0,60 so với 1,00 / 0,67 / 1,00 của Graph; judge trung bình toàn bộ 1,00 so với 1,67), vì tên người chỉ có trong tin tức còn khung phạt chỉ có trong luật. KG càng đáng khi một nguồn rất đều (văn bản luật) để dựng bằng regex với **0 lần gọi LLM**, và khi số câu hỏi nhiều: chi phí thêm mỗi câu hội tụ về khoảng $0,0005.

**Khi nào Flat RAG đủ:** câu hỏi một nguồn (Q1, Q2 ngang nhau, recall 1,00, judge 2) hoặc kho nhỏ, ít cần suy luận qua nhiều tài liệu. Flat rẻ hơn khoảng 4,6 lần mỗi câu và 8,2 lần khi dựng chỉ mục, và không cần Neo4j.

**Điều kiện và giới hạn của kết luận này:** chỉ 6 câu và một lần chạy cho bản nộp (hai lần chạy ontology gợi ý trước đó, khác nhau ở chi tiết code, cũng ra recall 0,94 và judge 1,67 cho Graph), trích xuất bằng LLM không tất định, và cột USD/giây đo qua cổng API trung gian. GraphRAG cũng **không** tự sửa được mọi câu: Q4 vẫn sai dù graph đủ dữ kiện, và độ phủ của câu gom nhóm (Q6) phụ thuộc cách LLM tách `Case` và đặt tên chất. Với kho lớn hơn hoặc thay đổi thường xuyên, chi phí duy trì ontology (khóa định danh, chuẩn hóa tên) mới là chi phí thật sự của KG.

## 5. Tự kiểm

Đầu ra `pytest tests/ -q` và `python bench_kg.py --check` (chạy ngày 05/10/2026, trước khi nộp):

`pytest tests/ -q`:

```
................................................                         [100%]
48 passed in 0.08s
```

`python bench_kg.py --check` (7 dòng `[OK]`; graph nhỏ nên số node khác graph đầy đủ ở trên):

```
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[OK] KG-2 build_graph: 148 node / 292 cạnh, đường xuyên 2 KB dài 2 cạnh
[OK] KG-3 context: 17 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00076. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

Ba ảnh Neo4j (chụp tự động toàn bộ cửa sổ Chrome đang mở `http://localhost:7474/browser/`, không chỉnh sửa; graph của lần chạy tạo ra `ket_qua_benchmark_kg.txt`): `report/img/kg_count.png` (Q-A, đếm node), `report/img/kg_cross_kb.png` (Q-B, đường người → vụ → tội → Điều luật), `report/img/kg_my_case.png` (Q-D với người tôi tự chọn: **Cái Quang Huy**). Ảnh chụp từ một cửa sổ Chrome mới với hồ sơ trống (chỉ một tab Neo4j), không phải cửa sổ trình duyệt thường dùng.

**Vấn đề gặp phải:** Docker Desktop chưa chạy khi bắt đầu nên không kết nối được Neo4j (`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`); đã mở Docker Desktop rồi `docker run` container `neo4j-drug-kg`. Máy dùng Python 3.12 thay vì 3.11 mà bài ghi; 48 test vẫn qua. Key API thuộc một cổng trung gian tương thích OpenAI nên phải đặt thêm `OPENAI_BASE_URL` trong `.env` (không commit).

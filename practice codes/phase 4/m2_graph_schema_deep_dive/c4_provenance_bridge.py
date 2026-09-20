# C4: the provenance bridge -- the graph stores IDs, the text stays in Qdrant, and they meet on chunk_id.
# Uses the REAL chunks on disk (data/chunks/, written by the P3 pipeline). Live Neo4j, self-cleaning. Qdrant is not needed.
import json                                                              # to read the chunk files
import re                                                                # to find chunks that name a product
import uuid                                                              # to make labels no other data can have
from collections import Counter                                          # counts how often each value appears
from pathlib import Path                                                 # object-style file paths

from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)
from src.vectorstore.store import point_id                               # chunk_id -> Qdrant point id (a deterministic UUIDv5)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
doc_l, chunk_l, prod_l = f"DemoDoc{suffix}", f"DemoChunk{suffix}", f"DemoProduct{suffix}"  # stand-ins for Document, Chunk and Product

chunks = []                                                              # every chunk the P3 pipeline produced
for f in sorted(Path("data/chunks").glob("*.json")):                     # one JSON file per document, plus report.json
    loaded = json.loads(f.read_text(encoding="utf-8"))                   # parse the file
    if isinstance(loaded, list):                                         # chunk files are lists; report.json is a dict, so skip it
        chunks.extend(loaded)                                            # add this document's chunks
print("1. chunks on disk               :", len(chunks))                  # 223 in this project

mentions = [c for c in chunks if re.search(r"\bCarllm\b", c["text"])]    # the chunks whose TEXT names the product Carllm
print("2. chunks that name Carllm      :", len(mentions), "in", dict(Counter(c["category"] for c in mentions)))  # spread over categories

rows = [                                                                 # what goes into the graph: IDs only, never the text
    {"doc_id": c["doc_id"], "source_path": c["source_path"], "chunk_id": c["chunk_id"], "chunk_index": c["chunk_index"]}  # four small fields
    for c in mentions                                                    # one row per chunk that names Carllm
]                                                                        # end of the list
own_doc = next(c["doc_id"] for c in chunks if c["source_path"] == "products/Carllm.md")  # the doc_id of the product's own page

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        session.run(                                                     # load Document, Chunk, Product and their relationships
            # the Cypher below is a multi-line f-string; the // comments inside it are Cypher comments, not Python
            f"""
            UNWIND $rows AS row                                          // run the block below once for each row
            MERGE (d:{doc_l} {{doc_id: row.doc_id}})                     // find or create the Document by its key
            SET d.source_path = row.source_path                          // remember where the file lives
            MERGE (c:{chunk_l} {{chunk_id: row.chunk_id}})               // find or create the Chunk by its key
            SET c.chunk_index = row.chunk_index                          // remember its position (still no text)
            MERGE (d)-[:HAS_CHUNK]->(c)                                  // the document owns this chunk
            MERGE (p:{prod_l} {{name: 'Carllm'}})                        // find or create the one Carllm product node
            MERGE (c)-[:MENTIONS]->(p)                                   // the chunk's text names the product
            """,                                                         # end of the Cypher text
            rows=rows,                                                   # the parameter: a list of small dicts
        ).consume()                                                      # wait for the write to finish
        session.run(                                                     # link the product to its own page
            f"MATCH (p:{prod_l} {{name: 'Carllm'}}), (d:{doc_l} {{doc_id: $id}}) MERGE (p)-[:DESCRIBED_IN]->(d)",  # entity -> its document
            id=own_doc,                                                  # the product page's doc_id
        ).consume()                                                      # wait for the write to finish

        keys = session.run(f"MATCH (c:{chunk_l}) RETURN keys(c) AS k LIMIT 1").single()["k"]  # which properties does a Chunk node hold?
        print("3. a Chunk node holds           :", keys, "(no text)")   # the text is not in the graph

        hit = session.run(                                               # a graph hit: everything that MENTIONS Carllm
            f"MATCH (:{prod_l} {{name: 'Carllm'}})<-[:MENTIONS]-(c) RETURN c.chunk_id AS id ORDER BY id"  # follow MENTIONS backwards
        ).value("id")                                                    # a plain list of chunk_ids
        print("4. graph hit (MENTIONS)         :", len(hit), "chunk_ids, e.g.", hit[:3])  # what a graph lookup returns

        own = session.run(                                               # the entity's OWN page, via DESCRIBED_IN then HAS_CHUNK
            f"MATCH (:{prod_l})-[:DESCRIBED_IN]->(:{doc_l})-[:HAS_CHUNK]->(c) RETURN c.chunk_id AS id ORDER BY c.chunk_index"  # two hops
        ).value("id")                                                    # the product page's chunk_ids
        print("5. its own page (DESCRIBED_IN)  :", own)                  # the page the dense search missed in the top 10

        print("6. the join key for Qdrant      :", {cid: point_id(cid) for cid in own})  # derive the Qdrant point ids; nothing stored
    finally:                                                             # always clean up, even after an error above
        removed = session.run(                                           # delete every node with one of the three demo labels
            f"MATCH (n) WHERE n:{doc_l} OR n:{chunk_l} OR n:{prod_l} DETACH DELETE n RETURN count(n) AS c"  # DETACH removes relationships too
        ).single()["c"]                                                  # how many nodes were deleted
        print("7. cleanup                      :", removed, "nodes deleted")  # nothing left behind

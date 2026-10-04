# Minimal demo of C2: exactly what Reranker sends, what comes back, and how a result index becomes a chunk.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects with named attributes

sys.path.insert(0, ".")  # lets Python import src.* and tests.* when run from the project root
from src.retrieval.reranker import Reranker  # the real production class
from tests.support import make_chunk  # builds a real DocumentChunk


class ShowingClient:
    def rerank(self, *, model, query, documents, top_n):
        print("  SENT  model:", model)  # shows the model name sent
        print("  SENT  query:", query)  # shows the question sent
        print("  SENT  documents:", documents)  # shows the exact texts sent, in order
        print("  SENT  top_n:", top_n)  # shows how many results were requested
        answer = [  # a canned reply, best first, naming documents by POSITION
            SimpleNamespace(index=2, relevance_score=0.91),  # the third document sent is the best
            SimpleNamespace(index=0, relevance_score=0.40),  # the first document sent is second
        ]
        print("  GOT   results:", [(r.index, r.relevance_score) for r in answer])  # shows the positions and scores returned
        return SimpleNamespace(results=answer[:top_n])  # answers with at most top_n results


pool = [  # three candidates, as the hybrid retriever would hand them over
    make_chunk("company/about.md", 0, "Founded in 2015."),  # input position 1
    make_chunk("products/Carllm.md", 0, "Carllm handles auto claims."),  # input position 2
    make_chunk("company/careers.md", 0, "We are hiring engineers."),  # input position 3
]
print("rerank_with_scores('What does Carllm do?', pool, k=2):")  # announces the call
for item in Reranker(ShowingClient()).rerank_with_scores("What does Carllm do?", pool, 2):
    print(f"  RESULT {item.chunk.chunk_id:<24} score={item.relevance_score}  input_rank={item.input_rank}")  # prints each returned chunk
print("index 2 in the reply became the chunk that was 3rd in the input; input_rank remembers that 3")  # explains the mapping

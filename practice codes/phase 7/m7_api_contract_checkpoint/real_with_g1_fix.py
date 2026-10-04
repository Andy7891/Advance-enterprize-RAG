"""The REAL app (real Qdrant, Neo4j, Ollama) with the candidate fix for gap G1 applied.

Used by real_stack_probes.py to compare an unmodified server with one that closes an abandoned
stream's generator chain the moment the response ends (docs/api-contract.md, G1). Nothing in
src/api/main.py is edited: the one change is made here, after import, exactly as mutant_stub.py's
"closes-abandoned-streams" does for the stub.

Run (from the repo root; PYTHONPATH separators are ";" on Windows, ":" elsewhere):
    PYTHONPATH="practice codes/phase 7/m7_api_contract_checkpoint;." \
        python -m uvicorn real_with_g1_fix:app --port 8001
"""

from starlette.background import BackgroundTask

import src.api.main as api


class ClosingStreamingResponse(api.StreamingResponse):
    """A StreamingResponse that closes its generator once the response is over, however it ended.

    Starlette runs a response's `background` task after the stream is cancelled by a client
    disconnect, and that cancellation waits for the in-flight next() to finish, so close() never
    races a running generator. Closing the outer generator closes every generator beneath it,
    which is what finally lets the model client release its connection to Ollama.
    """

    def __init__(self, content, *args, **kwargs):
        super().__init__(content, *args, background=BackgroundTask(content.close), **kwargs)


api.StreamingResponse = ClosingStreamingResponse
app = api.app

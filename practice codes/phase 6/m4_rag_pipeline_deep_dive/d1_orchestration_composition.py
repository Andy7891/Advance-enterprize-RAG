# Minimal demo of C2: orchestration -- one class that KNOWS about two other pieces and wires
# them together, so no OTHER code ever needs to know both exist at the same time.
class Fetcher:  # stands in for RetrievalPipeline: knows how to fetch raw material
    def fetch(self, topic: str) -> str:  # returns something relevant to the topic
        return f"facts about {topic}"  # a toy stand-in for real retrieved chunks


class Writer:  # stands in for LocalLLM: knows how to turn material into a finished answer
    def write(self, material: str) -> str:  # turns raw material into a finished answer
        return f"Report: {material}, summarized."  # a toy stand-in for a real generated answer


class ReportPipeline:  # the ORCHESTRATOR: the only place that knows BOTH Fetcher and Writer exist
    def __init__(self, fetcher: Fetcher, writer: Writer) -> None:  # takes both dependencies in
        self._fetcher = fetcher  # keeps the fetcher for later use
        self._writer = writer  # keeps the writer for later use

    def run(self, topic: str) -> str:  # the ONE public method a caller ever needs to call
        material = self._fetcher.fetch(topic)  # step 1: fetch, exactly like RAGPipeline._prepare
        return self._writer.write(material)  # step 2: write, using step 1's result as input


pipeline = ReportPipeline(Fetcher(), Writer())  # wires the two pieces together, exactly once
print(pipeline.run("insurance"))  # a caller asks for ONE thing, knowing nothing about the 2 steps

print("\nNotice: nothing outside ReportPipeline ever imports or calls Fetcher/Writer directly --")
print("that is the whole point of orchestration: RAGPipeline plays this exact role for a real")
print("Retriever and a real LLM, and no other module in this project needs to know both exist.")

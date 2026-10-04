# Minimal demo of C2: two completely different vendor libraries, each with their OWN
# calling convention, hidden behind ONE adapter class -- so calling code never changes
# when the backend underneath is swapped, exactly like LocalLLM hides ollama.Client.
class VendorAClient:  # imagine this is a real, installed third-party library
    def complete_text(self, text_in, *, randomness):  # its OWN, vendor-specific names
        return f"[VendorA saw {text_in!r} at randomness={randomness}] -> 42"  # its OWN reply shape


class VendorBClient:  # a COMPLETELY different library, for a different provider
    def run(self, payload):  # a different method name, a different argument shape entirely
        return {"output_text": f"[VendorB got {payload['input']!r}] -> 42"}  # a different reply shape


class LocalLLM:  # the ONE stable interface calling code is written against
    def __init__(self, vendor):
        self._vendor = vendor  # stores WHICHEVER vendor object was handed in
        self._kind = type(vendor).__name__  # remembers which one, only to explain this demo

    def generate(self, prompt, temperature):  # ONE method name, ONE argument shape, always
        if self._kind == "VendorAClient":  # translates to Vendor A's own convention
            return self._vendor.complete_text(prompt, randomness=temperature)  # A's real call
        if self._kind == "VendorBClient":  # translates to Vendor B's own, different convention
            return self._vendor.run({"input": prompt})["output_text"]  # B's real call
        raise TypeError(f"no adapter written for {self._kind}")  # a vendor nobody taught this class about


PROMPT = "What is 6 times 7?"  # the ONE question asked through both backends, unchanged

for vendor in (VendorAClient(), VendorBClient()):  # tries the SAME calling code against BOTH vendors
    llm = LocalLLM(vendor)  # wraps whichever vendor this loop iteration has
    print(f"backend = {type(vendor).__name__:<14} -> llm.generate(...) = {llm.generate(PROMPT, temperature=0.0)!r}")
    # the calling code above (llm.generate(PROMPT, temperature=0.0)) NEVER changed between the two runs

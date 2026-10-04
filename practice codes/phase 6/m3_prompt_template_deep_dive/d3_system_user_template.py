# Minimal demo of C3: Ollama's REAL, measured Mistral template (docker exec ... ollama show
# mistral --template), reproduced here as plain Python string substitution, so you can see
# EXACTLY what [INST]...[/INST] block the model actually receives, with or without `system`.
# The real Go template's relevant branch (verbatim, from `ollama show mistral --template`):
REAL_TEMPLATE_BRANCH = "[INST] {{ if .System }}{{ .System }}\n\n{{ end }}{{ .Prompt }}[/INST]"
print("the real template branch /api/generate uses:")
print(" ", REAL_TEMPLATE_BRANCH)


def apply_real_template(prompt, system=None):
    if system:  # mirrors the real template's "{{ if .System }}" check
        return f"[INST] {system}\n\n{prompt}[/INST]"  # system present: wrapped exactly like this
    return f"[INST] {prompt}[/INST]"  # system absent: just the prompt, still INST-wrapped


print("\n--- what LocalLLM sends TODAY (no `system` parameter exists yet) ---")
today = apply_real_template("What is the capital of France?")  # system=None, the only option now
print(repr(today))

print("\n--- what section 6's proposed LocalLLM change would send, once built ---")
system_text = "You are a helpful geography assistant. Answer in one word."
tomorrow = apply_real_template("What is the capital of France?", system=system_text)
print(repr(tomorrow))

print("\n--- the REJECTED alternative (D1b): manually concatenating system+user into ONE string ---")
concatenated_by_hand = apply_real_template(system_text + "\n\n" + "What is the capital of France?")
print(repr(concatenated_by_hand))
print("\nNotice: the 'tomorrow' and 'concatenated by hand' strings end up nearly identical --")
print("the REAL difference is that Ollama does that wrapping FOR you, correctly, once `system`")
print("is a real parameter, instead of you having to reproduce its exact format by hand forever.")

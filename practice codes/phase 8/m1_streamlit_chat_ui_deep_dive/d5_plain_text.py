# Demo of C5: text from a model is UNTRUSTED, and a markdown renderer acts on it. Shows what
# as_plain_text (src/ui/app.py) does, and why it escapes character by character.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d5_plain_text.py"
import re  # for the "pattern" version of escaping that we compare against

from src.ui.app import as_plain_text  # the real function (importing the page does NOT start it)

samples = [  # things a model, or a document, could really contain
    "Bizllm costs $10,000 per month or $12,000/month.",  # two prices: markdown would read $...$ as maths
    "![pixel](http://evil.example/p.png)",  # an image: the browser would fetch that URL
    "Click [here](http://evil.example) now",  # a link
    ":red[not red] **not bold** `not code`",  # colour directive, bold, code
    "line one\nline two",  # a newline: must stay a line break
]
print("1) what as_plain_text does to each sample")  # section title
for sample in samples:  # one block per sample
    print("   in :", ascii(sample))  # the original (ascii() makes escapes visible)
    print("   out:", ascii(as_plain_text(sample)))  # the escaped version Streamlit receives

print("\n2) the invariant: after removing escaped pairs, NO ascii punctuation is left")  # section title
punctuation = r"[!-/:-@\[-`{-~]"  # every ASCII punctuation character, as three-four ranges
everything = "".join(chr(code) for code in range(33, 127))  # every printable ASCII character once
leftover = re.sub(r"\\" + punctuation, "", as_plain_text(everything))  # drop each backslash+punct pair
print("   leftover punctuation:", re.findall(punctuation, leftover), "(empty list = nothing to interpret)")  # proof


def escape_only_image_start(text):  # a tempting, WRONG alternative: escape just the two-character "!["
    return text.replace("![", "\\![")  # works only when ! and [ are in the same piece


tokens = ["Look: !", "[pixel](http://evil.example/p.png)"]  # a model streams in tokens; "!" and "[" arrive apart
print("\n3) the same hostile image, streamed in two tokens ('!' then '[...')")  # section title
print("   escape-only-'![' :", ascii("".join(escape_only_image_start(t) for t in tokens)))  # the image survives
print("   as_plain_text    :", ascii("".join(as_plain_text(t) for t in tokens)))  # neutralised

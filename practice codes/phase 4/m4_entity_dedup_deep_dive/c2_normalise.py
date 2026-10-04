# C2: normalisation -- different-looking spellings become the same list of words. No database needed.
import re                                                                    # to show what a naive split would do
import unicodedata                                                           # the standard library's Unicode tools
from src.graph.dedup import _words                                           # the project's word splitter (private: imported to look inside)

quote = "Michael O" + chr(0x2019) + "Brien"                                  # a name written with a typographic apostrophe
for text in ["Alice  Smith", "ALICE SMITH", "alice_smith", "Alice-Smith", "Michael O'Brien", quote]:  # spacing, case, _, -, two apostrophes
    print(f"{text!r:<20} ->", _words(text))                                  # the words the matcher will compare

composed = "José"                                                            # e-acute as ONE character (what a person types)
decomposed = unicodedata.normalize("NFD", composed)                          # the same letter as 'e' + a combining accent (what some file systems store)
print("\nsame text?      ", composed == decomposed)                           # False: different characters underneath
print("lengths          ", len(composed), len(decomposed))                    # 4 vs 5: the accent is a separate character
print("with NFKC        ", _words(composed) == _words(decomposed))           # True: normalising first puts them back together
print("without NFKC     ", re.findall(r"[^\W_]+", decomposed.casefold()))    # the accent is dropped, leaving 'jose' (not 'josé')

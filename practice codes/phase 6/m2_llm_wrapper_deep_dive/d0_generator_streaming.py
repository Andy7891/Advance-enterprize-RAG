# Minimal demo of C1: a generator function's BODY does not run until you ask it for the
# next value -- proven here by comparing WHEN print statements fire for a "buffered"
# (returns a full list) version versus a "streamed" (yields one piece at a time) version.
import time  # lets this demo simulate a slow model "thinking" between words


def buffered_answer(words):
    print("  buffered: starting work (nothing has been produced yet)...")  # runs immediately
    pieces = []  # will hold every word before handing ANY of it back
    for word in words:  # simulates the model producing one word at a time
        time.sleep(0.3)  # simulates the real thinking time each word costs
        print(f"  buffered: produced {word!r} (but the CALLER still has nothing)")  # proves the caller is still waiting
        pieces.append(word)  # keeps it, instead of handing it over right away
    return pieces  # only NOW, after everything is done, does the caller get anything


def streamed_answer(words):
    print("  streamed: function CALLED, but not one line inside it has run yet")  # proves nothing ran yet
    for word in words:  # the SAME loop as above
        time.sleep(0.3)  # the SAME simulated thinking time
        print(f"  streamed: about to yield {word!r}")  # runs just before handing this one word over
        yield word  # hands this ONE word to the caller immediately -- this IS LocalLLM.stream()


print("--- calling buffered_answer(...) ---")  # heading for the buffered version
result = buffered_answer(["The", "answer", "is", "4"])  # this line blocks until EVERYTHING is ready
print("  caller received:", result)  # only prints after the full 1.2s wait

print("\n--- calling streamed_answer(...) ---")  # heading for the streamed version
generator = streamed_answer(["The", "answer", "is", "4"])  # THIS LINE RUNS INSTANTLY
print("  streamed_answer(...) returned already -- did any 'about to yield' line print above? No.")
for word in generator:  # each loop step is what actually resumes the function's body
    print("  caller received:", repr(word), "<- the moment this line runs, that word is already usable")

# Visualization for C2: the same steps as src/ui/client.py's iter_sse_data, but printing its
# internal state after every network chunk, so you can SEE the buffer, the held CR and the events.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d2b_trace_reader.py"
import codecs  # incremental UTF-8 decoding (survives a multi-byte character cut in two)
import itertools  # chain() lets us append an "end of stream" marker to the chunks
import re  # the line-ending pattern

LINE_END = re.compile(r"\r\n|\r|\n")  # the three SSE line terminators, CRLF first so it counts as one

# The network delivers the body in awkward pieces: a CRLF split in two, and a euro sign (3 bytes) cut.
euro = "€".encode("utf-8")  # b'\xe2\x82\xac'
chunks = [  # what arrives, in order
    b"data: price 10" + euro[:1],  # the first byte of the euro sign only
    euro[1:] + b"\r",  # the rest of the euro sign, then a CR that might be half of a CRLF
    b"\n\r\n",  # the LF that completes the CRLF, then CRLF again = blank line = event ends
    b"data: a\r\rdata: b\r",  # lone CRs as line ends; the stream will END on a CR
]

decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")  # the same decoder the real reader uses
buffer = ""  # text received but not yet cut into lines
data_lines = []  # data: lines of the event being built
for number, chunk in enumerate(itertools.chain(chunks, [None])):  # None = end of stream
    at_end = chunk is None  # is this the final pass?
    text = decoder.decode(b"" if chunk is None else chunk, final=at_end)  # bytes -> text, never half a character
    buffer += text  # add to what was left over
    held = "\r" if buffer.endswith("\r") and not at_end else ""  # hold back a trailing CR unless the stream ended
    lines = LINE_END.split(buffer[: len(buffer) - len(held)])  # cut into lines
    buffer = lines.pop() + held  # last piece is an unfinished line; keep it (plus any held CR)
    events = []  # events completed in this pass
    for line in lines:  # each complete line
        if line == "":  # blank line = the event is complete
            if data_lines:  # (only if it has data)
                events.append("\n".join(data_lines))  # hand it over
            data_lines = []  # start a new event
        elif not line.startswith(":"):  # not a comment
            name, _, value = line.partition(":")  # split "field: value"
            if name == "data":  # only data fields matter here
                data_lines.append(value.removeprefix(" "))  # drop exactly one leading space
    label = "END " if at_end else f"#{number}  "  # a short label for the chunk
    print(f"{label} bytes={ascii(chunk)!s:34} decoded={ascii(text)!s:18} held={ascii(held)!s:6}")  # input side
    print(f"      lines={[ascii(x) for x in lines]}  buffer_left={ascii(buffer)}  events={[ascii(e) for e in events]}")  # output side

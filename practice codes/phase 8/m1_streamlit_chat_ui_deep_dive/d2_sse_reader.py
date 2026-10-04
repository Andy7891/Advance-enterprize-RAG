# Demo of C2: reading a Server-Sent Events stream correctly. Compares the tempting shortcut
# (response.iter_lines()) with the project's own reader (src/ui/client.py: iter_sse_data).
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d2_sse_reader.py"
import httpx  # the HTTP library the page uses; here only to build a fake response from bytes

from src.ui.client import iter_sse_data  # the real reader being studied

# What the API puts on the wire for three answer fragments and the end marker. Each event is
# "data: <text>" followed by a blank line. The second fragment contains U+2028 (LINE SEPARATOR),
# which Python's splitlines() treats as a line break but the SSE rules do NOT.
body = "data: Hello \n\ndata: sep: after\n\ndata: [DONE]\n\n".encode("utf-8")  # text -> bytes, like a socket delivers

print("1) the shortcut: response.iter_lines()")  # section title
response = httpx.Response(200, content=body)  # a fake response whose body is those bytes
lines = list(response.iter_lines())  # httpx cuts the body into lines the splitlines() way
print("   lines     :", [ascii(line) for line in lines])  # ascii() shows invisible characters as escapes
naive = [line[5:].removeprefix(" ") for line in lines if line.startswith("data:")]  # keep only data lines
print("   naive text:", [ascii(text) for text in naive])  # the text after U+2028 has vanished

print("\n2) the project's reader: iter_sse_data")  # section title
print("   events    :", [ascii(event) for event in iter_sse_data([body])])  # whole body in one chunk

print("\n3) same bytes, delivered ONE BYTE AT A TIME (network chunks can cut anywhere)")  # section title
one_byte_chunks = [body[i : i + 1] for i in range(len(body))]  # a list of 1-byte pieces
print("   events    :", [ascii(event) for event in iter_sse_data(one_byte_chunks)])  # must be identical to 2)

print("\n4) a character that needs 3 bytes, cut in the middle by the network")  # section title
euro = "data: price 10€\n\n".encode("utf-8")  # the euro sign is 3 bytes in UTF-8
cut = euro.index("€".encode("utf-8")) + 1  # cut after the FIRST byte of the euro sign
print("   events    :", [ascii(event) for event in iter_sse_data([euro[:cut], euro[cut:]])])  # reassembled correctly

print("\n5) a lone CR as the very last byte of the stream")  # section title
print("   events    :", [ascii(event) for event in iter_sse_data([b"data: a\r\rdata: b\r\r"])])  # both events arrive

print("\n6) a stream that stops in the middle of an event")  # section title
print("   events    :", [ascii(event) for event in iter_sse_data([b"data: complete\n\ndata: unfinis"])])  # only the finished one

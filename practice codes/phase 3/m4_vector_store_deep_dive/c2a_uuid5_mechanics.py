# How a UUIDv5 is made from a name, step by step, and why it is deterministic.
import hashlib
import uuid

from src.vectorstore.store import POINT_ID_NAMESPACE, point_id

name = "company/about.md_3"

# Step 1: SHA-1 over (namespace bytes + name bytes)
digest = hashlib.sha1(POINT_ID_NAMESPACE.bytes + name.encode("utf-8")).digest()
print("1. sha1(namespace + name) =", digest.hex())

# Step 2: keep the first 16 bytes, then overwrite the version and variant bits
b = bytearray(digest[:16])
b[6] = (b[6] & 0x0F) | 0x50      # high nibble of byte 6 = 5  -> "version 5"
b[8] = (b[8] & 0x3F) | 0x80      # top two bits of byte 8 = 10 -> RFC variant
by_hand = uuid.UUID(bytes=bytes(b))
print("2. by hand                =", by_hand)
print("3. uuid.uuid5             =", uuid.uuid5(POINT_ID_NAMESPACE, name))
print("4. store.point_id         =", point_id(name))
print("   version field          =", by_hand.version, "(the '5' at the start of the third group)")

# Same name -> same ID; one character different -> unrelated ID; different namespace -> different ID
print("5. same name again        =", point_id(name) == point_id("company/about.md_3"))
print("6. '..._4' instead        =", point_id("company/about.md_4"))
print("7. other namespace        =", uuid.uuid5(uuid.NAMESPACE_URL, name))
print("8. uuid4 twice            =", uuid.uuid4(), uuid.uuid4(), "<- random every call")

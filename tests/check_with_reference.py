"""Check an article made by the web client with the reference implementation.

    node --experimental-default-type=module tests/proto.test.mjs ... | python tests/check_with_reference.py
"""
import base64
import sys

from unseenet.core.article import Article, validate_structure

raw = base64.b64decode(sys.stdin.read().strip().splitlines()[-1])
art = Article.from_bytes(raw)
validate_structure(art, raw_size=len(raw))
print("reference accepts it:", art.message_id, "postage bits:", art.proven_pow())
assert art.proven_pow() >= 12

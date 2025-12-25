"""sroberta 컬렉션 스키마 확인"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from pymilvus import connections, Collection, utility

HOST = os.getenv("MILVUS_HOST", "localhost")
PORT = os.getenv("MILVUS_PORT", "19530")

connections.connect("default", host=HOST, port=int(PORT))
print(f"Connected to {HOST}:{PORT}")

collections = utility.list_collections()
print(f"\n컬렉션 목록: {collections}")

for coll_name in collections:
    col = Collection(coll_name)
    print(f"\n=== {coll_name} ===")
    print(f"엔티티 수: {col.num_entities}")
    for field in col.schema.fields:
        info = f"  - {field.name}: {field.dtype.name}"
        if hasattr(field, 'dim') and field.dim:
            info += f" (dim={field.dim})"
        print(info)

connections.disconnect("default")

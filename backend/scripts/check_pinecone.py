"""Validate the existing Pinecone index; never creates or deletes an index."""

import argparse
import asyncio
import os
from uuid import uuid4

from pinecone import PineconeAsyncio


async def main(smoke_test: bool) -> None:
    api_key = os.environ.get("PINECONE_API_KEY")
    host = os.environ.get("PINECONE_HOST")
    index_name = os.environ.get("PINECONE_INDEX_NAME", "stratix-knowledge")
    dimensions = int(os.environ.get("PINECONE_DIMENSIONS", "768"))
    metric = os.environ.get("PINECONE_METRIC", "cosine")
    if not api_key:
        raise SystemExit("PINECONE_API_KEY is required.")
    if not host:
        raise SystemExit("PINECONE_HOST is required for data operations.")

    client = PineconeAsyncio(api_key=api_key)
    index = None
    try:
        description = await client.describe_index(index_name)
        ready = getattr(getattr(description, "status", None), "ready", False)
        actual_dimension = getattr(description, "dimension", None)
        actual_metric = getattr(description, "metric", None)
        print(f"index={index_name}")
        print(f"ready={ready}")
        print(f"dimension={actual_dimension}")
        print(f"metric={actual_metric}")
        if actual_dimension != dimensions:
            raise SystemExit("Index dimension does not match PINECONE_DIMENSIONS.")
        if actual_metric != metric:
            raise SystemExit("Index metric does not match PINECONE_METRIC.")
        if not ready:
            raise SystemExit("Index is not ready.")
        if not smoke_test:
            return

        namespace = f"stratix-smoke-{uuid4()}"
        vector_id = f"smoke-{uuid4()}"
        index = client.IndexAsyncio(host=host)
        vector = [0.0] * dimensions
        vector[0] = 1.0
        await index.upsert(
            namespace=namespace,
            vectors=[
                {
                    "id": vector_id,
                    "values": vector,
                    "metadata": {"purpose": "explicit-smoke-test"},
                }
            ],
        )
        result = await index.query(
            namespace=namespace,
            vector=vector,
            top_k=1,
            include_values=False,
            include_metadata=True,
        )
        matches = getattr(result, "matches", [])
        if not matches or getattr(matches[0], "id", None) != vector_id:
            raise SystemExit("Smoke-test query did not return its temporary vector.")
        await index.delete(namespace=namespace, ids=[vector_id])
        print("smoke_test=passed")
        print("temporary_vectors_deleted=true")
    finally:
        if index is not None:
            await index.close()
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Upsert, query, and delete one vector in a temporary namespace.",
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.smoke_test))

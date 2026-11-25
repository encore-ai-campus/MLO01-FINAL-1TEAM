"""Check the DB contract using the candidate image and the existing app credentials."""
import asyncio
import os

import asyncpg


async def check():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=10)
    try:
        async with conn.transaction(readonly=True):
            identity = await conn.fetchrow("SELECT current_user, current_database()")
            if tuple(identity) != ("rag_app", "rag_dev"):
                raise RuntimeError("Unexpected DB identity")
            # Parse, plan and check access to the current RAG columns and search functions.
            await conn.fetch("""
                SELECT d.current_version_id, d.allowed_depts, d.ontology_graph_meta,
                       v.raw_markdown, v.version_status, c.embedding,
                       tsv_to_text(c.tsv) <@> to_bm25query('check','idx_chunks_bm25')
                FROM documents d JOIN document_versions v ON v.doc_id=d.doc_id
                JOIN chunks c ON c.version_id=v.version_id
                LIMIT 0
            """)
            await conn.fetch("SELECT pack_labels, ojt_questions FROM departments LIMIT 0")
            await conn.fetch("SELECT dict_version, synonyms FROM ontology_concepts LIMIT 0")
            indexes = await conn.fetchval("""
                SELECT count(*) FROM pg_index
                WHERE indexrelid IN (to_regclass('public.idx_chunks_bm25'),
                                     to_regclass('public.idx_chunks_embedding_hnsw')) AND indisvalid
            """)
            if indexes != 2:
                raise RuntimeError("Missing or invalid RAG indexes")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(check())

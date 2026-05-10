import os
import shutil
import tempfile
from pathlib import Path

from git import Repo
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INDEX_NAME = "github-explainer"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"   # free, CPU-friendly, 384-dim
EMBED_DIM   = 384

SUPPORTED_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cpp", ".c", ".h", ".cs",
    ".go", ".rs", ".rb", ".php", ".swift",
    ".md", ".txt", ".json", ".yaml", ".yml",
    ".html", ".css", ".sh", ".env.example",
    ".toml", ".ini", ".cfg",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv",
    "venv", "dist", "build", ".next", ".cache",
}

SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Gemfile.lock", "composer.lock"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_namespace(github_url: str) -> str:
    """Convert a GitHub URL to a safe Pinecone namespace string."""
    url = github_url.rstrip("/")
    # e.g. https://github.com/tiangolo/fastapi  →  tiangolo-fastapi
    parts = url.replace("https://github.com/", "").replace("http://github.com/", "")
    namespace = parts.replace("/", "-").replace(".", "-")[:60]
    return namespace.lower()


def get_embedder() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def ensure_pinecone_index() -> None:
    """Create the Pinecone index if it doesn't already exist."""
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    existing = [idx.name for idx in pc.list_indexes()]
    if INDEX_NAME not in existing:
        pc.create_index(
            name=INDEX_NAME,
            dimension=EMBED_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )


def clear_namespace(namespace: str) -> None:
    """Wipe all vectors in a namespace (called when re-indexing a repo)."""
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(INDEX_NAME)
    try:
        index.delete(delete_all=True, namespace=namespace)
    except Exception:
        pass  # namespace may not exist yet — that's fine


# ---------------------------------------------------------------------------
# Clone + read
# ---------------------------------------------------------------------------

def generate_tree(tmp_path: str) -> str:
    lines = []
    for root, dirs, files in os.walk(tmp_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        rel_root = os.path.relpath(root, tmp_path)
        if rel_root == ".":
            indent_level = 0
        else:
            indent_level = len(Path(rel_root).parts)
        
        indent = "  " * indent_level
        if rel_root != ".":
            lines.append(f"{indent}{os.path.basename(root)}/")
            indent += "  "
        for f in sorted(files):
            lines.append(f"{indent}{f}")
    return "\n".join(lines)


def clone_and_read(github_url: str) -> list[dict]:
    """
    Shallow-clone the repo into a temp dir, read every supported file,
    return a list of {content, source} dicts.
    """
    tmp = tempfile.mkdtemp()
    try:
        Repo.clone_from(github_url, tmp, depth=1)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"Could not clone repo: {e}")

    docs = []
    
    # Generate project tree
    tree_str = generate_tree(tmp)
    docs.append({
        "content": f"Repository Structure:\n\n{tree_str}",
        "source": "PROJECT_STRUCTURE"
    })

    for path in Path(tmp).rglob("*"):
        # Skip hidden / dependency directories
        if any(skip in path.parts for skip in SKIP_DIRS) or any(p.startswith(".") and p != "." for p in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix not in SUPPORTED_EXT or not path.is_file():
            continue
            
        # Skip files > 100KB
        try:
            if path.stat().st_size > 102400:
                continue
        except Exception:
            pass

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            if len(content.strip()) > 60:   # ignore near-empty files
                docs.append({
                    "content": content,
                    "source": str(path.relative_to(tmp)),
                })
        except Exception:
            pass

    shutil.rmtree(tmp, ignore_errors=True)
    return docs


# ---------------------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------------------

def chunk_docs(docs: list[dict], github_url: str = "") -> tuple[list[str], list[dict]]:
    """Split each document into overlapping chunks. Returns texts + metadatas."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=120,
        separators=["\nclass ", "\ndef ", "\n\n", "\n", " ", ""],
    )
    texts, metadatas = [], []
    for doc in docs:
        splits = splitter.split_text(doc["content"])
        for i, chunk in enumerate(splits):
            texts.append(chunk)
            metadatas.append({
                "source": doc["source"],
                "chunk_index": i,
                "github_url": github_url,
            })
    return texts, metadatas


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest(github_url: str) -> str:
    """
    Full ingestion pipeline:
      clone → read → chunk → embed → upsert to Pinecone (namespaced).

    Returns the namespace string so the caller can store it.
    """
    namespace = make_namespace(github_url)

    # 1. Make sure the index exists
    ensure_pinecone_index()

    # 2. Wipe old vectors for this repo (so re-indexing stays fresh)
    clear_namespace(namespace)

    # 3. Clone and read files
    docs = clone_and_read(github_url)
    if not docs:
        raise ValueError("No readable files found in the repository.")

    # 4. Chunk
    texts, metadatas = chunk_docs(docs, github_url=github_url)

    # 5. Embed + upsert
    embedder = get_embedder()
    PineconeVectorStore.from_texts(
        texts=texts,
        embedding=embedder,
        metadatas=metadatas,
        index_name=INDEX_NAME,
        namespace=namespace,
    )

    return namespace   # e.g. "tiangolo-fastapi"


# ---------------------------------------------------------------------------
# Persistence helpers  (survive page refreshes)
# ---------------------------------------------------------------------------

def namespace_has_vectors(namespace: str) -> bool:
    """Return True if *namespace* already contains vectors in Pinecone."""
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    try:
        index = pc.Index(INDEX_NAME)
        stats = index.describe_index_stats()
        ns_map = stats.namespaces or {}
        return namespace in ns_map and ns_map[namespace].vector_count > 0
    except Exception:
        return False


def get_all_indexed_repos() -> dict[str, str]:
    """
    Scan every Pinecone namespace and return {namespace: github_url}.

    Reads ``github_url`` from stored vector metadata when available;
    otherwise reconstructs it from the namespace string (best-effort).
    """
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    try:
        index = pc.Index(INDEX_NAME)
        stats = index.describe_index_stats()
        ns_map = stats.namespaces or {}
    except Exception:
        return {}

    repos: dict[str, str] = {}
    for ns, info in ns_map.items():
        if info.vector_count == 0:
            continue

        # Try to read the github_url we stored in vector metadata
        try:
            results = index.query(
                vector=[0.0] * EMBED_DIM,
                top_k=1,
                namespace=ns,
                include_metadata=True,
            )
            if results.matches:
                url = (results.matches[0].metadata or {}).get("github_url", "")
                if url:
                    repos[ns] = url
                    continue
        except Exception:
            pass

        # Fallback: reconstruct URL from namespace  (owner-repo -> owner/repo)
        repos[ns] = f"https://github.com/{ns.replace('-', '/', 1)}"

    return repos